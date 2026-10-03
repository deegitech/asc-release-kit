"""``release``: prepare an App Store version and, if asked, submit it for review.

Order of work:

1. read everything and refuse early, before any write: build not VALID, another
   version in review, a What's New locale missing on the version, and with
   ``--submit`` also What's New left empty for some locale or another review
   submission still open;
2. create the version (or reuse/rename an editable one) with the build;
3. attach the build, set What's New per locale, copy App Review details and the
   Game Center link from the live version when the new version lacks them;
4. read everything back and verify;
5. with ``--submit``: find or create the draft review submission, add the version,
   **re-read the submission and assert the version is really in it**, then submit
   and read the submission back.

Step 5's assertion exists because App Store Connect once accepted a submission
whose app-version item had failed to attach (the POST returned 500), so the
"release" went to review without the app. See docs/observed-behaviour.md.
"""

from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

from .api import AscClient, rel
from .context import Context
from .errors import ApiError, Blocked, CheckFailed, UsageError
from .redact import CONTACT_FIELDS
from .states import (
    DRAFT_SUBMISSION,
    EDITABLE_VERSION,
    GONE_ITEM,
    IN_FLIGHT_VERSION,
    LIVE_VERSION,
    OPEN_SUBMISSION,
    SUBMITTED,
    attrs,
    find_builds,
    find_version,
    list_versions,
    live_version,
    version_state,
)
from .storefronts import canonical_locale

RELEASE_TYPES = ("AFTER_APPROVAL", "MANUAL", "SCHEDULED")
GAME_CENTER_MODES = ("auto", "require", "skip")
WHATS_NEW_LIMIT = 4000
REVIEW_NOTES_LIMIT = 4000
REVIEW_FIELDS = tuple(sorted(CONTACT_FIELDS)) + ("demoAccountRequired", "notes")
REQUIRED_CONTACT = ("contactFirstName", "contactLastName", "contactPhone", "contactEmail")
BUILD_POLL_SECONDS = 30.0
SUBMISSION_ITEM_INCLUDES = (
    "appStoreVersion,appEvent,inAppPurchaseVersion,subscriptionVersion,"
    "gameCenterLeaderboardVersion,gameCenterAchievementVersion,appCustomProductPageVersion"
)

# Indirection so tests can skip real waiting.
_sleep: Callable[[float], None] = time.sleep
_monotonic: Callable[[], float] = time.monotonic


#: Key in a What's New file whose text goes to every locale the file doesn't list.
WHATS_NEW_DEFAULT_KEY = "*"


@dataclass
class ReleaseOptions:
    app_id: str
    platform: str
    version: str
    build: str
    whats_new: dict[str, str] | None = None
    #: What's New for every locale of the version that ``whats_new`` doesn't list
    #: (``--whats-new-text`` or the file's ``"*"`` entry)
    whats_new_default: str | None = None
    review_notes: str | None = None
    release_type: str | None = None
    earliest_release_date: str | None = None
    game_center: str = "auto"
    copy_review_details: bool = True
    reuse_editable: bool = False
    wait_for_build_minutes: float = 0.0
    submit: bool = False


@dataclass
class Step:
    text: str
    run: Callable[[], Any]


@dataclass
class State:
    build: dict[str, Any]
    versions: list[dict[str, Any]]
    live: dict[str, Any] | None
    target: dict[str, Any] | None
    live_review: dict[str, Any] | None = None
    live_game_center: dict[str, Any] | None = None


# ------------------------------------------------------------------------------ inputs


