"""App Store metadata from one JSON file: validate it, apply it, check storefront coverage.

File format (see ``examples/aso.json``)::

    {
      "app": {"id": "1234567890", "version": "1.2.0", "platform": "IOS"},
      "denylist": ["SomeBrand"],
      "localizations": {
        "en-US": {"name": "...", "subtitle": "...", "keywords": "a,b,c",
                  "promotionalText": "...", "description": "...", "whatsNew": "...",
                  "supportUrl": "https://...", "marketingUrl": "https://...",
                  "privacyPolicyUrl": "https://..."}
      }
    }

Keys starting with ``_`` are comments and ignored. Only the fields present in the
file are written; absent fields are left untouched in App Store Connect.
``aso pull`` writes such a file from what App Store Connect holds now.
"""

from __future__ import annotations

import json
import os
import re
import urllib.parse
from collections.abc import Iterable
from dataclasses import dataclass, field
from itertools import combinations
from typing import Any

from .api import AscClient, rel
from .context import Context
from .errors import ApiError, Blocked, CheckFailed, UsageError
from .fsutil import write_private
from .states import (
    EDITABLE_APP_INFO,
    EDITABLE_VERSION,
    LIVE_APP_INFO,
    app_info_state,
    attrs,
    find_version,
    list_versions,
    live_version,
    version_key,
    version_state,
)
from .storefronts import STOREFRONTS, Storefront, canonical_locale, coverage

INFO_FIELDS = ("name", "subtitle", "privacyPolicyUrl", "privacyChoicesUrl", "privacyPolicyText")
VERSION_FIELDS = ("description", "keywords", "promotionalText", "whatsNew", "supportUrl", "marketingUrl")
ALL_FIELDS = INFO_FIELDS + VERSION_FIELDS
URL_FIELDS = frozenset({"privacyPolicyUrl", "privacyChoicesUrl", "supportUrl", "marketingUrl"})

#: Apple's documented limits (App Store Connect Help: App information / Platform version information).
TEXT_LIMITS = {"name": 30, "subtitle": 30, "promotionalText": 170, "description": 4000, "whatsNew": 4000}
KEYWORDS_MAX_BYTES = 100
NAME_MIN_CHARS = 2

#: Fields Apple documents as editable without submitting a new version.
ANYTIME_FIELDS = frozenset({"promotionalText", "supportUrl", "marketingUrl", "privacyPolicyUrl", "privacyChoicesUrl"})
REQUIRED_NONEMPTY = ("name", "description", "keywords", "supportUrl")
DEFAULT_DENYLIST_FIELDS = ("name", "subtitle", "keywords", "promotionalText")

LABELS = {
    "name": "name",
    "subtitle": "subtitle",
    "keywords": "keywords",
    "promotionalText": "promotional text",
    "description": "description",
    "whatsNew": "what's new",
    "supportUrl": "support URL",
    "marketingUrl": "marketing URL",
    "privacyPolicyUrl": "privacy policy URL",
    "privacyChoicesUrl": "privacy choices URL",
    "privacyPolicyText": "privacy policy text",
}

KEYWORD_SEPARATOR_RE = re.compile("[,，]")  # English comma or full-width (Chinese) comma
WORD_RE = re.compile(r"\w+")
HTML_RE = re.compile(r"</?[A-Za-z][A-Za-z0-9]*(\s[^<>]*)?/?>")
#: Very common English words ignored by the duplicate-word heuristic.
STOP_WORDS = frozenset(
    {"the", "and", "for", "with", "your", "you", "are", "from", "into", "this", "that", "our", "its", "all", "new"}
)


@dataclass
class Issue:
    level: str  # "error" | "warning" | "info"
    locale: str | None
    field: str | None
    message: str

    def as_dict(self) -> dict[str, Any]:
        return {"level": self.level, "locale": self.locale, "field": self.field, "message": self.message}


@dataclass
class AsoDocument:
    path: str
    app: dict[str, str]
    localizations: dict[str, dict[str, str]]
    denylist: list[str] = field(default_factory=list)
    load_issues: list[Issue] = field(default_factory=list)


# --------------------------------------------------------------------------------- loading


