"""Command-line interface: ``asc-release-kit <command> ...`` (alias ``asckit``)."""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import signal
import sys
import threading
import traceback
from collections.abc import Callable, Iterator, Sequence
from typing import Any

from . import __version__
from .config import PLATFORMS, VENDOR_FIX
from .context import CONFIG_ORIGINS, Context
from .errors import (
    EXIT_CHECKS_FAILED,
    EXIT_OK,
    EXIT_USAGE,
    ApiError,
    CheckFailed,
    CredentialError,
    KitError,
    TransportError,
    UsageError,
)
from .hints import TROUBLESHOOTING_URL
from .redact import Redactor
from .sales import FREQUENCIES, REPORT_TYPES, SUB_TYPES

EXIT_UNEXPECTED = 70
EXIT_INTERRUPTED = 130

EPILOG = """\
Nothing is written to App Store Connect without --apply; without it every command only shows its plan.
Credentials come from the environment, a chmod 600 key file, the macOS Keychain or AWS SSM:
`asc-release-kit doctor` checks the whole setup and prints the fix for anything that fails.
Exit codes: 0 ok, 1 checks failed, 2 usage/config error, 3 App Store Connect API or network error,
4 stopped by App Store Connect's current state (checks run before writes), 70 unexpected error,
130 interrupted (Ctrl-C), 128+N stopped by signal N (143 for SIGTERM).
"""

Handler = Callable[[argparse.Namespace, Redactor], int]

#: Settings whose origin `auth check` reports (never their values).
ORIGIN_FIELDS = (
    ("key ID", "key_id"),
    ("issuer ID", "issuer_id"),
    ("key type", "key_type"),
    ("app ID", "app_id"),
    ("platform", "platform"),
    ("vendor number", "vendor_number"),
)


def _csv(value: str | None) -> set[str] | None:
    if not value:
        return None
    return {part.strip() for part in value.split(",") if part.strip()}


def _context(
    args: argparse.Namespace, command: str, redactor: Redactor, apply: bool = False, **overrides: Any
) -> Context:
    ctx = Context.create(
        command=command,
        config=getattr(args, "config", None),
        overrides=overrides,
        apply=apply,
        json_mode=getattr(args, "json", False),
        ascii_only=getattr(args, "ascii", False),
        verbose=getattr(args, "verbose", False),
        redactor=redactor,
    )
    # Kept so a failure in --json mode can still report the events collected so far.
    args._kit_context = ctx
    return ctx


# ------------------------------------------------------------------------------ handlers


def cmd_auth_check(args: argparse.Namespace, redactor: Redactor) -> int:
    from .jwt import decode_unverified

    ctx = _context(args, "auth check", redactor)
    r = ctx.reporter
    s = ctx.settings
    r.title("auth check")
    if s.config_path:
        r.info(f"config file: {os.path.basename(s.config_path)} ({CONFIG_ORIGINS.get(s.config_origin or '', '')})")
    else:
        r.info("config file: none")
    origins = {attr: (s.origin(attr) if getattr(s, attr) is not None else "not set") for _, attr in ORIGIN_FIELDS}
    r.info("settings from: " + ", ".join(f"{label} {origins[attr]}" for label, attr in ORIGIN_FIELDS))
    key = ctx.api_key()
    r.ok(f"{key.key_type} key loaded from the {key.describe()} (key ID and issuer ID are never shown)")
    if key.source == "file":
        r.ok("key file is readable by its owner only")
    tokens = ctx.tokens()
    header, payload = decode_unverified(tokens())
    lifetime = int(payload["exp"]) - int(payload["iat"])
    r.ok(
        f"token signed ({header.get('alg')}) with {tokens.signer_name}; lifetime {lifetime} s; audience {payload.get('aud')}"
    )
    result: dict[str, Any] = {
        "keyType": key.key_type,
        "source": key.source,
        "signer": tokens.signer_name,
        "config": os.path.basename(s.config_path) if s.config_path else None,
        "origins": origins,
        "apiReachable": None,
    }
    if args.offline:
        r.info("offline: App Store Connect was not contacted")
    else:
        client = ctx.client()
        doc = client.get("/v1/apps", {"limit": 1, "fields[apps]": "bundleId"})
        total = ((doc.get("meta") or {}).get("paging") or {}).get("total")
        count = total if total is not None else len(doc.get("data") or [])
        r.ok(f"App Store Connect accepted the token; {count} app(s) visible to this key")
        if client.last_rate_limit:
            r.info(f"rate limit: {client.last_rate_limit}")
        result.update(apiReachable=True, apps=count)
    r.finish("auth check", result)
    return EXIT_OK


