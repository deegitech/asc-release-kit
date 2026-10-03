"""App Store Connect Analytics Reports API: request, list and download reports.

Flow (Apple, "Downloading analytics reports"): create a report *request* for an
app (``ONGOING`` or ``ONE_TIME_SNAPSHOT``) -> Apple generates *reports* (first ones
in 1-2 days) -> each report has *instances* per granularity and processing date ->
each instance has *segments*: pre-signed file URLs with an MD5 checksum and size.

Segment URLs are fetched without the API token, every file is checked against
Apple's MD5 and size before it is kept, and files already on disk with the right
checksum are skipped, so re-running a download is cheap. Files are written with
mode 0600 into directories created with mode 0700, and never outside ``--out``.
"""

from __future__ import annotations

import gzip
import hashlib
import os
import re
from dataclasses import dataclass
from typing import Any

from .api import AscClient, rel
from .context import Context
from .errors import Blocked, CheckFailed, SecurityError, UsageError
from .fsutil import is_inside, private_dirs, write_private
from .states import attrs

ACCESS_TYPES = ("ONGOING", "ONE_TIME_SNAPSHOT")
CATEGORIES = ("APP_USAGE", "APP_STORE_ENGAGEMENT", "COMMERCE", "FRAMEWORK_USAGE", "PERFORMANCE")
GRANULARITIES = ("DAILY", "WEEKLY", "MONTHLY")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "report"


def list_requests(client: AscClient, app_id: str, access_type: str | None = None) -> list[dict[str, Any]]:
    params: dict[str, Any] = {}
    if access_type:
        params["filter[accessType]"] = access_type
    return client.get_all(f"/v1/apps/{app_id}/analyticsReportRequests", params)


def _active(request: dict[str, Any]) -> bool:
    return not attrs(request).get("stoppedDueToInactivity")


def request_reports(ctx: Context, app_id: str, access_type: str, force_new: bool = False) -> dict[str, Any]:
    if access_type not in ACCESS_TYPES:
        raise UsageError(f"--access-type must be one of {', '.join(ACCESS_TYPES)}.")
    r = ctx.reporter
    client = ctx.client()
    mode = "APPLY" if ctx.apply else "PLAN (nothing is written; add --apply)"
    r.title(f"analytics request · app {app_id} · {access_type} · {mode}")
    existing = [q for q in list_requests(client, app_id, access_type) if _active(q)]
    if existing and not force_new:
        r.same(f"an active {access_type} request already exists ({existing[0]['id']}); nothing to do")
        return {"requestId": existing[0]["id"], "created": False}
    r.change(f"create an {access_type} analytics report request")
    if not ctx.apply:
        r.blank()
        r.plain("Plan only. Re-run with --apply. Apple generates the first reports in about 1-2 days.")
        return {"requestId": None, "created": False}
    created = client.create("analyticsReportRequests", {"accessType": access_type}, {"app": rel("apps", app_id)})
    check = client.get_one(f"/v1/analyticsReportRequests/{created.get('id')}")
    ok = check is not None and attrs(check).get("accessType") == access_type
    ctx.journal().record("verify", target="analyticsReportRequest", id=created.get("id"), ok=ok)
    if not ok:
        raise CheckFailed("The new report request could not be read back.")
    r.ok(f"request {created.get('id')} created and read back; first reports usually appear within 1-2 days")
    return {"requestId": created.get("id"), "created": True}


def list_reports(
    client: AscClient, request_id: str, category: str | None = None, name: str | None = None
) -> list[dict[str, Any]]:
    params: dict[str, Any] = {}
    if category:
        params["filter[category]"] = category
    if name:
        params["filter[name]"] = name
    return client.get_all(f"/v1/analyticsReportRequests/{request_id}/reports", params)


def show(ctx: Context, app_id: str, category: str | None, show_reports: bool) -> dict[str, Any]:
    r = ctx.reporter
    client = ctx.client()
    if category and category not in CATEGORIES:
        raise UsageError(f"--category must be one of {', '.join(CATEGORIES)}.")
    requests = list_requests(client, app_id)
    r.title(f"analytics · app {app_id} · {len(requests)} report request(s)")
    out = []
    for request in requests:
        a = attrs(request)
        status = "stopped (inactive; request again)" if a.get("stoppedDueToInactivity") else "active"
        r.plain(f"{request['id']}  {a.get('accessType')}  {status}")
        entry: dict[str, Any] = {"id": request["id"], "accessType": a.get("accessType"), "active": _active(request)}
        if show_reports or category:
            reports = list_reports(client, request["id"], category)
            entry["reports"] = []
            for report in sorted(reports, key=lambda x: (str(attrs(x).get("category")), str(attrs(x).get("name")))):
                ra = attrs(report)
                r.plain(f"    {report['id']}  {ra.get('category'):<22} {ra.get('name')}")
                entry["reports"].append({"id": report["id"], "category": ra.get("category"), "name": ra.get("name")})
        out.append(entry)
    if not requests:
        r.plain("No report requests yet. Create one with: asc-release-kit analytics request --apply")
    return {"requests": out}


@dataclass
class DownloadOptions:
    app_id: str
    report: str
    granularity: str = "DAILY"
    date: str | None = None
    since: str | None = None
    all_instances: bool = False
    out_dir: str = "analytics"
    decompress: bool = False
    access_type: str | None = None


