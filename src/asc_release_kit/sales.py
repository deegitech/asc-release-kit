"""Sales and Trends reports (``GET /v1/salesReports``).

Apple returns a gzip file (``application/a-gzip``) of tab-separated text. A 404
means there is no report for that date: no sales/downloads, or not published yet.
Reading Sales and Trends needs a *team* key with a Sales, Finance or Admin role;
individual keys can't access Sales and Finance.

Report type, sub-type and frequency are checked against Apple's table of allowed
combinations before anything is requested, so a typo fails here with the valid
choices instead of as an API error. Files are written with mode 0600 (they hold
units and proceeds) into directories created with mode 0700.
"""

from __future__ import annotations

import gzip
import os
import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from .context import Context
from .errors import ApiError, CheckFailed, UsageError
from .fsutil import private_dirs, write_private

FREQUENCIES = ("DAILY", "WEEKLY", "MONTHLY", "YEARLY")
REPORT_TYPES = (
    "SALES",
    "PRE_ORDER",
    "NEWSSTAND",
    "SUBSCRIPTION",
    "SUBSCRIPTION_EVENT",
    "SUBSCRIBER",
    "SUBSCRIPTION_OFFER_CODE_REDEMPTION",
    "INSTALLS",
    "FIRST_ANNUAL",
    "WIN_BACK_ELIGIBILITY",
)
SUB_TYPES = ("SUMMARY", "DETAILED", "SUMMARY_INSTALL_TYPE", "SUMMARY_TERRITORY", "SUMMARY_CHANNEL")

#: Apple's "Allowed values based on sales report type" for GET /v1/salesReports:
#: (report type, sub-type, frequencies, versions). Transcribed on 2026-10-03 from
#: https://developer.apple.com/documentation/appstoreconnectapi/get-v1-salesreports
ALLOWED: tuple[tuple[str, str, tuple[str, ...], tuple[str, ...]], ...] = (
    ("FIRST_ANNUAL", "DETAILED", ("DAILY",), ("1_0",)),
    ("FIRST_ANNUAL", "SUMMARY", ("YEARLY",), ("1_0",)),
    ("INSTALLS", "SUMMARY_CHANNEL", ("YEARLY",), ("1_0", "1_1")),
    ("INSTALLS", "SUMMARY_INSTALL_TYPE", ("YEARLY",), ("1_0", "1_1")),
    ("INSTALLS", "SUMMARY", ("MONTHLY",), ("1_2",)),
    ("INSTALLS", "SUMMARY_TERRITORY", ("YEARLY",), ("1_0", "1_1")),
    ("INSTALLS", "DETAILED", ("MONTHLY",), ("1_2",)),
    ("INSTALLS", "DETAILED", ("YEARLY",), ("1_0", "1_1")),
    ("NEWSSTAND", "DETAILED", ("DAILY", "WEEKLY"), ("1_0",)),
    ("PRE_ORDER", "SUMMARY", ("DAILY", "WEEKLY", "MONTHLY", "YEARLY"), ("1_0",)),
    ("SALES", "SUMMARY", ("DAILY", "WEEKLY", "MONTHLY", "YEARLY"), ("1_0",)),
    ("SUBSCRIBER", "DETAILED", ("DAILY",), ("1_3",)),
    ("SUBSCRIPTION", "SUMMARY", ("DAILY",), ("1_3",)),
    ("SUBSCRIPTION_EVENT", "SUMMARY", ("DAILY",), ("1_3",)),
    ("SUBSCRIPTION_OFFER_CODE_REDEMPTION", "SUMMARY", ("DAILY",), ("1_0",)),
    ("WIN_BACK_ELIGIBILITY", "SUMMARY", ("DAILY",), ("1_0",)),
)

#: Versions the kit sends when you don't pass --report-version. Apple says version 1_2
#: of these three reports is no longer available. Other reports get no filter[version]
#: and Apple picks.
VERSION_DEFAULTS = {"SUBSCRIPTION": "1_3", "SUBSCRIPTION_EVENT": "1_3", "SUBSCRIBER": "1_3"}