def cmd_doctor(args: argparse.Namespace, redactor: Redactor) -> int:
    from .doctor import DoctorOptions, run_doctor

    opts = DoctorOptions(
        config=getattr(args, "config", None),
        app_id=args.app,
        vendor_number=args.vendor,
        offline=args.offline,
        strict=args.strict,
        json_mode=getattr(args, "json", False),
        ascii_only=getattr(args, "ascii", False),
        verbose=getattr(args, "verbose", False),
    )
    code, _ = run_doctor(opts, redactor)
    return code


def cmd_apps_list(args: argparse.Namespace, redactor: Redactor) -> int:
    ctx = _context(args, "apps list", redactor)
    r = ctx.reporter
    apps = ctx.client().get_all("/v1/apps", {"fields[apps]": "name,bundleId,primaryLocale"})
    rows = [
        {
            "id": app.get("id"),
            "bundleId": (app.get("attributes") or {}).get("bundleId"),
            "name": (app.get("attributes") or {}).get("name"),
            "primaryLocale": (app.get("attributes") or {}).get("primaryLocale"),
        }
        for app in apps
    ]
    rows.sort(key=lambda row: str(row["name"]).lower())
    if not rows:
        r.plain("No apps visible to this key.")
    for row in rows:
        r.plain(f"{row['id']:<12} {str(row['bundleId']):<40} {row['primaryLocale'] or '':<8} {row['name']}")
    r.finish("apps list", {"apps": rows})
    return EXIT_OK


def cmd_release(args: argparse.Namespace, redactor: Redactor) -> int:
    from .release import (
        WHATS_NEW_DEFAULT_KEY,
        ReleaseOptions,
        check_whats_new_text,
        load_review_notes,
        load_whats_new,
        run_release,
    )

    ctx = _context(args, "release", redactor, apply=args.apply, app_id=args.app, platform=args.platform)
    whats_new = load_whats_new(args.whats_new) if args.whats_new else None
    default_text = None
    if whats_new is not None:
        default_text = whats_new.pop(WHATS_NEW_DEFAULT_KEY, None)
        whats_new = whats_new or None
    if args.whats_new_text is not None:
        default_text = check_whats_new_text(args.whats_new_text, "--whats-new-text")
    opts = ReleaseOptions(
        app_id=ctx.app_id(),
        platform=ctx.platform(),
        version=args.version_string,
        build=args.build,
        whats_new=whats_new,
        whats_new_default=default_text,
        review_notes=load_review_notes(args.review_notes) if args.review_notes else None,
        release_type=args.release_type,
        earliest_release_date=args.earliest_release_date,
        game_center=args.game_center,
        copy_review_details=not args.no_copy_review_details,
        reuse_editable=args.reuse_editable,
        wait_for_build_minutes=args.wait_for_build,
        submit=args.submit,
    )
    result = run_release(ctx, opts)
    ctx.reporter.finish("release", result)
    return EXIT_OK


def _load_aso(args: argparse.Namespace) -> tuple[Any, list[str], tuple[str, ...]]:
    from .aso import DEFAULT_DENYLIST_FIELDS, load_denylist, load_document

    doc = load_document(args.file)
    terms = load_denylist(args.denylist) if getattr(args, "denylist", None) else []
    fields_ = tuple(sorted(_csv(args.denylist_fields) or ())) or DEFAULT_DENYLIST_FIELDS
    return doc, terms, fields_


def _from_aso_file(ctx: Context, attr: str, flag: str | None, file_value: str | None, label: str) -> str | None:
    """--flag wins. Otherwise the ASO file and ASC_*/the config file must agree when both are set."""
    if flag:
        return flag
    configured = getattr(ctx.settings, attr)
    where = ctx.settings.origin(attr)
    if file_value and configured and where in ("env", "config") and file_value != str(configured):
        source = "the environment (ASC_*)" if where == "env" else "the config file"
        raise UsageError(
            f"The ASO file is for {label} {file_value}, but {source} says {configured}. "
            f"Pass --{'app' if attr == 'app_id' else 'platform'} to choose."
        )
    return file_value or configured


