"""``doctor``: read-only checks of a setup, each failed one with the exact fix.

The checks run in order and skip what can't work yet (no key, so no token, so no
API calls). Nothing is written to App Store Connect or to disk. No secret is
printed: the key ID, issuer ID, key path and vendor number are masked as in every
other command, and the private key and tokens never leave memory.

Results:

* ``ok``: works;
* ``fail``: broken; the command exits 1;
* ``warn``: works but looks wrong or can't be confirmed; exit 1 only with ``--strict``;
* ``info``: an optional feature that isn't set up, with how to set it up; never fails.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime
from typing import Any

from . import credentials
from .api import LOOPBACK_HOSTS, AscClient, check_base_url, loopback_allowed
from .config import APP_ID_FIX, DEFAULT_BASE_URL, VENDOR_FIX, Settings, load_settings
from .context import CONFIG_ORIGINS
from .credentials import ApiKey
from .errors import ApiError, KitError, TransportError
from .hints import KEYS_PAGE, SETUP_URL, TROUBLESHOOTING_URL
from .jwt import CryptographySigner, TokenProvider, cryptography_available, decode_unverified, make_signer
from .output import Reporter
from .redact import Redactor
from .states import attrs

#: App Store Connect key IDs are ten capital letters and digits.
KEY_ID_RE = re.compile(r"^[A-Z0-9]{10}$")
ISSUER_ID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
KEY_FILE_RE = re.compile(r"^(?:AuthKey|ApiKey)_([A-Za-z0-9]+)\.p8$")
#: Seconds the local clock may differ from Apple's before the doctor warns.
CLOCK_SKEW_LIMIT = 60
#: Warn when fewer than this share of the hourly request limit is left.
RATE_LIMIT_WARN_SHARE = 0.1

ORIGIN_WORDS = {"flag": "a flag", "env": "the environment", "config": "the config file", "default": "the default"}
SOURCE_OK = {
    "env": "ASC_PRIVATE_KEY holds a complete PEM key (the usual way in CI)",
    "file": "key file: a regular file you own, readable by you only",
    "keychain": "Keychain item read and decoded into a complete PEM key",
    "ssm": "SSM parameter read and decoded into a complete PEM key",
}
NETWORK_FIX = "check the internet connection and any proxy (HTTPS_PROXY), then run doctor again"
STORE_KEY_FIX = (
    "store the .p8 key in ONE place (docs/setup.md, step 3), e.g. "
    "export ASC_PRIVATE_KEY_PATH=~/.config/asc-release-kit/AuthKey_ABC123DEFG.p8"
)
SELECT_XCODE_FIX = "sudo xcode-select -s /Applications/Xcode.app/Contents/Developer"
#: ``xcode-select -p`` prints a path like this when a full Xcode (not only the Command Line Tools) is active.
XCODE_APP_RE = re.compile(r"([^/]+\.app)/Contents/Developer/?$")


def _xcode_select_path() -> tuple[int, str]:
    """``(exit code, output)`` of ``xcode-select -p``, which prints the active developer directory.

    It only reads a setting: unlike ``xcodebuild`` or ``xcrun``, it never opens the
    "install the command line developer tools" dialog.
    """
    try:
        proc = subprocess.run(
            ["xcode-select", "-p"], capture_output=True, check=False, timeout=15, env=credentials.child_env()
        )
    except (OSError, subprocess.SubprocessError):
        return 127, ""
    return proc.returncode, proc.stdout.decode("utf-8", "replace").strip()


# Indirections so tests can simulate other machines without touching this one.
_runner: credentials.Runner | None = None
_platform: str | None = None
_which: Callable[[str], str | None] = shutil.which
_now: Callable[[], float] = time.time
_xcode_select: Callable[[], tuple[int, str]] = _xcode_select_path


@dataclass
class DoctorOptions:
    config: str | None = None
    app_id: str | None = None
    vendor_number: str | None = None
    offline: bool = False
    strict: bool = False
    json_mode: bool = False
    ascii_only: bool = False
    verbose: bool = False


@dataclass
class Check:
    id: str
    status: str  # ok | fail | warn | info
    text: str
    fix: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "status": self.status, "text": self.text, "fix": self.fix}


@dataclass
class Doctor:
    reporter: Reporter
    checks: list[Check] = field(default_factory=list)

    def section(self, title: str) -> None:
        self.reporter.blank()
        self.reporter.title(title)

    def _add(self, status: str, check_id: str, text: str, fix: str = "") -> None:
        fix = self.reporter.redactor.text(fix)
        self.checks.append(Check(check_id, status, self.reporter.redactor.text(text), fix))
        emit = {
            "ok": self.reporter.ok,
            "fail": self.reporter.fail,
            "warn": self.reporter.warn,
            "info": self.reporter.info,
        }
        emit[status](text, check=check_id, fix=fix or None)
        if fix and not self.reporter.json_mode:
            label = "fix" if status in ("fail", "warn") else "to set up"
            self.reporter.plain(f"    {label}: {fix}")

    def ok(self, check_id: str, text: str) -> None:
        self._add("ok", check_id, text)

    def fail(self, check_id: str, text: str, fix: str) -> None:
        self._add("fail", check_id, text, fix)

    def warn(self, check_id: str, text: str, fix: str) -> None:
        self._add("warn", check_id, text, fix)

    def info(self, check_id: str, text: str, fix: str = "") -> None:
        self._add("info", check_id, text, fix)

    def count(self, status: str) -> int:
        return sum(1 for check in self.checks if check.status == status)


def _origin(settings: Settings, attr: str) -> str:
    return ORIGIN_WORDS.get(settings.origin(attr), settings.origin(attr))


def _error_text(exc: KitError) -> str:
    """The error on one line (its fix is printed separately)."""
    return " · ".join(str(exc).split("\n  "))


def _api_fix(exc: ApiError) -> str:
    return exc.fix or "see the error above; " + TROUBLESHOOTING_URL


# ------------------------------------------------------------------------------ the checks


def _check_settings(doc: Doctor, settings: Settings) -> bool:
    """Identifiers and preferences. Returns False when nothing after this can work."""
    usable = True
    if settings.key_id:
        if KEY_ID_RE.match(settings.key_id):
            doc.ok("settings.key_id", f"key ID is set (from {_origin(settings, 'key_id')})")
        else:
            doc.warn(
                "settings.key_id",
                f"key ID is set (from {_origin(settings, 'key_id')}) but doesn't look like one: "
                "App Store Connect key IDs are 10 capital letters and digits",
                f"copy the KEY ID column from {KEYS_PAGE} > Team Keys",
            )
    else:
        usable = False
        doc.fail(
            "settings.key_id",
            "key ID is not set",
            f"copy the KEY ID column from {KEYS_PAGE} > Team Keys, then export ASC_KEY_ID=ABC123DEFG",
        )
    if settings.key_type == "team":
        if not settings.issuer_id:
            usable = False
            doc.fail(
                "settings.issuer_id",
                "issuer ID is not set (team keys need one)",
                "copy the Issuer ID shown above the Team Keys list, then "
                "export ASC_ISSUER_ID=00000000-0000-0000-0000-000000000000 "
                "(for a key from your own profile set ASC_KEY_TYPE=individual instead)",
            )
        elif not ISSUER_ID_RE.match(settings.issuer_id):
            doc.fail(
                "settings.issuer_id",
                f"issuer ID (from {_origin(settings, 'issuer_id')}) isn't a UUID; the key ID or the team ID "
                "pasted into the wrong place is a common mix-up",
                "copy the Issuer ID shown above the Team Keys list (it looks like "
                "00000000-0000-0000-0000-000000000000)",
            )
        else:
            doc.ok("settings.issuer_id", f"issuer ID is set (from {_origin(settings, 'issuer_id')}) and is a UUID")
    elif settings.issuer_id:
        usable = False
        doc.fail(
            "settings.issuer_id",
            "an individual key has an issuer ID set",
            "unset ASC_ISSUER_ID (and delete auth.issuer_id from the config file), or set ASC_KEY_TYPE=team",
        )
    else:
        doc.ok("settings.key_type", "individual key (no issuer ID, as Apple requires)")
    if settings.app_id:
        doc.ok("settings.app_id", f"app ID is set (from {_origin(settings, 'app_id')})")
    else:
        doc.info(
            "settings.app_id",
            "app ID is not set: release, aso, analytics and reviews then need --app",
            "once the key works, `asc-release-kit apps list` prints it (first column); export ASC_APP_ID=1234567890",
        )
    return usable


def _check_api_host(doc: Doctor, settings: Settings, env: Mapping[str, str]) -> bool:
    try:
        check_base_url(settings.api_base_url, allow_loopback=loopback_allowed(env))
    except KitError as exc:
        doc.fail("settings.api_host", _error_text(exc), "unset ASC_API_BASE_URL (the default is Apple's API host)")
        return False
    if settings.api_base_url == DEFAULT_BASE_URL:
        doc.ok("settings.api_host", "API host: Apple's App Store Connect API")
    elif (urllib.parse.urlsplit(settings.api_base_url).hostname or "") in LOOPBACK_HOSTS:
        doc.info("settings.api_host", "API host: a local mock server (ASC_ALLOW_INSECURE_LOOPBACK=1)")
    else:
        doc.warn(
            "settings.api_host",
            "API host comes from ASC_API_BASE_URL instead of the default",
            "unset ASC_API_BASE_URL unless you run a local mock server on purpose",
        )
    return True


def _in_git_repository(path: str) -> bool:
    directory = os.path.dirname(path)
    while True:
        if os.path.exists(os.path.join(directory, ".git")):
            return True
        parent = os.path.dirname(directory)
        if parent == directory:
            return False
        directory = parent


def _check_key(doc: Doctor, settings: Settings, env: Mapping[str, str]) -> ApiKey | None:
    """Find and read the key; ``None`` when there is no usable key (or no identifiers to use it with)."""
    try:
        sources = credentials.key_sources(settings, env)
    except KitError as exc:
        doc.fail("key.source", _error_text(exc), exc.fix or STORE_KEY_FIX)
        return None
    if not sources:
        doc.fail("key.source", "no private key source is configured", STORE_KEY_FIX)
        return None
    source = sources[0]
    doc.ok("key.source", f"one key source: {credentials.SOURCE_LABELS[source]}")
    runner = _runner or credentials.default_runner
    platform = _platform or sys.platform
    try:
        pem, path = credentials.read_private_key(source, settings, env, runner, platform)
    except KitError as exc:
        doc.fail("key.read", _error_text(exc), exc.fix or "see " + SETUP_URL)
        return None
    doc.ok("key.read", SOURCE_OK[source])
    if path:
        if _in_git_repository(path):
            doc.warn(
                "key.location",
                "the key file is inside a git repository, where it can be committed by accident",
                "mkdir -p ~/.config/asc-release-kit && chmod 700 ~/.config/asc-release-kit, move the file there "
                "and update ASC_PRIVATE_KEY_PATH",
            )
        else:
            doc.ok("key.location", "the key file is outside any git repository")
        match = KEY_FILE_RE.match(os.path.basename(path))
        if match and settings.key_id and match.group(1) != settings.key_id:
            doc.warn(
                "key.name",
                "the key file's name (AuthKey_<KEY_ID>.p8) names a different key ID than ASC_KEY_ID; "
                "a mismatch makes Apple reject every token",
                f"use the Key ID from the same row as this key in {KEYS_PAGE} > Team Keys",
            )
        elif match and settings.key_id:
            doc.ok("key.name", "the key file's name matches the key ID")
    if cryptography_available():
        try:
            CryptographySigner(pem)
        except KitError as exc:
            doc.fail("key.parse", _error_text(exc), exc.fix or "see " + SETUP_URL)
            return None
        doc.ok("key.parse", "the key is an unencrypted EC P-256 private key, the kind App Store Connect issues")
    if not settings.key_id or (settings.key_type == "team" and not settings.issuer_id):
        doc.info("key.use", "token not signed: set the identifiers marked above first")
        return None
    return ApiKey(
        key_id=str(settings.key_id),
        issuer_id=settings.issuer_id if settings.key_type == "team" else None,
        key_type=settings.key_type,
        pem=pem,
        source=source,
        path=path,
    )


def _check_token(doc: Doctor, settings: Settings, key: ApiKey, redactor: Redactor) -> TokenProvider | None:
    try:
        signer = make_signer(key, settings.signer, settings.openssl)
    except KitError as exc:
        doc.fail("token.signer", _error_text(exc), exc.fix or "see " + SETUP_URL)
        return None
    doc.ok(
        "token.signer",
        "signer: cryptography (in-process)" if signer.name == "cryptography" else "signer: the openssl command",
    )
    tokens = TokenProvider(key, signer, ttl=settings.token_ttl_seconds, on_new_token=redactor.add_secret)
    try:
        header, payload = decode_unverified(tokens())
    except KitError as exc:
        doc.fail("token.sign", _error_text(exc), exc.fix or "check that the key is the .p8 file from App Store Connect")
        return None
    lifetime = int(payload["exp"]) - int(payload["iat"])
    doc.ok(
        "token.sign",
        f"token signed ({header.get('alg')}); lifetime {lifetime} s (Apple's limit is 1200 s); "
        f"audience {payload.get('aud')}",
    )
    return tokens


def _check_clock(doc: Doctor, client: AscClient) -> None:
    if not client.last_server_date:
        return
    try:
        server = parsedate_to_datetime(client.last_server_date).timestamp()
    except (TypeError, ValueError):
        return
    skew = _now() - server
    if abs(skew) <= CLOCK_SKEW_LIMIT:
        doc.ok("api.clock", f"this computer's clock is within {CLOCK_SKEW_LIMIT} s of Apple's")
        return
    doc.warn(
        "api.clock",
        f"this computer's clock is {abs(skew):.0f} s {'ahead of' if skew > 0 else 'behind'} Apple's; "
        "tokens carry the local time, so Apple may reject them",
        "turn on automatic time (macOS: System Settings > General > Date & Time > Set time and date "
        "automatically; Linux: sudo timedatectl set-ntp true)",
    )


def _check_rate_limit(doc: Doctor, client: AscClient) -> None:
    text = client.last_rate_limit or ""
    limit = re.search(r"user-hour-lim:(\d+)", text)
    left = re.search(r"user-hour-rem:(\d+)", text)
    if not (limit and left):
        return
    total, remaining = int(limit.group(1)), int(left.group(1))
    message = f"rate limit: {remaining} of {total} requests left this hour"
    if total and remaining < total * RATE_LIMIT_WARN_SHARE:
        doc.warn(
            "api.rate_limit",
            message,
            "wait for the hour to roll over; another job using the same key may be busy",
        )
    else:
        doc.ok("api.rate_limit", message)


def _check_api(doc: Doctor, settings: Settings, env: Mapping[str, str], tokens: TokenProvider, verbose: bool) -> None:
    redactor = doc.reporter.redactor

    def log(line: str) -> None:
        print(redactor.text(line), file=sys.stderr)

    # A read-only client (POST, PATCH and DELETE are refused) without a journal: doctor writes nothing.
    client = AscClient(
        tokens,
        base_url=settings.api_base_url,
        journal=None,
        redactor=redactor,
        verbose=log if verbose else None,
        read_only=True,
        allow_loopback=loopback_allowed(env),
    )
    try:
        listing = client.get("/v1/apps", {"limit": 1, "fields[apps]": "bundleId"})
    except ApiError as exc:
        doc.fail("api.token", "App Store Connect refused the first request: " + _error_text(exc), _api_fix(exc))
        _check_clock(doc, client)
        return
    except TransportError as exc:
        doc.fail("api.token", _error_text(exc), NETWORK_FIX)
        return
    total = ((listing.get("meta") or {}).get("paging") or {}).get("total")
    count = total if total is not None else len(listing.get("data") or [])
    if count:
        doc.ok("api.token", f"Apple accepted the token; {count} app(s) visible to this key")
    else:
        doc.warn(
            "api.token",
            "Apple accepted the token, but this key sees no apps",
            "create the app record first (App Store Connect > Apps > + > New App), or use a key of the team that "
            "owns the app",
        )
    _check_clock(doc, client)
    _check_rate_limit(doc, client)
    name = "app"
    try:
        app_ok = _check_app(doc, settings, client)
        name = "sales"
        _check_sales(doc, settings, client)
        name = "analytics"
        if app_ok:
            _check_analytics(doc, settings, client)
        else:  # the same app ID would only fail again, or look like "no request yet"
            doc.info("api.analytics", "analytics request not checked: the app check above failed")
    except KitError as exc:  # the connection dropped, or a safety rule refused a link
        fix = NETWORK_FIX if isinstance(exc, TransportError) else exc.fix or NETWORK_FIX
        doc.fail(f"api.{name}", _error_text(exc), fix)


def _check_app(doc: Doctor, settings: Settings, client: AscClient) -> bool:
    """Whether the app can be read (also ``True`` when no app ID is set, as nothing failed)."""
    app_id = settings.app_id
    if not app_id:
        return True
    try:
        app = client.get_one(f"/v1/apps/{app_id}", {"fields[apps]": "name,bundleId,primaryLocale"})
    except ApiError as exc:
        doc.fail("api.app", f"app {app_id} can't be read: {_error_text(exc)}", _api_fix(exc))
        return False
    if app is None:
        doc.fail("api.app", f"app {app_id} isn't visible to this key", APP_ID_FIX)
        return False
    bundle = str(attrs(app).get("bundleId") or "?")
    expected = settings.bundle_id
    if expected and expected != bundle:
        doc.fail(
            "api.app",
            f"app {app_id} is {bundle}, but ASC_BUNDLE_ID / app.bundle_id says {expected}",
            "make ASC_APP_ID and ASC_BUNDLE_ID name the same app (`asc-release-kit apps list` shows both)",
        )
        return True  # the app ID itself works; only the bundle ID setting is off
    locale = attrs(app).get("primaryLocale")
    doc.ok("api.app", f"app {app_id} is visible: {bundle}" + (f" (primary language {locale})" if locale else ""))
    return True


def _check_sales(doc: Doctor, settings: Settings, client: AscClient) -> None:
    if not settings.vendor_number:
        doc.info(
            "api.sales",
            "vendor number is not set: only `sales download` needs it",
            VENDOR_FIX + "; then export ASC_VENDOR_NUMBER=87654321",
        )
        return
    if settings.key_type == "individual":
        doc.fail(
            "api.sales",
            "a vendor number is set, but individual keys can't access Sales and Trends",
            f"create a team key with the Sales (or Sales and Reports), Finance or Admin role under {KEYS_PAGE} "
            "> Team Keys",
        )
        return
    params = {
        "filter[frequency]": "DAILY",
        "filter[reportType]": "SALES",
        "filter[reportSubType]": "SUMMARY",
        "filter[vendorNumber]": settings.vendor_number,
    }
    try:
        client.request("GET", "/v1/salesReports", params, accept="application/a-gzip", expect=(200,))
    except ApiError as exc:
        if exc.status == 404:
            doc.warn(
                "api.sales",
                "Sales and Trends has no report for the latest day (HTTP 404: no sales or downloads that day, "
                "or not published yet), so the vendor number can't be confirmed",
                "try a recent day that had downloads: asc-release-kit sales download --date YYYY-MM-DD --out sales",
            )
            return
        doc.fail("api.sales", f"Sales and Trends refused the request: {_error_text(exc)}", _api_fix(exc))
        return
    doc.ok("api.sales", "Sales and Trends accepted the vendor number (latest daily report read, not saved)")


def _check_analytics(doc: Doctor, settings: Settings, client: AscClient) -> None:
    app_id = settings.app_id
    if not app_id:
        return
    request_fix = (
        "asc-release-kit analytics request --apply (Admin key, once per app; data starts the next day and the first "
        "files arrive about 24-48 h later; there's no backfill, use --access-type ONE_TIME_SNAPSHOT for history)"
    )
    try:
        requests = client.get_all(f"/v1/apps/{app_id}/analyticsReportRequests")
    except ApiError as exc:
        if exc.status == 403:
            doc.info(
                "api.analytics",
                "this key's role can't read analytics report requests (fine if you don't use `analytics`)",
                "use a key with the Admin, Sales and Reports, or Finance role for analytics",
            )
            return
        doc.fail("api.analytics", f"analytics report requests can't be read: {_error_text(exc)}", _api_fix(exc))
        return
    active = [q for q in requests if not attrs(q).get("stoppedDueToInactivity")]
    ongoing = [q for q in active if attrs(q).get("accessType") == "ONGOING"]
    if ongoing:
        doc.ok("api.analytics", "an active ONGOING analytics report request exists")
    elif requests and not active:
        doc.warn(
            "api.analytics",
            "the analytics report request stopped because its reports weren't downloaded for a long time",
            request_fix,
        )
    elif active:
        doc.info(
            "api.analytics",
            "only a ONE_TIME_SNAPSHOT analytics request exists; new days need an ONGOING one",
            request_fix,
        )
    else:
        doc.info("api.analytics", "no analytics report request yet (needed for `analytics download`)", request_fix)


def _check_tools(doc: Doctor) -> None:
    """Xcode for ``upload-build``. ``/usr/bin/xcrun`` exists on every Mac, so ask ``xcode-select`` instead."""
    if (_platform or sys.platform) != "darwin":
        doc.info(
            "tools.xcode", "upload-build runs on macOS only (it uses Xcode's tools); every other command works here"
        )
        return
    code, path = _xcode_select() if _which("xcode-select") else (127, "")
    if code != 0 or not path:
        doc.info(
            "tools.xcode",
            "no Xcode is selected (`xcode-select -p` found none): upload-build needs Xcode; every other command "
            "works without it",
            "install Xcode from the Mac App Store, open it once, then run: " + SELECT_XCODE_FIX,
        )
        return
    match = XCODE_APP_RE.search(path)
    if match:
        doc.ok(
            "tools.xcode",
            f"Xcode is selected ({match.group(1)}): upload-build can run xcodebuild, altool and notarytool",
        )
        return
    doc.info(
        "tools.xcode",
        "only the Command Line Tools are selected, not Xcode: upload-build needs Xcode (xcodebuild and altool come "
        "with it); every other command works",
        "install Xcode from the Mac App Store if needed, then run: " + SELECT_XCODE_FIX,
    )


# ------------------------------------------------------------------------------ entry point


def run_doctor(opts: DoctorOptions, redactor: Redactor, env: Mapping[str, str] | None = None) -> tuple[int, Reporter]:
    """Run every check; return ``(exit code, reporter)``. Never raises for a failed check."""
    env = os.environ if env is None else env
    reporter = Reporter(apply=False, json_mode=opts.json_mode, ascii_only=opts.ascii_only, redactor=redactor)
    doc = Doctor(reporter)
    reporter.title("doctor · read-only checks (nothing is written)" + (" · offline" if opts.offline else ""))

    doc.section("Settings")
    overrides = {"app_id": opts.app_id, "vendor_number": opts.vendor_number}
    try:
        settings = load_settings(opts.config, env, overrides)
    except KitError as exc:
        doc.fail("settings.config", "settings can't be loaded: " + _error_text(exc), exc.fix or TROUBLESHOOTING_URL)
        return _finish(doc, opts)
    redactor.add_secret(settings.key_id, settings.issuer_id, settings.private_key_path, settings.vendor_number)
    if settings.config_path:
        reporter.config = os.path.basename(settings.config_path)
        doc.ok(
            "settings.config",
            f"config file: {reporter.config} ({CONFIG_ORIGINS.get(settings.config_origin or '', 'chosen')})",
        )
    else:
        doc.ok("settings.config", "config file: none (settings come from ASC_* variables and flags)")
    usable = _check_settings(doc, settings)
    host_ok = _check_api_host(doc, settings, env)

    doc.section("Private key")
    key = _check_key(doc, settings, env)

    tokens: TokenProvider | None = None
    if key is not None:
        redactor.add_secrets(key.identifiers())
        doc.section("Token")
        tokens = _check_token(doc, settings, key, redactor)

    doc.section("App Store Connect")
    if opts.offline:
        doc.info("api", "skipped: --offline (nothing was sent to Apple)")
    elif tokens is None or not usable or not host_ok:
        doc.info("api", "skipped until the checks above pass")
    else:
        _check_api(doc, settings, env, tokens, opts.verbose)

    doc.section("Local tools")
    _check_tools(doc)
    return _finish(doc, opts)


def _finish(doc: Doctor, opts: DoctorOptions) -> tuple[int, Reporter]:
    reporter = doc.reporter
    failed, warned = doc.count("fail"), doc.count("warn")
    ok = failed == 0 and not (opts.strict and warned)
    reporter.blank()
    reporter.plain(
        f"{doc.count('ok')} passed, {warned} warning(s), {failed} failed"
        + (" (--strict: warnings fail)" if opts.strict and warned else "")
    )
    if not ok:
        reporter.plain(f"Fix the items marked above and run doctor again. More fixes: {TROUBLESHOOTING_URL}")
    result = {
        "ok": ok,
        "passed": doc.count("ok"),
        "warnings": warned,
        "failed": failed,
        "info": doc.count("info"),
        "checks": [check.as_dict() for check in doc.checks],
    }
    reporter.finish("doctor", result)
    return (0 if ok else 1), reporter