def _resolve_report(client: AscClient, opts: DownloadOptions) -> tuple[dict[str, Any], dict[str, Any]]:
    requests = [q for q in list_requests(client, opts.app_id, opts.access_type) if _active(q)]
    if not requests:
        raise Blocked(
            "No active analytics report request. Create one with `analytics request --apply` and wait 1-2 days."
        )
    # ONGOING first: it keeps producing new instances.
    requests.sort(key=lambda q: 0 if attrs(q).get("accessType") == "ONGOING" else 1)
    wanted = opts.report.strip().lower()
    for request in requests:
        for report in list_reports(client, request["id"]):
            if str(report.get("id")).lower() == wanted or str(attrs(report).get("name") or "").lower() == wanted:
                return request, report
    raise Blocked(
        f"No report named or identified as '{opts.report}' in the active request(s). "
        "List them with `asc-release-kit analytics list --reports`."
    )


def _md5(data: bytes) -> str:
    # Apple publishes MD5 as an integrity checksum; it isn't used for security here.
    return hashlib.md5(data, usedforsecurity=False).hexdigest()


def _file_md5(path: str) -> str:
    digest = hashlib.md5(usedforsecurity=False)
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_inside(root: str, path: str, data: bytes) -> None:
    """Write privately and atomically, refusing any path that resolves outside ``root``."""
    if not is_inside(root, path):
        raise SecurityError("Refusing to write a report file outside the --out directory.")
    write_private(path, data)


def download(ctx: Context, opts: DownloadOptions) -> dict[str, Any]:
    if opts.granularity not in GRANULARITIES:
        raise UsageError(f"--granularity must be one of {', '.join(GRANULARITIES)}.")
    for value in (opts.date, opts.since):
        if value and not DATE_RE.match(value):
            raise UsageError("Dates must look like YYYY-MM-DD.")
    if opts.date and opts.since:
        raise UsageError("Use --date or --since, not both.")
    r = ctx.reporter
    client = ctx.client()
    request, report = _resolve_report(client, opts)
    name = str(attrs(report).get("name") or report["id"])
    r.title(f"analytics download · {name} · {opts.granularity}")

    params: dict[str, Any] = {"filter[granularity]": opts.granularity}
    if opts.date:
        params["filter[processingDate]"] = opts.date
    instances = client.get_all(f"/v1/analyticsReports/{report['id']}/instances", params)
    instances.sort(key=lambda i: str(attrs(i).get("processingDate") or ""), reverse=True)
    if opts.since:
        instances = [i for i in instances if str(attrs(i).get("processingDate") or "") >= opts.since]
    elif not opts.date and not opts.all_instances:
        instances = instances[:1]
    if not instances:
        r.warn("no instances match (reports appear 1-2 days after the request; check --granularity and dates)")
        return {"report": name, "files": []}

    files = []
    private_dirs(opts.out_dir)
    base = os.path.join(opts.out_dir, slug(name), opts.granularity.lower())
    for instance in instances:
        date = str(attrs(instance).get("processingDate") or "")
        if not DATE_RE.match(date):
            # The date becomes a directory name, so only YYYY-MM-DD is accepted.
            r.warn(f"an instance without a YYYY-MM-DD processing date was skipped ({date[:20]!r})")
            continue
        segments = client.get_all(f"/v1/analyticsReportInstances/{instance['id']}/segments")
        for index, segment in enumerate(segments, start=1):
            sa = attrs(segment)
            url = sa.get("url")
            if not url:
                r.warn(f"{date}: segment {segment.get('id')} has no download URL; skipped")
                continue
            expected_md5 = str(sa.get("checksum") or "").lower()
            stem = os.path.join(base, date, f"part-{index:03d}")
            gz_path = stem + ".csv.gz"
            existing = next(
                (
                    p
                    for p in (gz_path, stem + ".csv")
                    if expected_md5 and os.path.isfile(p) and _file_md5(p) == expected_md5
                ),
                None,
            )
            if existing:
                r.same(f"{date} part {index}: already downloaded")
                files.append({"path": existing, "skipped": True})
                continue
            data = client.download(str(url))
            if sa.get("sizeInBytes") is not None and int(sa["sizeInBytes"]) != len(data):
                raise CheckFailed(
                    f"{date} part {index}: size {len(data)} B, Apple says {sa['sizeInBytes']} B; not saved."
                )
            if expected_md5 and _md5(data) != expected_md5:
                raise CheckFailed(f"{date} part {index}: MD5 mismatch; not saved.")
            is_gzip = data[:2] == b"\x1f\x8b"
            path = gz_path if is_gzip else stem + ".csv"
            _write_inside(opts.out_dir, path, data)
            entry: dict[str, Any] = {"path": path, "bytes": len(data), "md5": _md5(data)}
            if opts.decompress and is_gzip:
                plain = stem + ".csv"
                _write_inside(opts.out_dir, plain, gzip.decompress(data))
                entry["decompressed"] = plain
            files.append(entry)
            r.ok(f"{date} part {index}: {len(data):,} B, checksum ok -> {path}")
    return {"report": name, "requestId": request["id"], "files": files}