def cmd_aso_validate(args: argparse.Namespace, redactor: Redactor) -> int:
    from .aso import report_validation, validate_document

    ctx = _context(args, "aso validate", redactor)
    doc, terms, fields_ = _load_aso(args)
    issues = validate_document(doc, terms, fields_)
    result = report_validation(ctx, doc, issues, args.strict)
    ctx.reporter.finish("aso validate", result)
    return EXIT_OK if result["ok"] else EXIT_CHECKS_FAILED


def cmd_aso_apply(args: argparse.Namespace, redactor: Redactor) -> int:
    from .aso import ALL_FIELDS, ApplyOptions, apply_document, validate_document
    from .storefronts import canonical_locale

    doc, terms, fields_ = _load_aso(args)
    ctx = _context(args, "aso apply", redactor, apply=args.apply, app_id=args.app, platform=args.platform)
    file_platform = str(doc.app["platform"]).upper() if doc.app.get("platform") else None
    if file_platform and file_platform not in PLATFORMS:
        raise UsageError(f"The ASO file's app.platform must be one of {', '.join(PLATFORMS)}.")
    app_id = _from_aso_file(ctx, "app_id", args.app, doc.app.get("id"), "app")
    platform = _from_aso_file(ctx, "platform", args.platform, file_platform, "platform")
    issues = validate_document(doc, terms, fields_)
    errors = [i for i in issues if i.level == "error"]
    warnings = [i for i in issues if i.level == "warning"]
    if errors or (args.strict and warnings):
        for issue in errors + (warnings if args.strict else []):
            ctx.reporter.fail(f"{issue.locale or ''} {issue.field or ''}: {issue.message}".strip())
        raise CheckFailed("The ASO file has validation problems; fix them (see `aso validate`) before applying.")
    if warnings:
        ctx.reporter.warn(f"{len(warnings)} validation warning(s); `aso validate` shows them")
    version = args.version_string or doc.app.get("version")
    if not version:
        raise UsageError("Pass --version or set app.version in the ASO file.")
    locales: set[str] | None = None
    if args.locales:
        locales = set()
        for code in sorted(_csv(args.locales) or ()):
            canonical = canonical_locale(code)
            if canonical is None:
                raise UsageError(f"--locales: unknown locale code {code} (App Store Connect codes such as en-US).")
            locales.add(canonical)
    opts = ApplyOptions(
        app_id=ctx.app_id(app_id),
        platform=ctx.platform(platform),
        version=version,
        fields=_csv(args.fields),
        locales=locales,
    )
    if opts.fields:
        unknown = sorted(opts.fields - set(ALL_FIELDS))
        if unknown:
            raise UsageError("--fields has unknown names: " + ", ".join(unknown))
    result = apply_document(ctx, doc, opts)
    ctx.reporter.finish("aso apply", result)
    return EXIT_OK


def cmd_aso_pull(args: argparse.Namespace, redactor: Redactor) -> int:
    from .aso import PullOptions, pull_document

    ctx = _context(args, "aso pull", redactor, app_id=args.app, platform=args.platform)
    opts = PullOptions(
        app_id=ctx.app_id(),
        platform=ctx.platform(),
        version=args.version_string,
        out=args.out,
        force=args.force,
    )
    result = pull_document(ctx, opts)
    ctx.reporter.finish("aso pull", result)
    return EXIT_OK


def cmd_aso_storefronts(args: argparse.Namespace, redactor: Redactor) -> int:
    from .aso import load_document, report_storefronts
    from .storefronts import canonical_locale, find

    ctx = _context(args, "aso storefronts", redactor)
    doc = load_document(args.file) if args.file else None
    locales: list[str] = []
    if doc is not None:
        locales = list(doc.localizations)
    for code in sorted(_csv(args.locales) or ()):
        canonical = canonical_locale(code)
        if canonical is None:
            raise UsageError(f"Unknown locale code: {code}")
        if canonical not in locales:
            locales.append(canonical)
    if not locales:
        raise UsageError("Pass an ASO file or --locales en-US,de-DE,...")
    only = None
    if args.storefront:
        only = find(args.storefront)
        if only is None:
            raise UsageError(f"Unknown storefront: {args.storefront} (use a code like USA or a name like Germany)")
    result = report_storefronts(ctx, locales, doc, only, args.all)
    ctx.reporter.finish("aso storefronts", result)
    return EXIT_OK