DATE_FORMATS = {
    "DAILY": (re.compile(r"^\d{4}-\d{2}-\d{2}$"), "YYYY-MM-DD"),
    "WEEKLY": (re.compile(r"^\d{4}-\d{2}-\d{2}$"), "YYYY-MM-DD (a week's ending Sunday)"),
    "MONTHLY": (re.compile(r"^\d{4}-\d{2}$"), "YYYY-MM"),
    "YEARLY": (re.compile(r"^\d{4}$"), "YYYY"),
}
MAX_RANGE_DAYS = 400


@dataclass
class SalesOptions:
    vendor_number: str
    frequency: str = "DAILY"
    report_type: str = "SALES"
    #: ``None``: the sub-type Apple's table allows for the report type and frequency
    sub_type: str | None = None
    report_version: str | None = None
    date: str | None = None
    date_from: str | None = None
    date_to: str | None = None
    out_dir: str = "sales"
    decompress: bool = False
    #: exit 0 even when Apple had no report for any requested date
    allow_missing: bool = False


def _ordered(values: set[str], order: tuple[str, ...]) -> list[str]:
    return sorted(values, key=lambda value: order.index(value) if value in order else len(order))


def resolve_report(report_type: str, sub_type: str | None, frequency: str) -> tuple[str, tuple[str, ...]]:
    """Check a combination against Apple's table; return ``(sub-type, versions Apple lists)``."""
    rows = [row for row in ALLOWED if row[0] == report_type]
    if not rows:
        raise UsageError(f"--report-type must be one of {', '.join(REPORT_TYPES)}.")
    if sub_type is None:
        candidates = {row[1] for row in rows if frequency in row[2]}
        if not candidates:
            frequencies = _ordered({f for row in rows for f in row[2]}, FREQUENCIES)
            raise UsageError(
                f"{report_type} reports are {', '.join(frequencies)} (Apple's table for Sales and Trends); "
                f"pass --frequency."
            )
        if "SUMMARY" in candidates:
            sub_type = "SUMMARY"
        elif len(candidates) == 1:
            (sub_type,) = candidates
        else:
            raise UsageError(
                f"{report_type} {frequency} reports need --report-sub-type "
                f"({', '.join(_ordered(candidates, SUB_TYPES))})."
            )
    typed = [row for row in rows if row[1] == sub_type]
    if not typed:
        allowed = _ordered({row[1] for row in rows}, SUB_TYPES)
        raise UsageError(f"{report_type} reports use --report-sub-type {' or '.join(allowed)}, not {sub_type}.")
    for row in typed:
        if frequency in row[2]:
            return sub_type, row[3]
    frequencies = _ordered({f for row in typed for f in row[2]}, FREQUENCIES)
    raise UsageError(
        f"{report_type} {sub_type} reports are {', '.join(frequencies)} only (Apple's table for Sales and "
        f"Trends), not {frequency}."
    )