def normalize_text(text: str) -> str:
    """CRLF to LF and surrounding whitespace removed (App Store Connect keeps plain text)."""
    return text.replace("\r\n", "\n").replace("\r", "\n").strip()


def load_document(path: str) -> AsoDocument:
    name = os.path.basename(path)
    try:
        with open(path, encoding="utf-8") as handle:
            raw = json.load(handle)
    except FileNotFoundError:
        raise UsageError(f"ASO file not found: {path}") from None
    except json.JSONDecodeError as exc:
        raise UsageError(f"{name}: invalid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}") from None
    except OSError as exc:
        raise UsageError(f"{name}: {exc.strerror}") from None
    if not isinstance(raw, dict) or not isinstance(raw.get("localizations"), dict) or not raw["localizations"]:
        raise UsageError(f'{name}: needs a non-empty "localizations" object keyed by locale (en-US, de-DE, ...).')

    issues: list[Issue] = []
    app_raw = raw.get("app") or {}
    if not isinstance(app_raw, dict):
        raise UsageError(f'{name}: "app" must be an object.')
    app = {k: str(v) for k, v in app_raw.items() if not str(k).startswith("_") and v is not None}

    denylist_raw = raw.get("denylist") or []
    if not isinstance(denylist_raw, list) or not all(isinstance(x, str) for x in denylist_raw):
        raise UsageError(f'{name}: "denylist" must be a list of strings.')

    localizations: dict[str, dict[str, str]] = {}
    for code, body in raw["localizations"].items():
        if str(code).startswith("_"):
            continue
        canonical = canonical_locale(str(code))
        if canonical is None:
            issues.append(
                Issue(
                    "error",
                    code,
                    None,
                    "unknown locale code; App Store Connect uses codes such as en-US, de-DE, ja, zh-Hans "
                    "(docs/storefront-locales.md lists all 50)",
                )
            )
            canonical = str(code)
        elif canonical != code:
            issues.append(Issue("warning", code, None, f"write the locale as '{canonical}'"))
        if canonical in localizations:
            issues.append(Issue("error", canonical, None, "locale appears more than once"))
        if not isinstance(body, dict):
            issues.append(Issue("error", canonical, None, "must be an object of fields"))
            continue
        values: dict[str, str] = {}
        for key, value in body.items():
            if str(key).startswith("_"):
                continue
            if key not in ALL_FIELDS:
                issues.append(Issue("warning", canonical, key, "unknown field, ignored (typo?)"))
                continue
            if not isinstance(value, str):
                issues.append(Issue("error", canonical, key, "must be a string"))
                continue
            values[key] = value.strip() if key in URL_FIELDS else normalize_text(value)
        localizations[canonical] = values
    return AsoDocument(path, app, localizations, list(denylist_raw), issues)


def is_comment(line: str) -> bool:
    """``# text`` (hash + space) or a lone ``#`` is a comment; ``#1`` is a term."""
    return line == "#" or line.startswith("# ")


def load_denylist(path: str) -> list[str]:
    """One term per line; blank lines and ``# comments`` ignored."""
    try:
        with open(path, encoding="utf-8") as handle:
            lines = handle.read().splitlines()
    except OSError as exc:
        raise UsageError(f"Denylist file can't be read: {exc.strerror}") from None
    return [line.strip() for line in lines if line.strip() and not is_comment(line.strip())]


# ------------------------------------------------------------------------------ validation


def char_count(text: str) -> int:
    return len(text)