def cmd_analytics_request(args: argparse.Namespace, redactor: Redactor) -> int:
    from .analytics import request_reports

    ctx = _context(args, "analytics request", redactor, apply=args.apply, app_id=args.app)
    result = request_reports(ctx, ctx.app_id(), args.access_type, args.force_new)
    ctx.reporter.finish("analytics request", result)
    return EXIT_OK


def cmd_analytics_list(args: argparse.Namespace, redactor: Redactor) -> int:
    from .analytics import show

    ctx = _context(args, "analytics list", redactor, app_id=args.app)
    result = show(ctx, ctx.app_id(), args.category, args.reports)
    ctx.reporter.finish("analytics list", result)
    return EXIT_OK


def cmd_analytics_download(args: argparse.Namespace, redactor: Redactor) -> int:
    from .analytics import DownloadOptions, download

    ctx = _context(args, "analytics download", redactor, app_id=args.app)
    opts = DownloadOptions(
        app_id=ctx.app_id(),
        report=args.report,
        granularity=args.granularity,
        date=args.date,
        since=args.since,
        all_instances=args.all,
        out_dir=args.out,
        decompress=args.decompress,
        access_type=args.access_type,
    )
    result = download(ctx, opts)
    ctx.reporter.finish("analytics download", result)
    return EXIT_OK


def cmd_sales_download(args: argparse.Namespace, redactor: Redactor) -> int:
    from .sales import SalesOptions, download

    ctx = _context(args, "sales download", redactor, vendor_number=args.vendor)
    if not ctx.settings.vendor_number:
        raise UsageError("Pass --vendor or set ASC_VENDOR_NUMBER / [reports] vendor_number.", fix=VENDOR_FIX)
    opts = SalesOptions(
        vendor_number=ctx.settings.vendor_number,
        frequency=args.frequency,
        report_type=args.report_type,
        sub_type=args.report_sub_type,
        report_version=args.report_version,
        date=args.date,
        date_from=args.date_from,
        date_to=args.date_to,
        out_dir=args.out,
        decompress=args.decompress,
        allow_missing=args.allow_missing,
    )
    result = download(ctx, opts)
    ctx.reporter.finish("sales download", result)
    return EXIT_OK


def cmd_reviews_export(args: argparse.Namespace, redactor: Redactor) -> int:
    from .reviews import ReviewOptions, export

    ctx = _context(args, "reviews export", redactor, app_id=args.app)
    opts = ReviewOptions(
        app_id=ctx.app_id(),
        output_format=args.format,
        out=args.out,
        since=args.since,
        territory=args.territory,
        rating=args.rating,
        limit=args.limit,
        nicknames=not args.no_nicknames,
        raw_csv=args.raw_csv,
    )
    if getattr(args, "json", False) and not opts.out:
        raise UsageError("--json reports a summary; use --format json (or --out FILE) for the review data itself.")
    result = export(ctx, opts)
    ctx.reporter.finish("reviews export", result)
    return EXIT_OK


def cmd_upload_build(args: argparse.Namespace, redactor: Redactor) -> int:
    from .upload import UploadOptions, run_upload

    ctx = _context(args, "upload-build", redactor, apply=args.apply, app_id=args.app, platform=args.platform)
    opts = UploadOptions(
        archive=args.archive,
        ipa=args.ipa,
        notarize=args.notarize,
        export_options=args.export_options,
        export_path=args.export_path,
        team_id=args.team_id,
        app_id=ctx.settings.app_id,
        platform=ctx.platform(),
        wait_minutes=args.wait_processing,
    )
    result = run_upload(ctx, opts)
    ctx.reporter.finish("upload-build", result)
    return EXIT_OK


# ------------------------------------------------------------------------------ parser


