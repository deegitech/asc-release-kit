"""Export customer reviews (``GET /v1/apps/{id}/customerReviews``) to CSV or JSON Lines.

CSV output guards against spreadsheet formula injection: review text is
user-generated, so a cell that starts with ``=``, ``+``, ``-``, ``@``, tab or
carriage return gets a leading apostrophe (OWASP's recommendation). Pass
``--raw-csv`` to turn that off. Files written with ``--out`` get mode 0600, also
when the file already existed with looser permissions.
"""

from __future__ import annotations

import csv
import io
import json
import os
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, TextIO

from .context import Context
from .errors import UsageError
from .fsutil import open_private
from .states import attrs

FORMATS = ("csv", "jsonl", "json")
COLUMNS = (
    "id",
    "createdDate",
    "rating",
    "territory",
    "title",
    "body",
    "reviewerNickname",
    "responseBody",
    "responseState",
    "responseLastModifiedDate",
)
FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


@dataclass
class ReviewOptions:
    app_id: str
    output_format: str = "csv"
    out: str | None = None
    since: str | None = None
    territory: str | None = None
    rating: int | None = None
    limit: int | None = None
    nicknames: bool = True
    raw_csv: bool = False


def csv_safe(value: Any) -> str:
    text = "" if value is None else str(value)
    return "'" + text if text.startswith(FORMULA_PREFIXES) else text


def fetch(ctx: Context, opts: ReviewOptions) -> list[dict[str, Any]]:
    client = ctx.client()
    params: dict[str, Any] = {
        "sort": "-createdDate",
        "include": "response",
        "fields[customerReviews]": "rating,title,body,reviewerNickname,createdDate,territory,response",
        "fields[customerReviewResponses]": "responseBody,lastModifiedDate,state",
    }
    if opts.territory:
        params["filter[territory]"] = opts.territory.upper()
    if opts.rating is not None:
        params["filter[rating]"] = str(opts.rating)
    rows: list[dict[str, Any]] = []
    for page in client.pages(f"/v1/apps/{opts.app_id}/customerReviews", params):
        responses = {
            str(item.get("id")): attrs(item)
            for item in page.get("included") or []
            if isinstance(item, dict) and item.get("type") == "customerReviewResponses"
        }
        for review in page.get("data") or []:
            a = attrs(review)
            created = str(a.get("createdDate") or "")
            if opts.since and created[:10] < opts.since:
                return rows
            link = ((review.get("relationships") or {}).get("response") or {}).get("data") or {}
            response = responses.get(str(link.get("id"))) if isinstance(link, dict) else None
            rows.append(
                {
                    "id": review.get("id"),
                    "createdDate": created,
                    "rating": a.get("rating"),
                    "territory": a.get("territory"),
                    "title": a.get("title"),
                    "body": a.get("body"),
                    "reviewerNickname": a.get("reviewerNickname") if opts.nicknames else None,
                    "responseBody": (response or {}).get("responseBody"),
                    "responseState": (response or {}).get("state"),
                    "responseLastModifiedDate": (response or {}).get("lastModifiedDate"),
                }
            )
            if opts.limit is not None and len(rows) >= opts.limit:
                return rows
    return rows


def write(
    rows: Iterable[dict[str, Any]], output_format: str, stream: TextIO, raw_csv: bool = False, nicknames: bool = True
) -> None:
    columns = [c for c in COLUMNS if nicknames or c != "reviewerNickname"]
    if output_format == "csv":
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(columns)
        for row in rows:
            writer.writerow([row.get(c) if raw_csv else csv_safe(row.get(c)) for c in columns])
    elif output_format == "jsonl":
        for row in rows:
            stream.write(json.dumps({c: row.get(c) for c in columns}, ensure_ascii=False) + "\n")
    else:
        json.dump([{c: row.get(c) for c in columns} for row in rows], stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def export(ctx: Context, opts: ReviewOptions) -> dict[str, Any]:
    if opts.output_format not in FORMATS:
        raise UsageError(f"--format must be one of {', '.join(FORMATS)}.")
    if opts.rating is not None and opts.rating not in range(1, 6):
        raise UsageError("--rating must be 1 to 5.")
    if opts.since and len(opts.since) != 10:
        raise UsageError("--since must be YYYY-MM-DD.")
    rows = fetch(ctx, opts)
    if opts.out:
        buffer = io.StringIO()
        write(rows, opts.output_format, buffer, opts.raw_csv, opts.nicknames)
        # Reviews carry reviewer nicknames: keep the export private to this user.
        fd = open_private(opts.out)
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(buffer.getvalue())
        print(f"{len(rows)} review(s) -> {opts.out}", file=sys.stderr)
    else:
        write(rows, opts.output_format, sys.stdout, opts.raw_csv, opts.nicknames)
    return {"reviews": len(rows), "out": opts.out}