def utf16_units(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def split_keywords(text: str) -> list[str]:
    return KEYWORD_SEPARATOR_RE.split(text) if text else []


def words(text: str) -> set[str]:
    """Indexable-looking words: 3+ characters (2+ for non-Latin scripts), minus a few English stop words."""
    return {
        w
        for w in WORD_RE.findall(text.casefold())
        if (len(w) >= 3 or (len(w) == 2 and not w.isascii())) and w not in STOP_WORDS
    }


def keyword_words(text: str) -> set[str]:
    out: set[str] = set()
    for token in split_keywords(text):
        out |= words(token)
    return out


def compile_denylist(terms: Iterable[str]) -> list[tuple[str, re.Pattern[str]]]:
    """Case-insensitive matchers; whole-word for terms that start/end with letters or digits."""
    patterns = []
    for raw in terms:
        term = raw.strip()
        if not term:
            continue
        folded = term.casefold()
        prefix = r"(?<!\w)" if re.match(r"\w", folded[0]) else ""
        suffix = r"(?!\w)" if re.match(r"\w", folded[-1]) else ""
        patterns.append((term, re.compile(prefix + re.escape(folded) + suffix)))
    return patterns


def denylist_hits(text: str, patterns: list[tuple[str, re.Pattern[str]]]) -> list[str]:
    folded = text.casefold()
    return [term for term, pattern in patterns if pattern.search(folded)]


def _check_keywords(locale: str, text: str) -> list[Issue]:
    issues: list[Issue] = []

    def add(level: str, message: str) -> None:
        issues.append(Issue(level, locale, "keywords", message))

    size = len(text.encode("utf-8"))
    if size > KEYWORDS_MAX_BYTES:
        add("error", f"{size} bytes, limit {KEYWORDS_MAX_BYTES} bytes (UTF-8: many non-Latin letters take 2-3 bytes)")
    parts = split_keywords(text)
    if text[:1] in (",", "，") or text[-1:] in (",", "，"):
        add("warning", "leading or trailing comma wastes a byte")
    empties = sum(1 for part in parts[1:-1] if not part.strip())
    if empties:
        add("warning", f"{empties} empty keyword(s) between commas")
    wasted = sum(len(part.encode("utf-8")) - len(part.strip().encode("utf-8")) for part in parts)
    if wasted:
        add("warning", f"spaces around commas waste {wasted} byte(s); write 'one,two' rather than 'one, two'")
    tokens = [part.strip() for part in parts if part.strip()]
    seen: set[str] = set()
    repeated: list[str] = []
    for token in tokens:
        key = token.casefold()
        if key in seen and token not in repeated:
            repeated.append(token)
        seen.add(key)
    if repeated:
        add("warning", "duplicate keyword(s): " + ", ".join(repeated))
    # Apple: "each greater than two characters". Applied to Latin-script keywords only;
    # two-character words are normal in Chinese, Japanese and Korean.
    short = [token for token in tokens if len(token) <= 2 and token.isascii()]
    if short:
        add(
            "warning",
            "keyword(s) of two characters or fewer: "
            + ", ".join(short)
            + " (Apple asks for keywords longer than two characters)",
        )
    return issues


def _check_url(locale: str, name: str, value: str) -> list[Issue]:
    parts = urllib.parse.urlsplit(value)
    if parts.scheme not in ("http", "https") or not parts.netloc or any(ch.isspace() for ch in value):
        return [Issue("error", locale, name, "is not a valid http(s) URL")]
    if parts.scheme == "http":
        return [Issue("warning", locale, name, "uses http://; prefer https://")]
    return []


def validate_locale(
    locale: str,
    values: dict[str, str],
    patterns: list[tuple[str, re.Pattern[str]]],
    denylist_fields: Iterable[str] = DEFAULT_DENYLIST_FIELDS,
) -> list[Issue]:
    issues: list[Issue] = []

    def add(level: str, name: str | None, message: str) -> None:
        issues.append(Issue(level, locale, name, message))

    for name, limit in TEXT_LIMITS.items():
        if name not in values:
            continue
        count = char_count(values[name])
        if count > limit:
            add("error", name, f"{count} characters, limit {limit}")
        elif utf16_units(values[name]) > limit:
            add(
                "warning",
                name,
                f"{count} characters but {utf16_units(values[name])} UTF-16 units; App Store Connect may count "
                f"emoji and some scripts differently (limit {limit})",
            )
    if "name" in values and 0 < len(values["name"]) < NAME_MIN_CHARS:
        add("error", "name", f"must be at least {NAME_MIN_CHARS} characters")
    for name in REQUIRED_NONEMPTY:
        if name in values and not values[name]:
            add("error", name, "is empty; App Store Connect requires it")
    if "whatsNew" in values and not values["whatsNew"]:
        add("warning", "whatsNew", "is empty; every update needs What's New text")
    if "keywords" in values:
        issues.extend(_check_keywords(locale, values["keywords"]))

    title_words = words(values.get("name", ""))
    subtitle_words = words(values.get("subtitle", ""))
    shared_title = title_words & subtitle_words
    if shared_title:
        add("warning", "subtitle", "repeats word(s) from the name: " + ", ".join(sorted(shared_title)))
    shared_keywords = (title_words | subtitle_words) & keyword_words(values.get("keywords", ""))
    if shared_keywords:
        add(
            "warning",
            "keywords",
            "repeat word(s) already in the name or subtitle, which Apple indexes anyway: "
            + ", ".join(sorted(shared_keywords)),
        )

    for name in sorted(URL_FIELDS):
        if values.get(name):
            issues.extend(_check_url(locale, name, values[name]))
    if values.get("description") and HTML_RE.search(values["description"]):
        add("warning", "description", "looks like it contains HTML; App Store descriptions are plain text")

    for name in denylist_fields:
        if values.get(name):
            hits = denylist_hits(values[name], patterns)
            if hits:
                add(
                    "error",
                    name,
                    "contains denylisted term(s) "
                    + ", ".join(repr(h) for h in hits)
                    + " (App Review Guideline 2.3.7: no trademarks, other apps' names or pricing terms to game search)",
                )
    return issues


def validate_document(
    doc: AsoDocument,
    denylist_terms: Iterable[str] = (),
    denylist_fields: Iterable[str] = DEFAULT_DENYLIST_FIELDS,
) -> list[Issue]:
    patterns = compile_denylist(list(doc.denylist) + list(denylist_terms))
    fields_ = tuple(denylist_fields)
    issues = list(doc.load_issues)
    for locale, values in doc.localizations.items():
        issues.extend(validate_locale(locale, values, patterns, fields_))
    return issues


def counts_for(values: dict[str, str]) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    for name, limit in TEXT_LIMITS.items():
        if name in values:
            out[name] = {"used": char_count(values[name]), "limit": limit}
    if "keywords" in values:
        out["keywords"] = {"used": len(values["keywords"].encode("utf-8")), "limit": KEYWORDS_MAX_BYTES, "bytes": 1}
    return out


def report_validation(ctx: Context, doc: AsoDocument, issues: list[Issue], strict: bool) -> dict[str, Any]:
    r = ctx.reporter
    r.title(f"aso validate · {os.path.basename(doc.path)} · {len(doc.localizations)} locale(s)")
    by_locale: dict[str | None, list[Issue]] = {}
    for issue in issues:
        by_locale.setdefault(issue.locale, []).append(issue)
    for locale, values in doc.localizations.items():
        counts = counts_for(values)
        summary = " · ".join(
            f"{LABELS[name]} {c['used']}/{c['limit']}{' B' if c.get('bytes') else ''}" for name, c in counts.items()
        )
        r.plain(f"{locale:<8} {summary or '(no length-limited fields)'}")
        for issue in by_locale.get(locale, []):
            text = f"{LABELS.get(issue.field or '', issue.field or 'locale')}: {issue.message}"
            if issue.level == "error":
                r.fail(text)
            elif issue.level == "warning":
                r.warn(text)
            else:
                r.info(text)
    for where, items in by_locale.items():
        if where in doc.localizations:
            continue
        for issue in items:
            prefix = f"{where}: " if where else ""
            (r.fail if issue.level == "error" else r.warn)(prefix + issue.message)
    errors = sum(1 for i in issues if i.level == "error")
    warnings = sum(1 for i in issues if i.level == "warning")
    r.blank()
    verdict = "FAILED" if errors or (strict and warnings) else "OK"
    r.plain(f"{verdict}: {errors} error(s), {warnings} warning(s)" + (" (strict: warnings fail)" if strict else ""))
    return {
        "file": os.path.basename(doc.path),
        "errors": errors,
        "warnings": warnings,
        "ok": verdict == "OK",
        "locales": {loc: counts_for(values) for loc, values in doc.localizations.items()},
        "issues": [i.as_dict() for i in issues],
    }


# ------------------------------------------------------------------------- storefronts


def keyword_overlaps(doc: AsoDocument, storefront: Storefront) -> list[tuple[str, str, list[str]]]:
    """Words that two of your locales both spend keyword bytes on inside one storefront."""
    present = sorted(loc for loc in storefront.locales if loc in doc.localizations)
    out = []
    for a, b in combinations(present, 2):
        shared = sorted(
            keyword_words(doc.localizations[a].get("keywords", ""))
            & keyword_words(doc.localizations[b].get("keywords", ""))
        )
        if shared:
            out.append((a, b, shared))
    return out


def report_storefronts(
    ctx: Context, locales: list[str], doc: AsoDocument | None, only: Storefront | None, show_all: bool = False
) -> dict[str, Any]:
    r = ctx.reporter
    rows = coverage(locales)
    if only is not None:
        rows = [row for row in rows if row.storefront.code == only.code]
        show_all = True
    r.title(f"aso storefronts · your locales: {', '.join(sorted(locales))}")
    result_rows = []
    overlap_count = 0
    for row in rows:
        if not row.present and only is None:
            continue
        sf = row.storefront
        overlaps = keyword_overlaps(doc, sf) if doc is not None else []
        overlap_count += len(overlaps)
        if show_all or overlaps:
            if row.default_present:
                default = sf.default
            elif row.present:
                default = sf.default + " (not yours: shoppers see one of your other languages)"
            else:
                default = sf.default + " (none of your locales: shoppers see your primary language)"
            extra = sorted(loc for loc in row.present if loc != sf.default)
            r.plain(
                f"{sf.code}  {sf.name:<32} default {default}" + (f" · also yours: {', '.join(extra)}" if extra else "")
            )
            for a, b, shared in overlaps:
                r.warn(f"{a} and {b} both apply here and repeat keyword(s): {', '.join(shared)}")
        entry: dict[str, Any] = {
            "storefront": sf.code,
            "name": sf.name,
            "default": sf.default,
            "defaultIsYours": row.default_present,
            "yours": list(row.present),
        }
        if doc is not None:
            entry["overlaps"] = [{"locales": [a, b], "words": shared} for a, b, shared in overlaps]
        result_rows.append(entry)
    reached_default = sum(1 for row in rows if row.default_present)
    reached_any = sum(1 for row in rows if row.present)
    if result_rows and not show_all and not overlap_count:
        r.plain("No keyword overlaps between your locales in any storefront.")
    r.blank()
    r.plain(
        f"Your locales are the default language in {reached_default} storefront(s) and are supported in "
        f"{reached_any} of {len(rows) if only is not None else len(STOREFRONTS)}."
    )
    if not show_all:
        r.plain("List every storefront with --all, or one with --storefront CODE (e.g. USA, TUR).")
    r.plain(
        "Note: Apple says a locale's keywords are searchable wherever the App Store supports that language; how "
        "much they weigh in ranking isn't documented, so overlap warnings are advice, not rules."
    )
    return {
        "storefronts": result_rows,
        "defaultIn": reached_default,
        "supportedIn": reached_any,
        "overlaps": overlap_count,
    }


# ------------------------------------------------------------------------------- apply


def _same(name: str, actual: Any, wanted: str) -> bool:
    actual_text = "" if actual is None else str(actual)
    if name in URL_FIELDS:
        return actual_text.strip().rstrip("/") == wanted.strip().rstrip("/")
    return normalize_text(actual_text) == normalize_text(wanted)


def _diff(current: dict[str, Any], wanted: dict[str, str]) -> dict[str, str]:
    return {name: value for name, value in wanted.items() if not _same(name, current.get(name), value)}


def _describe(values: dict[str, str]) -> str:
    parts = []
    for name, value in values.items():
        if name == "keywords":
            parts.append(f"keywords ({len(value.encode('utf-8'))} B)")
        elif name in URL_FIELDS:
            parts.append(LABELS[name])
        else:
            parts.append(f"{LABELS[name]} ({len(value)} chars)")
    return ", ".join(parts)


@dataclass
class ApplyOptions:
    app_id: str
    platform: str
    version: str
    fields: set[str] | None = None
    locales: set[str] | None = None


def _by_locale(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(attrs(item).get("locale")): item for item in items}


def _info_localizations(client: AscClient, info_id: str) -> dict[str, dict[str, Any]]:
    return _by_locale(client.get_all(f"/v1/appInfos/{info_id}/appInfoLocalizations"))


def _version_localizations(client: AscClient, version_id: str) -> dict[str, dict[str, Any]]:
    return _by_locale(client.get_all(f"/v1/appStoreVersions/{version_id}/appStoreVersionLocalizations"))


def apply_document(ctx: Context, doc: AsoDocument, opts: ApplyOptions) -> dict[str, Any]:
    r = ctx.reporter
    client = ctx.client()

    if opts.locales is not None:
        unknown = sorted(opts.locales - set(doc.localizations))
        if unknown:
            raise UsageError("--locales names locales that aren't in the file: " + ", ".join(unknown))
    targets: dict[str, dict[str, str]] = {}
    for locale, values in doc.localizations.items():
        if opts.locales is not None and locale not in opts.locales:
            continue
        chosen = {k: v for k, v in values.items() if opts.fields is None or k in opts.fields}
        if chosen:
            targets[locale] = chosen
    if not targets:
        raise UsageError("Nothing to apply: no fields left after --fields / --locales.")

    versions = list_versions(client, opts.app_id, opts.platform)
    version = find_version(versions, opts.version)
    if version is None:
        raise Blocked(
            f"There is no {opts.platform} version {opts.version} in App Store Connect. Create it first "
            "(for example with `asc-release-kit release`)."
        )
    state = version_state(version)
    requested = {name for values in targets.values() for name in values}
    if state not in EDITABLE_VERSION:
        locked = sorted(requested - ANYTIME_FIELDS)
        if locked:
            raise Blocked(
                f"Version {opts.version} is {state}. Only promotional text and the support, marketing and privacy "
                f"URLs can change now; locked fields requested: {', '.join(locked)}. "
                "Use --fields promotionalText (for example) or prepare a new version."
            )
    if live_version(versions) is None and "whatsNew" in requested:
        r.warn("first version of the app: App Store Connect doesn't accept What's New yet; skipping it")
        for values in targets.values():
            values.pop("whatsNew", None)
        requested.discard("whatsNew")

    app_info: dict[str, Any] | None = None
    if requested & set(INFO_FIELDS):
        infos = client.get_all(f"/v1/apps/{opts.app_id}/appInfos")
        editable = [i for i in infos if app_info_state(i) in EDITABLE_APP_INFO]
        live = [i for i in infos if app_info_state(i) in LIVE_APP_INFO]
        if editable:
            app_info = editable[0]
        else:
            locked = sorted((requested & set(INFO_FIELDS)) - ANYTIME_FIELDS)
            if locked:
                raise Blocked(
                    "There is no editable App Info record (App Store Connect opens one while a new version is "
                    f"being prepared), so these can't change now: {', '.join(locked)}."
                )
            app_info = live[0] if live else None
            if app_info is None:
                raise Blocked("Could not find the app's App Info record.")

    vid = str(version["id"])
    info_locs = _info_localizations(client, app_info["id"]) if app_info else {}
    ver_locs = _version_localizations(client, vid)

    mode = "APPLY" if ctx.apply else "PLAN (nothing is written; add --apply)"
    r.title(f"aso apply · app {opts.app_id} · {opts.platform} {opts.version} ({state}) · {mode}")

    # Work out every step before writing anything.
    steps: list[tuple[str, str, dict[str, str]]] = []  # (kind, locale, values)
    for locale, values in targets.items():
        info_want = {k: v for k, v in values.items() if k in INFO_FIELDS}
        ver_want = {k: v for k, v in values.items() if k in VERSION_FIELDS}
        if info_want:
            current = info_locs.get(locale)
            if current is None:
                if "name" not in info_want:
                    raise Blocked(f"{locale}: adding a new locale needs at least a name in the file.")
                steps.append(("info-create", locale, info_want))
            else:
                diff = _diff(attrs(current), info_want)
                if diff:
                    steps.append(("info-update", locale, diff))
        if ver_want:
            current = ver_locs.get(locale)
            if current is None:
                steps.append(("version-create", locale, ver_want))
            else:
                diff = _diff(attrs(current), ver_want)
                if diff:
                    steps.append(("version-update", locale, diff))

    changed_locales = {locale for _, locale, _ in steps}
    for locale in targets:
        if locale not in changed_locales:
            r.same(f"{locale}: up to date")
    for kind, locale, values in steps:
        if kind == "info-create":
            r.change(f"{locale}: create App Info localization: {_describe(values)}")
        elif kind == "info-update":
            r.change(f"{locale}: update {_describe(values)}")
        elif kind == "version-create":
            r.change(
                f"{locale}: create version localization: {_describe(values)}"
                + ("" if ctx.apply else " (or update it if App Store Connect creates it automatically)")
            )
        else:
            r.change(f"{locale}: update {_describe(values)}")

    result: dict[str, Any] = {
        "app": opts.app_id,
        "version": opts.version,
        "state": state,
        "changes": len(steps),
        "locales": sorted(targets),
        "verified": None,
    }
    if not ctx.apply:
        r.blank()
        r.plain(f"Plan: {len(steps)} change(s). Re-run with --apply to write them; nothing is submitted for review.")
        return result

    for kind, locale, values in steps:
        if kind == "info-create":
            assert app_info is not None
            client.create(
                "appInfoLocalizations", {"locale": locale, **values}, {"appInfo": rel("appInfos", app_info["id"])}
            )
        elif kind == "info-update":
            client.update("appInfoLocalizations", info_locs[locale]["id"], values)
        elif kind == "version-update":
            client.update("appStoreVersionLocalizations", ver_locs[locale]["id"], values)
        else:
            _create_version_localization(ctx, client, vid, locale, values)

    result["verified"] = verify_applied(ctx, client, app_info, vid, targets)
    if not result["verified"]:
        raise CheckFailed("Read-back verification failed: App Store Connect doesn't hold what the file says.")
    r.blank()
    r.plain(f"Done: {len(steps)} change(s) written and read back. Nothing was submitted for review.")
    return result


def _create_version_localization(
    ctx: Context, client: AscClient, version_id: str, locale: str, values: dict[str, str]
) -> None:
    """Create a version localization, coping with App Store Connect creating it on its own.

    Observed (2026-10): creating an App Info localization made App Store Connect add
    the matching version localization by itself, and a POST for it then returned
    409. So: re-read first, and on 409 re-read and update instead.
    """
    existing = _version_localizations(client, version_id).get(locale)
    if existing is None:
        try:
            client.create(
                "appStoreVersionLocalizations",
                {"locale": locale, **values},
                {"appStoreVersion": rel("appStoreVersions", version_id)},
            )
            return
        except ApiError as exc:
            if exc.status != 409:
                raise
            ctx.reporter.info(f"{locale}: App Store Connect already has this localization (409); updating it instead")
        existing = _version_localizations(client, version_id).get(locale)
        if existing is None:
            raise CheckFailed(f"{locale}: App Store Connect refused to create the localization and none exists.")
    diff = _diff(attrs(existing), values)
    if diff:
        client.update("appStoreVersionLocalizations", existing["id"], diff)


def verify_applied(
    ctx: Context,
    client: AscClient,
    app_info: dict[str, Any] | None,
    version_id: str,
    targets: dict[str, dict[str, str]],
) -> bool:
    r = ctx.reporter
    r.blank()
    r.plain("Read-back verification:")
    info_locs = _info_localizations(client, app_info["id"]) if app_info else {}
    ver_locs = _version_localizations(client, version_id)
    all_ok = True
    checks = []
    for locale, values in targets.items():
        bad = []
        for name, wanted in values.items():
            source = info_locs if name in INFO_FIELDS else ver_locs
            actual = attrs(source.get(locale)).get(name)
            if not _same(name, actual, wanted):
                bad.append(name)
        ok = not bad
        all_ok &= ok
        checks.append({"locale": locale, "ok": ok, "mismatched": bad})
        if ok:
            r.ok(f"{locale}: {len(values)} field(s) match")
        else:
            r.fail(f"{locale}: mismatch in {', '.join(bad)}")
    ctx.journal().record("verify", target="aso", version=version_id, ok=all_ok, checks=checks)
    return all_ok


# --------------------------------------------------------------------------------- pull

#: Field order in a pulled file: the order people usually edit them in.
PULL_FIELDS = (
    "name",
    "subtitle",
    "keywords",
    "promotionalText",
    "description",
    "supportUrl",
    "marketingUrl",
    "privacyPolicyUrl",
    "privacyChoicesUrl",
    "privacyPolicyText",
)


@dataclass
class PullOptions:
    app_id: str
    platform: str
    version: str | None = None
    out: str = "aso.json"
    force: bool = False


def _pull_version(versions: list[dict[str, Any]], wanted: str | None, platform: str) -> dict[str, Any]:
    if wanted:
        version = find_version(versions, wanted)
        if version is None:
            raise Blocked(f"There is no {platform} version {wanted} in App Store Connect.")
        return version
    editable = [v for v in versions if version_state(v) in EDITABLE_VERSION]
    if editable:
        return max(editable, key=lambda v: version_key(str(attrs(v).get("versionString"))))
    live = live_version(versions)
    if live is None:
        raise Blocked(f"The app has no {platform} version to read metadata from yet.")
    return live


def _pull_app_info(infos: list[dict[str, Any]], editable_version: bool) -> dict[str, Any] | None:
    editable = [i for i in infos if app_info_state(i) in EDITABLE_APP_INFO]
    live = [i for i in infos if app_info_state(i) in LIVE_APP_INFO]
    ordered = (editable + live) if editable_version else (live + editable)
    return ordered[0] if ordered else (infos[0] if infos else None)


def pull_document(ctx: Context, opts: PullOptions) -> dict[str, Any]:
    """Write an ASO file from App Store Connect's current metadata (read-only on Apple's side)."""
    r = ctx.reporter
    name = os.path.basename(opts.out)
    if os.path.lexists(opts.out) and not opts.force:
        raise UsageError(f"{name} already exists; pass --force to replace it, or --out to write another file.")
    client = ctx.client()
    versions = list_versions(client, opts.app_id, opts.platform)
    version = _pull_version(versions, opts.version, opts.platform)
    state = version_state(version)
    version_string = str(attrs(version).get("versionString"))
    r.title(f"aso pull · app {opts.app_id} · {opts.platform} {version_string} ({state}) -> {name}")

    info = _pull_app_info(client.get_all(f"/v1/apps/{opts.app_id}/appInfos"), state in EDITABLE_VERSION)
    info_locs = _info_localizations(client, str(info["id"])) if info else {}
    ver_locs = _version_localizations(client, str(version["id"]))
    localizations: dict[str, dict[str, str]] = {}
    for locale in sorted(set(info_locs) | set(ver_locs)):
        values: dict[str, str] = {}
        for field_name in PULL_FIELDS:
            source = info_locs if field_name in INFO_FIELDS else ver_locs
            value = attrs(source.get(locale)).get(field_name)
            if isinstance(value, str) and value.strip():
                values[field_name] = value.strip() if field_name in URL_FIELDS else normalize_text(value)
        if values:
            localizations[locale] = values
    if not localizations:
        raise Blocked(f"App Store Connect has no metadata for version {version_string} yet; nothing to pull.")

    document = {
        "_comment": (
            f"Pulled from App Store Connect: version {version_string} ({state}). What's New isn't included; "
            "pass it to `release --whats-new`. Edit, run `aso validate`, then `aso apply`."
        ),
        "app": {"id": opts.app_id, "version": version_string, "platform": opts.platform},
        "localizations": localizations,
    }
    write_private(opts.out, (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    for locale, values in localizations.items():
        r.ok(f"{locale}: {len(values)} field(s)")
    issues = validate_document(load_document(opts.out))
    errors = sum(1 for i in issues if i.level == "error")
    warnings = sum(1 for i in issues if i.level == "warning")
    r.info(f"validation: {errors} error(s), {warnings} warning(s); `aso validate {name}` shows them")
    r.info("What's New isn't pulled: it belongs to one release (use `release --whats-new`)")
    r.blank()
    r.plain(f"Wrote {name} (mode 0600). Nothing was changed in App Store Connect.")
    return {
        "app": opts.app_id,
        "version": version_string,
        "state": state,
        "out": name,
        "locales": sorted(localizations),
        "errors": errors,
        "warnings": warnings,
    }