def _global_options(parser: argparse.ArgumentParser) -> None:
    group = parser.add_argument_group("global options")
    group.add_argument(
        "--config",
        metavar="PATH",
        default=argparse.SUPPRESS,
        help="config file (default: ./asc-release-kit.toml or .json)",
    )
    group.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="print one JSON document")
    group.add_argument("--ascii", action="store_true", default=argparse.SUPPRESS, help="ASCII status markers")
    group.add_argument(
        "-v", "--verbose", action="store_true", default=argparse.SUPPRESS, help="log each API request to stderr"
    )


def _app_options(parser: argparse.ArgumentParser, platform: bool = True) -> None:
    parser.add_argument("--app", metavar="APP_ID", help="numeric Apple ID of the app (default: ASC_APP_ID / config)")
    if platform:
        parser.add_argument("--platform", choices=PLATFORMS, type=str.upper, help="default: IOS")


def _apply_option(parser: argparse.ArgumentParser, what: str = "write the changes") -> None:
    parser.add_argument("--apply", action="store_true", help=f"{what} (default: plan only)")


def _denylist_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--denylist", metavar="FILE", help="trademark/competitor/pricing terms, one per line")
    parser.add_argument(
        "--denylist-fields", metavar="LIST", help="fields to check (default: name,subtitle,keywords,promotionalText)"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="asc-release-kit",
        description="App Store Connect release and metadata chores: plan first, apply on purpose, verify by reading back.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"asc-release-kit {__version__}")
    _global_options(parser)
    commands = parser.add_subparsers(title="commands", metavar="<command>")

    def sub(
        container: Any, name: str, help_text: str, handler: Handler | None = None, **kw: Any
    ) -> argparse.ArgumentParser:
        p = container.add_parser(name, help=help_text, description=help_text, **kw)
        _global_options(p)
        if handler is None:
            # A command group typed without an action: show that group's help.
            def show_help(_args: argparse.Namespace, _redactor: Redactor, parser: argparse.ArgumentParser = p) -> int:
                parser.print_help(sys.stderr)
                return EXIT_USAGE

            p.set_defaults(handler=show_help)
        else:
            p.set_defaults(handler=handler)
        return p

    # auth
    auth = sub(commands, "auth", "check credentials")
    auth_sub = auth.add_subparsers(title="auth commands", metavar="<action>")
    p = sub(auth_sub, "check", "load the key, sign a token and (unless --offline) call the API", cmd_auth_check)
    p.add_argument("--offline", action="store_true", help="don't contact App Store Connect")

    # doctor
    p = sub(
        commands,
        "doctor",
        "check the whole setup step by step (read-only) and print the fix for each problem",
        cmd_doctor,
    )
    p.add_argument("--app", metavar="APP_ID", help="numeric Apple ID of the app (default: ASC_APP_ID / config)")
    p.add_argument("--vendor", metavar="NUMBER", help="vendor number to check (default: ASC_VENDOR_NUMBER / config)")
    p.add_argument(
        "--offline",
        action="store_true",
        help="send nothing to App Store Connect (a key in AWS SSM is still read with the AWS CLI)",
    )
    p.add_argument("--strict", action="store_true", help="exit 1 on warnings too, not only on failures")

    # apps
    apps = sub(commands, "apps", "list apps")
    apps_sub = apps.add_subparsers(title="apps commands", metavar="<action>")
    sub(apps_sub, "list", "list the apps this key can see", cmd_apps_list)

    # release
    p = sub(
        commands,
        "release",
        "create or reuse a version, attach a VALID build, set What's New, copy review details and Game Center, verify, submit",
        cmd_release,
    )
    _app_options(p)
    p.add_argument(
        "--version", dest="version_string", required=True, metavar="X.Y.Z", help="version string, e.g. 1.2.0"
    )
    p.add_argument("--build", required=True, metavar="NUMBER", help="build number (CFBundleVersion), e.g. 42")
    p.add_argument(
        "--whats-new",
        metavar="FILE",
        help='JSON {"en-US": "...", "*": "text for every other locale"} or an ASO file',
    )
    p.add_argument(
        "--whats-new-text",
        metavar="TEXT",
        help="What's New for every locale that --whats-new doesn't list (replaces the text there)",
    )
    p.add_argument(
        "--review-notes", metavar="FILE", help="text file for App Review notes (else copied from the live version)"
    )
    p.add_argument("--release-type", choices=("AFTER_APPROVAL", "MANUAL", "SCHEDULED"), type=str.upper)
    p.add_argument("--earliest-release-date", metavar="ISO8601", help="with --release-type SCHEDULED")
    p.add_argument(
        "--game-center",
        choices=("auto", "require", "skip"),
        default="auto",
        help="auto: mirror the live version (default)",
    )
    p.add_argument(
        "--no-copy-review-details", action="store_true", help="don't copy App Review details from the live version"
    )
    p.add_argument(
        "--reuse-editable", action="store_true", help="rename an existing editable version instead of stopping"
    )
    p.add_argument(
        "--wait-for-build", type=float, default=0.0, metavar="MINUTES", help="wait for the build to become VALID"
    )
    p.add_argument("--submit", action="store_true", help="after verification, submit for App Review (needs --apply)")
    _apply_option(p)

    # aso
    aso = sub(commands, "aso", "validate, apply and pull App Store metadata kept in one JSON file")
    aso_sub = aso.add_subparsers(title="aso commands", metavar="<action>")
    p = sub(
        aso_sub, "validate", "check limits, keyword format, duplicates and denylisted terms (offline)", cmd_aso_validate
    )
    p.add_argument("file", help="ASO JSON file")
    _denylist_options(p)
    p.add_argument("--strict", action="store_true", help="treat warnings as failures")
    p = sub(aso_sub, "apply", "write App Info and version localizations, then read them back", cmd_aso_apply)
    p.add_argument("file", help="ASO JSON file")
    _app_options(p)
    p.add_argument(
        "--version", dest="version_string", metavar="X.Y.Z", help="target version (default: app.version in the file)"
    )
    p.add_argument("--fields", metavar="LIST", help="only these fields, e.g. promotionalText")
    p.add_argument("--locales", metavar="LIST", help="only these locales, e.g. en-US,de-DE")
    _denylist_options(p)
    p.add_argument("--strict", action="store_true", help="refuse to apply when there are warnings")
    _apply_option(p)
    p = sub(
        aso_sub,
        "pull",
        "write an ASO file from the metadata App Store Connect holds now (read-only on Apple's side)",
        cmd_aso_pull,
    )
    _app_options(p)
    p.add_argument(
        "--version",
        dest="version_string",
        metavar="X.Y.Z",
        help="version to read (default: the editable version, else the live one)",
    )
    p.add_argument("--out", default="aso.json", metavar="FILE", help="file to write, mode 0600 (default: %(default)s)")
    p.add_argument("--force", action="store_true", help="replace --out if it already exists")
    p = sub(
        aso_sub,
        "storefronts",
        "show which storefronts support your locales and where keywords overlap",
        cmd_aso_storefronts,
    )
    p.add_argument("file", nargs="?", help="ASO JSON file (optional)")
    p.add_argument("--locales", metavar="LIST", help="locales to check, e.g. en-US,es-MX")
    p.add_argument("--storefront", metavar="CODE", help="only this storefront, e.g. USA or TUR")
    p.add_argument("--all", action="store_true", help="list every storefront your locales reach")

    # analytics
    analytics = sub(commands, "analytics", "Analytics Reports API")
    an_sub = analytics.add_subparsers(title="analytics commands", metavar="<action>")
    p = sub(
        an_sub, "request", "create an analytics report request (once per app and access type)", cmd_analytics_request
    )
    _app_options(p, platform=False)
    p.add_argument(
        "--access-type",
        choices=("ONGOING", "ONE_TIME_SNAPSHOT"),
        type=str.upper,
        default="ONGOING",
        help="default: %(default)s",
    )
    p.add_argument("--force-new", action="store_true", help="create one even if an active request exists")
    _apply_option(p, "create the request")
    p = sub(an_sub, "list", "list report requests and (with --reports) their reports", cmd_analytics_list)
    _app_options(p, platform=False)
    p.add_argument("--reports", action="store_true", help="also list each request's reports")
    p.add_argument(
        "--category",
        type=str.upper,
        choices=("APP_USAGE", "APP_STORE_ENGAGEMENT", "COMMERCE", "FRAMEWORK_USAGE", "PERFORMANCE"),
    )
    p = sub(an_sub, "download", "download report segments, verifying Apple's MD5 checksums", cmd_analytics_download)
    _app_options(p, platform=False)
    p.add_argument(
        "--report", required=True, metavar="NAME_OR_ID", help='e.g. "App Store Discovery and Engagement Standard"'
    )
    p.add_argument(
        "--granularity",
        choices=("DAILY", "WEEKLY", "MONTHLY"),
        type=str.upper,
        default="DAILY",
        help="default: %(default)s",
    )
    p.add_argument("--date", metavar="YYYY-MM-DD", help="one processing date")
    p.add_argument("--since", metavar="YYYY-MM-DD", help="all instances processed on or after this date")
    p.add_argument("--all", action="store_true", help="every available instance (default: the newest)")
    p.add_argument("--access-type", choices=("ONGOING", "ONE_TIME_SNAPSHOT"), type=str.upper)
    p.add_argument("--out", default="analytics", metavar="DIR", help="where files go (default: %(default)s)")
    p.add_argument("--decompress", action="store_true", help="also write the uncompressed file")

    # sales
    sales = sub(commands, "sales", "Sales and Trends reports")
    sales_sub = sales.add_subparsers(title="sales commands", metavar="<action>")
    p = sub(sales_sub, "download", "download a Sales and Trends report (gzip TSV)", cmd_sales_download)
    p.add_argument("--vendor", metavar="NUMBER", help="vendor number (default: ASC_VENDOR_NUMBER / config)")
    p.add_argument("--frequency", type=str.upper, default="DAILY", choices=FREQUENCIES, help="default: %(default)s")
    p.add_argument("--report-type", type=str.upper, default="SALES", choices=REPORT_TYPES, help="default: %(default)s")
    p.add_argument(
        "--report-sub-type",
        type=str.upper,
        choices=SUB_TYPES,
        help="default: the one Apple's table allows for the type and frequency (SUMMARY for SALES)",
    )
    p.add_argument(
        "--report-version",
        metavar="1_0",
        help="pin a report version (default: 1_3 for subscription reports, otherwise Apple chooses)",
    )
    p.add_argument(
        "--date", metavar="DATE", help="YYYY-MM-DD, YYYY-MM or YYYY depending on --frequency (DAILY default: latest)"
    )
    p.add_argument("--from", dest="date_from", metavar="YYYY-MM-DD", help="first day of a DAILY range")
    p.add_argument("--to", dest="date_to", metavar="YYYY-MM-DD", help="last day of a DAILY range")
    p.add_argument("--out", default="sales", metavar="DIR", help="where files go (default: %(default)s)")
    p.add_argument("--decompress", action="store_true", help="also write the .tsv")
    p.add_argument(
        "--allow-missing",
        action="store_true",
        help="exit 0 when Apple has no report for any requested date (default: exit 1)",
    )

    # reviews
    reviews = sub(commands, "reviews", "customer reviews")
    rev_sub = reviews.add_subparsers(title="reviews commands", metavar="<action>")
    p = sub(rev_sub, "export", "export customer reviews (newest first) to CSV, JSON Lines or JSON", cmd_reviews_export)
    _app_options(p, platform=False)
    p.add_argument("--format", choices=("csv", "jsonl", "json"), default="csv", help="default: %(default)s")
    p.add_argument("--out", metavar="FILE", help="write here (mode 0600) instead of stdout")
    p.add_argument("--since", metavar="YYYY-MM-DD", help="stop at reviews older than this date")
    p.add_argument("--territory", metavar="ISO3", help="e.g. USA, DEU, TUR")
    p.add_argument("--rating", type=int, choices=range(1, 6), metavar="1-5")
    p.add_argument("--limit", type=int, metavar="N")
    p.add_argument("--no-nicknames", action="store_true", help="leave reviewer nicknames out")
    p.add_argument("--raw-csv", action="store_true", help="don't guard cells against spreadsheet formula injection")

    # upload-build
    p = sub(
        commands,
        "upload-build",
        "upload with xcodebuild/altool (or notarize with notarytool) using the API key",
        cmd_upload_build,
    )
    source = p.add_mutually_exclusive_group(required=True)
    source.add_argument("--archive", metavar="PATH.xcarchive", help="export and upload with xcodebuild")
    source.add_argument("--ipa", metavar="PATH.ipa", help="upload an existing IPA with altool")
    source.add_argument(
        "--notarize", metavar="PATH", help="notarize a .zip/.dmg/.pkg with notarytool (outside the Mac App Store)"
    )
    _app_options(p)
    p.add_argument("--export-options", metavar="PLIST", help="ExportOptions.plist (default: generated)")
    p.add_argument("--export-path", metavar="DIR", help="where xcodebuild writes its export (default: temporary)")
    p.add_argument("--team-id", metavar="TEAM_ID", help="teamID for the generated export options")
    p.add_argument(
        "--wait-processing",
        type=float,
        default=0.0,
        metavar="MINUTES",
        help="after upload, wait until the build is VALID",
    )
    _apply_option(p, "run the upload")
    return parser


@contextlib.contextmanager
def _exit_on_termination() -> Iterator[None]:
    """Turn SIGTERM and SIGHUP into a normal exit while a command runs.

    Python's default for these signals ends the process without running ``finally``
    blocks, which would leave a temporary key copy behind and Xcode's tools running.
    Raising ``SystemExit(128 + signal)`` instead lets every cleanup run. Signals
    that are ignored (for example SIGHUP under ``nohup``) stay ignored, and the
    previous handlers come back afterwards.
    """
    if threading.current_thread() is not threading.main_thread():
        yield
        return

    def handler(signum: int, _frame: Any) -> None:
        raise SystemExit(128 + signum)

    previous: dict[int, Any] = {}
    for name in ("SIGTERM", "SIGHUP"):
        number = getattr(signal, name, None)
        if number is None:
            continue
        current = signal.getsignal(number)
        if current is signal.SIG_IGN:
            continue
        try:
            signal.signal(number, handler)
        except (OSError, ValueError):
            continue
        previous[number] = current
    try:
        yield
    finally:
        for number, old in previous.items():
            with contextlib.suppress(OSError, ValueError, TypeError):
                signal.signal(number, old if old is not None else signal.SIG_DFL)


def _setup_problem(exc: KitError) -> bool:
    """Errors that usually mean the key or account setup is wrong, so ``doctor`` is the next step."""
    if isinstance(exc, (CredentialError, TransportError)):
        return True
    return isinstance(exc, ApiError) and exc.status in (401, 403)


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handler: Handler | None = getattr(args, "handler", None)
    if handler is None:
        parser.print_help(sys.stderr)
        return EXIT_USAGE
    redactor = Redactor()
    with _exit_on_termination():
        try:
            return int(handler(args, redactor) or 0)
        except KitError as exc:
            message = redactor.text(str(exc))
            fix = redactor.text(exc.fix)
            if getattr(args, "json", False):
                ctx: Context | None = getattr(args, "_kit_context", None)
                document = (
                    ctx.reporter.error_document(ctx.command, message, exc.exit_code)
                    if ctx is not None
                    else {"error": message, "exitCode": exc.exit_code}
                )
                if fix:
                    document["fix"] = fix
                print(json.dumps(document, indent=2, ensure_ascii=False))
            print(f"error: {message}", file=sys.stderr)
            if fix:
                print(f"fix: {fix}", file=sys.stderr)
            if _setup_problem(exc):
                print(
                    f"(`asc-release-kit doctor` checks the setup step by step; more fixes: {TROUBLESHOOTING_URL})",
                    file=sys.stderr,
                )
            return exc.exit_code
        except KeyboardInterrupt:
            print("interrupted", file=sys.stderr)
            return EXIT_INTERRUPTED
        except Exception as exc:  # noqa: BLE001 - last-resort report without leaking secrets
            print(f"unexpected error: {redactor.text(f'{exc.__class__.__name__}: {exc}')}", file=sys.stderr)
            if getattr(args, "verbose", False):
                print(redactor.text(traceback.format_exc()), file=sys.stderr)
            else:
                print("(run with --verbose for a traceback)", file=sys.stderr)
            return EXIT_UNEXPECTED


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