def _validate(opts: SalesOptions) -> list[str | None]:
    if opts.frequency not in FREQUENCIES:
        raise UsageError(f"--frequency must be one of {', '.join(FREQUENCIES)}.")
    if opts.report_type not in REPORT_TYPES:
        raise UsageError(f"--report-type must be one of {', '.join(REPORT_TYPES)}.")
    if opts.sub_type is not None and opts.sub_type not in SUB_TYPES:
        raise UsageError(f"--report-sub-type must be one of {', '.join(SUB_TYPES)}.")
    if opts.report_version and not re.fullmatch(r"\d+_\d+", opts.report_version):
        raise UsageError("--report-version looks like 1_0 or 1_3.")
    pattern, label = DATE_FORMATS[opts.frequency]
    if opts.date_from or opts.date_to:
        if opts.date:
            raise UsageError("Use --date or --from/--to, not both.")
        if opts.frequency != "DAILY" or not (opts.date_from and opts.date_to):
            raise UsageError("--from and --to go together and only with --frequency DAILY.")
        try:
            if not (DATE_FORMATS["DAILY"][0].match(opts.date_from) and DATE_FORMATS["DAILY"][0].match(opts.date_to)):
                raise ValueError
            start = date.fromisoformat(opts.date_from)
            end = date.fromisoformat(opts.date_to)
        except ValueError:
            raise UsageError("--from/--to must be YYYY-MM-DD.") from None
        if end < start:
            raise UsageError("--to is before --from.")
        days = (end - start).days + 1
        if days > MAX_RANGE_DAYS:
            raise UsageError(f"At most {MAX_RANGE_DAYS} days per run.")
        return [(start + timedelta(days=i)).isoformat() for i in range(days)]
    if opts.date is None:
        if opts.frequency != "DAILY":
            raise UsageError(f"--date is required for {opts.frequency} reports ({label}).")
        return [None]
    if not pattern.match(opts.date):
        raise UsageError(f"--date for {opts.frequency} reports must be {label}.")
    return [opts.date]


def _filename(report_type: str, sub_type: str, frequency: str, when: str | None) -> str:
    parts = ["sales", report_type, sub_type, frequency, when or "latest"]
    return "-".join(p.lower().replace("_", "-") for p in parts) + ".tsv.gz"


def download(ctx: Context, opts: SalesOptions) -> dict[str, Any]:
    dates = _validate(opts)
    sub_type, versions = resolve_report(opts.report_type, opts.sub_type, opts.frequency)
    version = opts.report_version or VERSION_DEFAULTS.get(opts.report_type)
    ctx.redactor.add_secret(opts.vendor_number)
    r = ctx.reporter
    client = ctx.client()
    r.title(f"sales download · {opts.report_type}/{sub_type} · {opts.frequency} · {len(dates)} report(s)")
    if opts.report_version and opts.report_version not in versions:
        r.warn(
            f"Apple's table lists version {', '.join(versions)} for {opts.report_type}/{sub_type}; "
            f"requesting {opts.report_version} anyway"
        )
    files: list[dict[str, Any]] = []
    missing: list[str] = []
    for when in dates:
        params: dict[str, Any] = {
            "filter[frequency]": opts.frequency,
            "filter[reportType]": opts.report_type,
            "filter[reportSubType]": sub_type,
            "filter[vendorNumber]": opts.vendor_number,
        }
        if when:
            params["filter[reportDate]"] = when
        if version:
            params["filter[version]"] = version
        label = when or "latest"
        try:
            response = client.request("GET", "/v1/salesReports", params, accept="application/a-gzip", expect=(200,))
        except ApiError as exc:
            if exc.status == 404:
                missing.append(label)
                r.warn(f"{label}: no report (Apple answers 404 when there were no sales or it isn't published yet)")
                continue
            raise
        data = response.body
        if data[:2] != b"\x1f\x8b":
            raise CheckFailed(f"{label}: expected a gzip file from Apple, got something else; not saved.")
        try:
            text = gzip.decompress(data)
        except (OSError, EOFError):
            raise CheckFailed(f"{label}: the gzip file is corrupt; not saved.") from None
        private_dirs(opts.out_dir)
        path = os.path.join(opts.out_dir, _filename(opts.report_type, sub_type, opts.frequency, when))
        write_private(path, data)
        entry: dict[str, Any] = {"date": label, "path": path, "rows": max(0, text.count(b"\n") - 1)}
        if opts.decompress:
            plain = path[: -len(".gz")]
            write_private(plain, text)
            entry["decompressed"] = plain
        files.append(entry)
        r.ok(f"{label}: {entry['rows']} row(s) -> {path}")
    if not files and not opts.allow_missing:
        raise CheckFailed(
            "No report downloaded: Apple had none for the requested date(s). "
            "Pass --allow-missing to exit 0 in that case (useful for scheduled jobs)."
        )
    return {"files": files, "missing": missing, "subType": sub_type, "version": version}