def _normalize(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n").strip()


def check_whats_new_text(text: Any, label: str) -> str:
    """Normalize one What's New text and enforce Apple's limit; ``label`` names it in errors."""
    if not isinstance(text, str) or not _normalize(text):
        raise UsageError(f"What's New [{label}] is empty.")
    clean = _normalize(text)
    if len(clean) > WHATS_NEW_LIMIT:
        raise UsageError(f"What's New [{label}] has {len(clean)} characters; the limit is {WHATS_NEW_LIMIT}.")
    return clean


def load_whats_new(path: str) -> dict[str, str]:
    """Read ``{"en-US": "text", ...}`` or an ASO file (its ``whatsNew`` fields).

    A ``"*"`` entry is kept under that key: its text is meant for every locale the
    file doesn't list (see ``ReleaseOptions.whats_new_default``).
    """
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        raise UsageError(f"What's New file not found: {path}") from None
    except json.JSONDecodeError as exc:
        raise UsageError(f"{os.path.basename(path)}: invalid JSON at line {exc.lineno}: {exc.msg}") from None
    if isinstance(data, dict) and isinstance(data.get("localizations"), dict):
        data = {
            loc: body.get("whatsNew")
            for loc, body in data["localizations"].items()
            if isinstance(body, dict) and isinstance(body.get("whatsNew"), str)
        }
    if not isinstance(data, dict) or not data:
        raise UsageError('The What\'s New file must map locales to text, e.g. {"en-US": "Bug fixes."}.')
    out: dict[str, str] = {}
    for locale, text in data.items():
        if str(locale).startswith("_"):
            continue
        if locale == WHATS_NEW_DEFAULT_KEY:
            out[WHATS_NEW_DEFAULT_KEY] = check_whats_new_text(text, "* (all other locales)")
            continue
        canonical = canonical_locale(str(locale))
        if canonical is None:
            raise UsageError(f"What's New: unknown locale '{locale}' (use App Store Connect codes such as en-US).")
        out[canonical] = check_whats_new_text(text, canonical)
    return out


def load_review_notes(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as handle:
            text = _normalize(handle.read())
    except OSError as exc:
        raise UsageError(f"Review notes file can't be read: {exc.strerror}") from None
    if not text:
        raise UsageError("The review notes file is empty.")
    if len(text) > REVIEW_NOTES_LIMIT:
        raise UsageError(f"Review notes have {len(text)} characters; the limit is {REVIEW_NOTES_LIMIT}.")
    return text


def parse_instant(text: str | None) -> datetime | None:
    """Parse an ISO 8601 date-time with a time zone (``Z``, ``+00:00`` or ``+0000``); ``None`` if it isn't one."""
    if not text:
        return None
    value = str(text).strip()
    if value[-1:] in ("Z", "z"):
        value = value[:-1] + "+00:00"
    value = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", value)
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return None
    return moment if moment.tzinfo is not None else None


def same_instant(actual: Any, wanted: str | None) -> bool:
    """True when two date-time strings name the same moment (App Store Connect may reformat them)."""
    if actual in (None, "") or not wanted:
        return actual in (None, "") and not wanted
    a, b = parse_instant(str(actual)), parse_instant(wanted)
    if a is not None and b is not None:
        return a == b
    return str(actual).strip() == wanted.strip()


def validate_options(opts: ReleaseOptions) -> None:
    if opts.release_type and opts.release_type not in RELEASE_TYPES:
        raise UsageError(f"--release-type must be one of {', '.join(RELEASE_TYPES)}.")
    if opts.release_type == "SCHEDULED" and not opts.earliest_release_date:
        raise UsageError(
            "--release-type SCHEDULED needs --earliest-release-date (ISO 8601, e.g. 2030-11-01T09:00:00Z)."
        )
    if opts.earliest_release_date and opts.release_type != "SCHEDULED":
        raise UsageError("--earliest-release-date only applies with --release-type SCHEDULED.")
    if opts.earliest_release_date and parse_instant(opts.earliest_release_date) is None:
        raise UsageError(
            "--earliest-release-date must be an ISO 8601 date and time with a time zone, e.g. 2030-11-01T09:00:00Z."
        )
    if opts.game_center not in GAME_CENTER_MODES:
        raise UsageError(f"--game-center must be one of {', '.join(GAME_CENTER_MODES)}.")
    if not opts.version.strip() or not opts.build.strip():
        raise UsageError("--version and --build are required.")


# ------------------------------------------------------------------------------ reading


def _wait_for_valid_build(ctx: Context, client: AscClient, opts: ReleaseOptions) -> dict[str, Any]:
    deadline = _monotonic() + max(0.0, opts.wait_for_build_minutes) * 60
    announced = False
    while True:
        builds = find_builds(client, opts.app_id, opts.version, opts.build, opts.platform)
        for build in builds:
            a = attrs(build)
            if a.get("processingState") == "VALID" and not a.get("expired"):
                return build
        states = sorted({str(attrs(b).get("processingState")) for b in builds})
        if any(attrs(b).get("expired") for b in builds):
            raise Blocked(f"Build {opts.version} ({opts.build}) has expired; upload a new build.")
        if any(s in ("INVALID", "FAILED") for s in states):
            raise Blocked(
                f"Build {opts.version} ({opts.build}) is {', '.join(states)}; Apple's processing e-mail says why."
            )
        remaining = deadline - _monotonic()
        if remaining <= 0:
            if states:
                raise Blocked(
                    f"Build {opts.version} ({opts.build}) is not VALID ({', '.join(states)}). Check the version/build "
                    "numbers, or pass --wait-for-build MINUTES to wait for processing."
                )
            raise Blocked(
                f"Build {opts.version} ({opts.build}) is not VALID (not found). Check the version/build numbers, "
                "or ASC_APP_ID / --app points to another app (`asc-release-kit apps list`), or pass --wait-for-build "
                "MINUTES to wait for processing.",
                fix="compare the numbers with the app's TestFlight tab, and ASC_APP_ID with the first column of "
                "`asc-release-kit apps list`",
            )
        if not announced:
            ctx.reporter.info(
                f"build {opts.build}: {', '.join(states) if states else 'not visible yet'}; polling every "
                f"{int(BUILD_POLL_SECONDS)} s for up to {opts.wait_for_build_minutes:g} min"
            )
            announced = True
        _sleep(min(BUILD_POLL_SECONDS, remaining))


def _read_state(ctx: Context, client: AscClient, opts: ReleaseOptions) -> State:
    build = _wait_for_valid_build(ctx, client, opts)
    versions = list_versions(client, opts.app_id, opts.platform)
    live = live_version(versions)
    target = find_version(versions, opts.version)
    state = State(build=build, versions=versions, live=live, target=target)
    if live is not None:
        state.live_review = client.get_one(f"/v1/appStoreVersions/{live['id']}/appStoreReviewDetail")
        if opts.game_center == "auto":
            state.live_game_center = client.get_one(f"/v1/appStoreVersions/{live['id']}/gameCenterAppVersion")
    return state


def _uses_game_center(opts: ReleaseOptions, state: State) -> bool:
    if opts.game_center == "skip":
        return False
    if opts.game_center == "require":
        return True
    gc = state.live_game_center
    return gc is not None and attrs(gc).get("enabled") is not False


def _version_label(version: dict[str, Any]) -> str:
    return f"{attrs(version).get('versionString')} ({version_state(version)})"


# ------------------------------------------------------------------------------ the command


def run_release(ctx: Context, opts: ReleaseOptions) -> dict[str, Any]:
    validate_options(opts)
    r = ctx.reporter
    client = ctx.client()
    mode = "APPLY" if ctx.apply else "PLAN (nothing is written; add --apply)"
    r.title(f"release {opts.version} ({opts.build}) · app {opts.app_id} · {opts.platform} · {mode}")

    state = _read_state(ctx, client, opts)
    build_id = str(state.build["id"])
    build_attrs = attrs(state.build)
    r.ok(
        f"build {opts.build} is VALID"
        + (f" (uploaded {build_attrs['uploadedDate'][:10]})" if build_attrs.get("uploadedDate") else "")
    )
    if build_attrs.get("usesNonExemptEncryption") is None:
        msg = (
            "export compliance isn't answered for this build; set ITSAppUsesNonExemptEncryption in Info.plist "
            "or answer it in App Store Connect"
        )
        if opts.submit and ctx.apply:
            raise Blocked("Can't submit: " + msg + ".")
        r.warn(msg + (" (--submit will refuse until then)" if opts.submit else ""))
    if state.live is not None:
        r.ok(f"live version: {_version_label(state.live)}")
    else:
        r.info("no live version yet (first release): What's New is skipped and nothing can be copied")

    result: dict[str, Any] = {
        "app": opts.app_id,
        "platform": opts.platform,
        "version": opts.version,
        "build": opts.build,
        "versionId": None,
        "verified": None,
        "submitted": False,
    }

    target = state.target
    other: dict[str, Any] | None = None
    if target is not None:
        current = version_state(target)
        if current in IN_FLIGHT_VERSION or current in LIVE_VERSION:
            r.ok(f"version {opts.version} is already {current}; nothing to do")
            result.update(
                versionId=target["id"], state=current, submitted=current in ("WAITING_FOR_REVIEW", "IN_REVIEW")
            )
            return result
        if current not in EDITABLE_VERSION:
            raise Blocked(f"Version {opts.version} is {current} and can't be edited.")
    else:
        busy = [v for v in state.versions if version_state(v) in IN_FLIGHT_VERSION]
        if busy:
            raise Blocked(
                f"Version {_version_label(busy[0])} is still with App Review or waiting for release; "
                "App Store Connect allows one version in flight per platform."
            )
        editable = [v for v in state.versions if version_state(v) in EDITABLE_VERSION]
        if editable:
            other = editable[0]
            if not opts.reuse_editable:
                raise Blocked(
                    f"An editable version {_version_label(other)} already exists. Pass --reuse-editable to rename "
                    f"it to {opts.version}, or remove it in App Store Connect."
                )

    # Everything below may write, so finish every check first.
    opts = _resolve_whats_new(ctx, client, opts, state, target or other)
    if opts.submit:
        _check_open_submissions(ctx, client, opts)

    # -- phase 1: get a version record ---------------------------------------------------------
    if target is None:
        if other is not None:
            r.change(f"rename editable version {attrs(other).get('versionString')} to {opts.version}")
            if ctx.apply:
                client.update("appStoreVersions", other["id"], {"versionString": opts.version})
            target = other
        else:
            create_attrs: dict[str, Any] = {"platform": opts.platform, "versionString": opts.version}
            if opts.release_type:
                create_attrs["releaseType"] = opts.release_type
            if opts.earliest_release_date:
                create_attrs["earliestReleaseDate"] = opts.earliest_release_date
            r.change(
                f"create version {opts.version} with build {opts.build}"
                + (f", release type {opts.release_type}" if opts.release_type else " (Apple's default release type)")
            )
            if not ctx.apply:
                _plan_for_new_version(ctx, opts, state)
                return result
            target = client.create(
                "appStoreVersions",
                create_attrs,
                {"app": rel("apps", opts.app_id), "build": rel("builds", build_id)},
            )
            r.info("App Store Connect copies the previous version's localizations into the new version")

    vid = str(target["id"])
    result["versionId"] = vid

    # -- phase 2: bring the version in line ---------------------------------------------------
    steps = _phase_two(ctx, client, opts, state, target, build_id)
    for step in steps:
        r.change(step.text)
        if ctx.apply:
            step.run()

    gc_step = _game_center_step(
        ctx, client, opts, state, vid, build_changes=any(s.text.startswith("attach build") for s in steps)
    )
    if gc_step is not None:
        r.change(gc_step.text)
        if ctx.apply:
            gc_step.run()

    if not ctx.apply:
        total = len(steps) + (1 if gc_step else 0)
        r.blank()
        r.plain(
            f"Plan: {total} change(s). Re-run with --apply to write them"
            + (" and submit." if opts.submit else "; add --submit to also send the version to App Review.")
        )
        result["changes"] = total
        return result

    ok = _verify(ctx, client, opts, state, vid, build_id)
    result["verified"] = ok
    result["changes"] = r.changes
    if not ok:
        raise CheckFailed("Read-back verification failed; the version was NOT submitted.")
    if not opts.submit:
        r.blank()
        r.plain("Ready. Not submitted: add --submit (with --apply) or press Submit for Review in App Store Connect.")
        return result
    result.update(_submit(ctx, client, opts, vid))
    result["changes"] = r.changes
    return result


def _whats_new_by_locale(client: AscClient, version_id: str) -> dict[str, str]:
    """``{locale: What's New}`` of a version, normalized ("" when empty)."""
    return {
        str(attrs(item).get("locale")): _normalize(str(attrs(item).get("whatsNew") or ""))
        for item in client.get_all(
            f"/v1/appStoreVersions/{version_id}/appStoreVersionLocalizations",
            {"fields[appStoreVersionLocalizations]": "locale,whatsNew"},
        )
    }


def _resolve_whats_new(
    ctx: Context, client: AscClient, opts: ReleaseOptions, state: State, existing: dict[str, Any] | None
) -> ReleaseOptions:
    """Work out the What's New text for every locale, refusing problems before anything is written.

    Returns the options with ``whats_new`` covering each locale the kit will set:
    the file's locales plus, with a default text, every other locale of the version.
    """
    r = ctx.reporter
    if state.live is None:
        # First version of the app: App Store Connect doesn't take What's New (run_release says so).
        return replace(opts, whats_new=None, whats_new_default=None)
    current = _whats_new_by_locale(client, str((existing or state.live)["id"]))
    if existing is None:
        # A new version gets the live version's localizations, with What's New empty.
        current = {locale: "" for locale in current}
    wanted = dict(opts.whats_new or {})
    missing = sorted(set(wanted) - set(current))
    if missing:
        raise Blocked(
            "What's New names locale(s) the version doesn't have: "
            + ", ".join(missing)
            + ". Add them first (for example with `asc-release-kit aso apply`)."
        )
    if opts.whats_new_default is not None:
        for locale in current:
            wanted.setdefault(locale, opts.whats_new_default)
    empty = sorted(locale for locale, text in current.items() if locale not in wanted and not text)
    if empty:
        problem = (
            f"What's New would be empty for {', '.join(empty)}; App Store Connect requires it before an update "
            "can be submitted"
        )
        if ctx.apply and opts.submit:
            raise Blocked(
                problem + ". Nothing was written. Add those locales to --whats-new, or pass --whats-new-text "
                "for every locale the file doesn't list."
            )
        r.warn(
            problem
            + (
                " (--submit will refuse)"
                if opts.submit
                else "; add them to --whats-new, use --whats-new-text, or type them in App Store Connect"
            )
        )
    return replace(opts, whats_new=wanted or None)


def _check_open_submissions(ctx: Context, client: AscClient, opts: ReleaseOptions) -> None:
    """With --submit: refuse before any write while another review submission is still open."""
    submissions = client.get_all(
        "/v1/reviewSubmissions",
        {
            "filter[app]": opts.app_id,
            "filter[platform]": opts.platform,
            "filter[state]": ",".join(sorted(OPEN_SUBMISSION)),
        },
    )
    blocking = [s for s in submissions if attrs(s).get("state") in OPEN_SUBMISSION]
    if not blocking:
        return
    problem = (
        f"review submission {blocking[0]['id']} is {attrs(blocking[0]).get('state')}; resolve or cancel it in "
        "App Store Connect before submitting again"
    )
    if ctx.apply:
        raise Blocked(problem[0].upper() + problem[1:] + ". Nothing was written.")
    ctx.reporter.warn(problem + " (--submit will refuse)")


def _plan_for_new_version(ctx: Context, opts: ReleaseOptions, state: State) -> None:
    r = ctx.reporter
    if opts.whats_new and state.live is not None:
        for locale, text in opts.whats_new.items():
            r.change(f"what's new [{locale}]: {len(text)} chars")
    if state.live is not None and opts.copy_review_details:
        r.change(
            f"App Review details: copy from {attrs(state.live).get('versionString')} if App Store Connect "
            "didn't copy them" + (" (notes from file)" if opts.review_notes else "")
        )
    if _uses_game_center(opts, state):
        r.change("Game Center: make sure the new version is linked (copied from the live version's setting)")
    r.blank()
    r.plain(
        "Plan only. The exact remaining steps are computed after the version exists. Re-run with --apply"
        + (" (and --submit)" if opts.submit else "")
        + " to create it; verification runs then."
    )


def _phase_two(
    ctx: Context, client: AscClient, opts: ReleaseOptions, state: State, target: dict[str, Any], build_id: str
) -> list[Step]:
    vid = str(target["id"])
    steps: list[Step] = []
    t_attrs = attrs(target)

    # release type / date
    patch: dict[str, Any] = {}
    if opts.release_type and t_attrs.get("releaseType") != opts.release_type:
        patch["releaseType"] = opts.release_type
    if opts.earliest_release_date and not same_instant(t_attrs.get("earliestReleaseDate"), opts.earliest_release_date):
        patch["earliestReleaseDate"] = opts.earliest_release_date
    if patch:
        steps.append(
            Step(
                f"set {', '.join(f'{k}={v}' for k, v in patch.items())}",
                lambda: client.update("appStoreVersions", vid, patch),
            )
        )

    # build
    current_build = client.get_one(f"/v1/appStoreVersions/{vid}/build")
    if current_build is None or str(current_build.get("id")) != build_id:
        replaced = f" (replaces build {attrs(current_build).get('version')})" if current_build else ""
        steps.append(
            Step(
                f"attach build {opts.build}{replaced}",
                lambda: client.request(
                    "PATCH",
                    f"/v1/appStoreVersions/{vid}/relationships/build",
                    body={"data": {"type": "builds", "id": build_id}},
                ),
            )
        )

    # What's New
    if opts.whats_new and state.live is not None:
        locs = {
            str(attrs(item).get("locale")): item
            for item in client.get_all(
                f"/v1/appStoreVersions/{vid}/appStoreVersionLocalizations",
                {"fields[appStoreVersionLocalizations]": "locale,whatsNew"},
            )
        }
        for locale, text in opts.whats_new.items():
            item = locs.get(locale)
            if item is None:
                raise Blocked(f"Version {opts.version} has no {locale} localization; add it first (aso apply).")
            if _normalize(str(attrs(item).get("whatsNew") or "")) != text:
                steps.append(Step(f"what's new [{locale}]: {len(text)} chars", _whats_new_writer(client, item, text)))

    # App Review details
    review = client.get_one(f"/v1/appStoreVersions/{vid}/appStoreReviewDetail")
    if review is None:
        source = state.live_review
        if not opts.copy_review_details:
            raise Blocked(
                "The version has no App Review details and --no-copy-review-details was given; fill in App "
                "Review Information in App Store Connect first."
            )
        if source is None:
            raise Blocked(
                "The version has no App Review details and there is no live version to copy them from. Fill in "
                "App Review Information once in App Store Connect, then re-run."
            )
        copied = {k: attrs(source).get(k) for k in REVIEW_FIELDS if attrs(source).get(k) is not None}
        if opts.review_notes is not None:
            copied["notes"] = opts.review_notes
        has_demo = bool(copied.get("demoAccountRequired"))
        steps.append(
            Step(
                f"App Review details: copy contact{' and demo account' if has_demo else ''} from "
                f"{attrs(state.live).get('versionString') if state.live else 'live version'}"
                f"; notes {len(str(copied.get('notes') or ''))} chars"
                + (" from file" if opts.review_notes is not None else " (copied)"),
                lambda: client.create(
                    "appStoreReviewDetails", copied, {"appStoreVersion": rel("appStoreVersions", vid)}
                ),
            )
        )
    elif opts.review_notes is not None and _normalize(str(attrs(review).get("notes") or "")) != opts.review_notes:
        review_id = str(review["id"])
        notes = opts.review_notes
        steps.append(
            Step(
                f"App Review notes: replace with file ({len(notes)} chars)",
                lambda: client.update("appStoreReviewDetails", review_id, {"notes": notes}),
            )
        )
    return steps


def _whats_new_writer(client: AscClient, item: dict[str, Any], text: str) -> Callable[[], Any]:
    loc_id = str(item["id"])
    return lambda: client.update("appStoreVersionLocalizations", loc_id, {"whatsNew": text})


def _game_center_step(
    ctx: Context, client: AscClient, opts: ReleaseOptions, state: State, vid: str, build_changes: bool
) -> Step | None:
    if not _uses_game_center(opts, state):
        if opts.game_center == "auto" and state.live is not None:
            ctx.reporter.info("Game Center: the live version isn't linked; skipped")
        return None
    current = client.get_one(f"/v1/appStoreVersions/{vid}/gameCenterAppVersion")
    if current is None:
        return Step(
            "Game Center: link the version",
            lambda: client.create("gameCenterAppVersions", None, {"appStoreVersion": rel("appStoreVersions", vid)}),
        )
    if attrs(current).get("enabled") is False:
        gc_id = str(current["id"])
        return Step(
            "Game Center: re-enable the version's link (App Store Connect turned it off)",
            lambda: client.update("gameCenterAppVersions", gc_id, {"enabled": True}),
        )
    if build_changes and not ctx.apply:
        ctx.reporter.info(
            "Game Center: linked now; changing the build has been observed to switch the link off, so it's "
            "re-checked after the build step"
        )
    return None


# ------------------------------------------------------------------------------ verification


def _verify(ctx: Context, client: AscClient, opts: ReleaseOptions, state: State, vid: str, build_id: str) -> bool:
    r = ctx.reporter
    r.blank()
    r.plain("Read-back verification:")
    checks: list[dict[str, Any]] = []

    def check(name: str, passed: bool, text: str) -> None:
        r.check(passed, text)
        checks.append({"check": name, "ok": passed})

    version = client.get_one(f"/v1/appStoreVersions/{vid}")
    v_state = version_state(version)
    v_attrs = attrs(version)
    read_string = v_attrs.get("versionString")
    check(
        "version",
        version is not None and v_state in EDITABLE_VERSION,
        f"version {read_string or opts.version} is {v_state}",
    )
    check("versionString", read_string == opts.version, f"version string reads back as {read_string}")
    if opts.release_type:
        check(
            "releaseType",
            v_attrs.get("releaseType") == opts.release_type,
            f"release type {v_attrs.get('releaseType')}",
        )
    if opts.earliest_release_date:
        check(
            "earliestReleaseDate",
            same_instant(v_attrs.get("earliestReleaseDate"), opts.earliest_release_date),
            f"earliest release date reads back as {v_attrs.get('earliestReleaseDate')}",
        )
    build = client.get_one(f"/v1/appStoreVersions/{vid}/build")
    check("build", build is not None and str(build.get("id")) == build_id, f"build {opts.build} attached")

    if state.live is not None:
        locs = client.get_all(
            f"/v1/appStoreVersions/{vid}/appStoreVersionLocalizations",
            {"fields[appStoreVersionLocalizations]": "locale,whatsNew"},
        )
        for item in locs:
            locale = str(attrs(item).get("locale"))
            text = _normalize(str(attrs(item).get("whatsNew") or ""))
            if opts.whats_new and locale in opts.whats_new:
                check(f"whatsNew:{locale}", text == opts.whats_new[locale], f"what's new [{locale}] matches")
            elif text:
                check(f"whatsNew:{locale}", True, f"what's new [{locale}] is filled in")
            elif opts.submit:
                check(f"whatsNew:{locale}", False, f"what's new [{locale}] is EMPTY (required to submit an update)")
            else:
                r.warn(f"what's new [{locale}] is empty; fill it in before submitting")

    review = client.get_one(f"/v1/appStoreVersions/{vid}/appStoreReviewDetail")
    ra = attrs(review)
    contact_ok = review is not None and all(ra.get(k) for k in REQUIRED_CONTACT)
    check("reviewContact", contact_ok, "App Review contact details are filled in (values not shown)")
    if ra.get("demoAccountRequired"):
        check(
            "demoAccount",
            bool(ra.get("demoAccountName")) and bool(ra.get("demoAccountPassword")),
            "demo account name and password are filled in",
        )
    if opts.review_notes is not None:
        check(
            "reviewNotes",
            _normalize(str(ra.get("notes") or "")) == opts.review_notes,
            "App Review notes match the file",
        )

    if _uses_game_center(opts, state):
        gc = client.get_one(f"/v1/appStoreVersions/{vid}/gameCenterAppVersion")
        check("gameCenter", gc is not None and attrs(gc).get("enabled") is not False, "Game Center link is on")

    ok = all(c["ok"] for c in checks)
    ctx.journal().record("verify", target="release", version=vid, ok=ok, checks=checks)
    return ok


# ------------------------------------------------------------------------------ submission


def _submission_items(client: AscClient, submission_id: str) -> list[dict[str, Any]]:
    return client.get_all(
        f"/v1/reviewSubmissions/{submission_id}/items",
        {"include": SUBMISSION_ITEM_INCLUDES},
    )


def _item_target(item: dict[str, Any]) -> tuple[str, str] | None:
    for name, relationship in (item.get("relationships") or {}).items():
        if name == "reviewSubmission":
            continue
        data = (relationship or {}).get("data") if isinstance(relationship, dict) else None
        if isinstance(data, dict) and data.get("id"):
            return name, str(data["id"])
    return None


def version_in_submission(items: list[dict[str, Any]], version_id: str) -> bool:
    """True only if an item that is still part of the submission points at ``version_id``."""
    for item in items:
        relationship = (item.get("relationships") or {}).get("appStoreVersion") or {}
        data = relationship.get("data") if isinstance(relationship, dict) else None
        if (
            isinstance(data, dict)
            and str(data.get("id")) == version_id
            and str(attrs(item).get("state") or "") not in GONE_ITEM
        ):
            return True
    return False


def _submit(ctx: Context, client: AscClient, opts: ReleaseOptions, vid: str) -> dict[str, Any]:
    r = ctx.reporter
    r.blank()
    r.plain("Submission:")
    submissions = client.get_all(
        "/v1/reviewSubmissions",
        {
            "filter[app]": opts.app_id,
            "filter[platform]": opts.platform,
            "filter[state]": ",".join(sorted(OPEN_SUBMISSION | {DRAFT_SUBMISSION})),
        },
    )
    blocking = [s for s in submissions if attrs(s).get("state") in OPEN_SUBMISSION]
    if blocking:
        raise Blocked(
            f"Review submission {blocking[0]['id']} is {attrs(blocking[0]).get('state')}; resolve or cancel it in "
            "App Store Connect before submitting again."
        )
    draft = next((s for s in submissions if attrs(s).get("state") == DRAFT_SUBMISSION), None)
    if draft is None:
        r.change("create a review submission")
        draft = client.create("reviewSubmissions", {"platform": opts.platform}, {"app": rel("apps", opts.app_id)})
    else:
        r.info(f"using the draft review submission {draft['id']}")
    sid = str(draft["id"])

    if not version_in_submission(_submission_items(client, sid), vid):
        r.change(f"add version {opts.version} to the submission")
        try:
            client.create(
                "reviewSubmissionItems",
                None,
                {"reviewSubmission": rel("reviewSubmissions", sid), "appStoreVersion": rel("appStoreVersions", vid)},
            )
        except ApiError as exc:
            # A 409 can mean "already there"; a 5xx may or may not have worked. Either way the
            # re-read below decides. Don't retry blindly.
            r.warn(f"adding the version answered HTTP {exc.status}; reading the submission back to decide")

    items = _submission_items(client, sid)
    present = version_in_submission(items, vid)
    ctx.journal().record("verify", target="submission-items", submission=sid, version=vid, ok=present, items=len(items))
    if not present:
        raise CheckFailed(
            f"NOT SUBMITTED: version {opts.version} is not an item of review submission {sid}. App Store Connect "
            "has accepted a submission without the app version before, so the kit refuses to submit. Re-run "
            "later, or add the version to the draft in App Store Connect and submit there."
        )
    r.ok(f"version {opts.version} is in review submission {sid}")
    others = [t for t in (_item_target(i) for i in items) if t and t[0] != "appStoreVersion"]
    if others:
        kinds: dict[str, int] = {}
        for name, _ in others:
            kinds[name] = kinds.get(name, 0) + 1
        r.info("the submission also contains: " + ", ".join(f"{n} × {k}" for k, n in sorted(kinds.items())))

    r.change(f"submit review submission {sid}")
    try:
        client.update("reviewSubmissions", sid, {"submitted": True})
    except ApiError as exc:
        if exc.status < 500:
            raise
        r.warn(f"submit answered HTTP {exc.status}; reading the submission back to see what happened")

    after = client.get_one(f"/v1/reviewSubmissions/{sid}")
    sub_state = str(attrs(after).get("state") or "UNKNOWN")
    still_there = version_in_submission(_submission_items(client, sid), vid)
    ok = sub_state in SUBMITTED and still_there
    ctx.journal().record("verify", target="submission", submission=sid, version=vid, ok=ok, state=sub_state)
    if not ok:
        raise CheckFailed(
            f"Submission {sid} reads back as {sub_state}"
            + ("" if still_there else " without the app version")
            + "; check App Store Connect."
        )
    r.ok(f"submitted: review submission {sid} is {sub_state}")
    version = client.get_one(f"/v1/appStoreVersions/{vid}")
    r.info(f"version state now: {version_state(version)}")
    return {"submitted": True, "submissionId": sid, "submissionState": sub_state}
