"""An in-process mock of the App Store Connect API for offline tests.

It implements just enough JSON:API behaviour for the kit's commands, checks every
request's bearer token (structure, claims and, when a verifier is given, the ES256
signature), and has switches that reproduce behaviour observed on the real
service:

* ``auto_version_localization``: creating an App Info localization also creates the
  version localization, so a later POST for it answers 409;
* ``gc_off_on_build_change``: attaching a different build switches the version's
  Game Center link off;
* ``fail(...)``: inject an error, e.g. a 500 when adding the app version to a
  review submission.

A second server on another port plays the pre-signed download host, so tests can
prove the API token never reaches it. All identifiers are fake placeholders.
"""

from __future__ import annotations

import base64
import gzip
import hashlib
import itertools
import json
import re
import threading
import time
import urllib.parse
from collections import defaultdict
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

APP_ID = "1234567890"
BUNDLE_ID = "com.example.mygame"
JSON_HEADERS = {"Content-Type": "application/json", "X-Rate-Limit": "user-hour-lim:3600;user-hour-rem:3500;"}

Reply = tuple[int, dict[str, str], bytes]


def _b64url_json(segment: str) -> dict[str, Any]:
    return json.loads(base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4)))


def reply(doc: Any, status: int = 200) -> Reply:
    return status, dict(JSON_HEADERS), json.dumps(doc).encode("utf-8")


def error(status: int, code: str, detail: str) -> Reply:
    return reply({"errors": [{"status": str(status), "code": code, "title": code, "detail": detail}]}, status)


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args: Any) -> None:  # keep test output clean
        pass

    def _handle(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        status, headers, payload = self.server.app.dispatch(self.command, self.path, self.headers, body)  # type: ignore[attr-defined]
        self.send_response(status)
        for key, value in headers.items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        if payload:
            self.wfile.write(payload)

    do_GET = do_POST = do_PATCH = do_DELETE = _handle


class _Server:
    def __init__(self, app: Any) -> None:
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.httpd.app = app  # type: ignore[attr-defined]
        self.thread = threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
        self.thread.start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


class FileHost:
    """Plays the pre-signed download host (a different origin from the API)."""

    def __init__(self) -> None:
        self.blobs: dict[str, bytes] = {}
        self.requests: list[dict[str, Any]] = []
        self._server = _Server(self)

    @property
    def url(self) -> str:
        return self._server.url

    def put(self, path: str, data: bytes) -> str:
        self.blobs[path] = data
        return self.url + path

    def dispatch(self, method: str, raw_path: str, headers: Any, body: bytes) -> Reply:
        path = urllib.parse.urlsplit(raw_path).path
        self.requests.append({"method": method, "path": path, "authorization": headers.get("Authorization")})
        if path in self.blobs:
            return 200, {"Content-Type": "application/octet-stream"}, self.blobs[path]
        return 404, {"Content-Type": "text/plain"}, b"not found"

    def close(self) -> None:
        self._server.close()


class MockASC:
    def __init__(
        self,
        verifier: Callable[[str], bool] | None = None,
        key_id: str | None = None,
        issuer_id: str | None = None,
    ) -> None:
        self.lock = threading.RLock()
        self._ids = itertools.count(1)
        self.db: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
        self.requests: list[dict[str, Any]] = []
        self.faults: list[dict[str, Any]] = []
        self.verifier = verifier
        self.key_id = key_id
        self.issuer_id = issuer_id
        self.auto_version_localization = True
        self.gc_off_on_build_change = True
        self.sales: dict[tuple[str, str], bytes] = {}
        self.max_page = 200  # lower it to force pagination
        self.files = FileHost()
        self._server = _Server(self)
        self.routes = self._routes()

    @property
    def url(self) -> str:
        return self._server.url

    def close(self) -> None:
        self._server.close()
        self.files.close()

    # ------------------------------------------------------------------ data helpers
    def new_id(self, prefix: str) -> str:
        return f"{prefix}-{next(self._ids):04d}"

    def add(
        self, type_: str, attributes: dict[str, Any], rels: dict[str, Any] | None = None, id_: str | None = None
    ) -> str:
        rid = id_ or self.new_id(type_[:6].lower())
        self.db[type_][rid] = {"attributes": dict(attributes), "rels": dict(rels or {})}
        return rid

    def attrs(self, type_: str, rid: str) -> dict[str, Any]:
        return self.db[type_][rid]["attributes"]

    def children(self, type_: str, rel_name: str, parent_id: str) -> list[str]:
        out = []
        for rid, res in self.db[type_].items():
            link = res["rels"].get(rel_name)
            if isinstance(link, tuple) and link[1] == parent_id:
                out.append(rid)
        return out

    def serialize(self, type_: str, rid: str) -> dict[str, Any]:
        res = self.db[type_][rid]
        rels: dict[str, Any] = {}
        for name, link in res["rels"].items():
            if isinstance(link, list):
                rels[name] = {"data": [{"type": t, "id": i} for t, i in link]}
            elif link is None:
                rels[name] = {"data": None}
            else:
                rels[name] = {"data": {"type": link[0], "id": link[1]}}
        return {
            "type": type_,
            "id": rid,
            "attributes": {k: v for k, v in res["attributes"].items() if not k.startswith("_")},
            "relationships": rels,
            "links": {"self": f"{self.url}/v1/{type_}/{rid}"},
        }

    def page(self, type_: str, ids: list[str], query: dict[str, str], path: str, included: list | None = None) -> Reply:
        limit = min(int(query.get("limit", "50")), self.max_page)
        cursor = int(query.get("cursor", "0"))
        chunk = ids[cursor : cursor + limit]
        doc: dict[str, Any] = {
            "data": [self.serialize(type_, rid) for rid in chunk],
            "links": {"self": f"{self.url}{path}"},
            "meta": {"paging": {"total": len(ids), "limit": limit}},
        }
        if included is not None:
            doc["included"] = included
        if cursor + limit < len(ids):
            nxt = dict(query)
            nxt["cursor"] = str(cursor + limit)
            doc["links"]["next"] = f"{self.url}{path}?{urllib.parse.urlencode(nxt)}"
        return reply(doc)

    def one(self, type_: str, rid: str | None) -> Reply:
        if rid is None or rid not in self.db[type_]:
            return reply({"data": None})
        return reply({"data": self.serialize(type_, rid)})

    # ------------------------------------------------------------------ test controls
    def fail(
        self,
        method: str,
        path_regex: str,
        status: int = 500,
        code: str = "UNEXPECTED_ERROR",
        times: int = 1,
        detail: str = "An unexpected error occurred.",
        headers: dict[str, str] | None = None,
    ) -> None:
        self.faults.append(
            {
                "method": method,
                "rx": re.compile(path_regex),
                "status": status,
                "code": code,
                "times": times,
                "detail": detail,
                "headers": headers or {},
            }
        )

    def writes(self) -> list[dict[str, Any]]:
        return [r for r in self.requests if r["method"] != "GET"]

    def write_paths(self) -> list[str]:
        return [f"{r['method']} {r['path']}" for r in self.writes()]

    # ------------------------------------------------------------------ seed data
    def seed(self) -> None:
        """One live 1.0.0 app with two locales, review details, Game Center and builds."""
        self.add(
            "apps",
            {"name": "Example Game", "bundleId": BUNDLE_ID, "sku": "EXAMPLE-SKU", "primaryLocale": "en-US"},
            id_=APP_ID,
        )
        self.add(
            "apps",
            {"name": "Another Example", "bundleId": "com.example.other", "sku": "OTHER", "primaryLocale": "de-DE"},
            id_="1234567891",
        )
        for rid, version, number, state, crypto in (
            ("build-41", "1.0.0", "41", "VALID", False),
            ("build-42", "1.1.0", "42", "VALID", False),
            ("build-43", "1.1.0", "43", "PROCESSING", False),
            ("build-44", "1.1.0", "44", "VALID", None),
        ):
            self.add(
                "builds",
                {
                    "version": number,
                    "processingState": state,
                    "expired": False,
                    "usesNonExemptEncryption": crypto,
                    "uploadedDate": "2030-01-15T10:00:00-07:00",
                    "_preRelease": version,
                    "_platform": "IOS",
                },
                {"app": ("apps", APP_ID)},
                id_=rid,
            )
        self.add(
            "appStoreVersions",
            {
                "versionString": "1.0.0",
                "appStoreState": "READY_FOR_SALE",
                "appVersionState": "READY_FOR_DISTRIBUTION",
                "releaseType": "AFTER_APPROVAL",
                "platform": "IOS",
                "createdDate": "2030-01-06T10:00:00-07:00",
            },
            {"app": ("apps", APP_ID), "build": ("builds", "build-41")},
            id_="ver-live",
        )
        for locale, desc in (("en-US", "A calm tile puzzle."), ("de-DE", "Ein ruhiges Puzzle.")):
            self.add(
                "appStoreVersionLocalizations",
                {
                    "locale": locale,
                    "description": desc,
                    "keywords": "puzzle,tiles,calm",
                    "promotionalText": "Play now.",
                    "whatsNew": None,
                    "supportUrl": "https://example.com/support",
                    "marketingUrl": "https://example.com",
                },
                {"appStoreVersion": ("appStoreVersions", "ver-live")},
            )
        self.add(
            "appStoreReviewDetails",
            {
                "contactFirstName": "Jane",
                "contactLastName": "Appleseed",
                "contactPhone": "+1 555 0100",
                "contactEmail": "you@example.com",
                "demoAccountName": None,
                "demoAccountPassword": None,
                "demoAccountRequired": False,
                "notes": "Tap Play to start. No login needed.",
            },
            {"appStoreVersion": ("appStoreVersions", "ver-live")},
            id_="review-live",
        )
        self.add(
            "gameCenterAppVersions",
            {"enabled": True},
            {"appStoreVersion": ("appStoreVersions", "ver-live")},
            id_="gc-live",
        )
        self.add(
            "appInfos",
            {"state": "READY_FOR_DISTRIBUTION", "appStoreState": "READY_FOR_SALE"},
            {"app": ("apps", APP_ID)},
            id_="info-live",
        )
        for locale, name in (("en-US", "Example Game"), ("de-DE", "Beispielspiel")):
            self.add(
                "appInfoLocalizations",
                {
                    "locale": locale,
                    "name": name,
                    "subtitle": "Calm puzzles",
                    "privacyPolicyUrl": "https://example.com/privacy",
                    "privacyChoicesUrl": None,
                    "privacyPolicyText": None,
                },
                {"appInfo": ("appInfos", "info-live")},
            )

    def seed_editable_version(self, version: str = "1.1.0", build: str | None = None) -> str:
        """An editable version (as if created earlier), with copied localizations and an editable App Info."""
        vid = self._create_version(version, "IOS", None, ("builds", build) if build else None)
        return vid

    def seed_analytics(self) -> dict[str, Any]:
        payload = gzip.compress(b"Date\tApp Name\tCounts\n2030-03-01\tExample Game\t42\n")
        req = self.add(
            "analyticsReportRequests",
            {"accessType": "ONGOING", "stoppedDueToInactivity": False},
            {"app": ("apps", APP_ID)},
            id_="req-ongoing",
        )
        rep = self.add(
            "analyticsReports",
            {"name": "App Store Discovery and Engagement Standard", "category": "APP_STORE_ENGAGEMENT"},
            {"request": ("analyticsReportRequests", req)},
            id_="rep-engagement",
        )
        self.add(
            "analyticsReports",
            {"name": "App Sessions Standard", "category": "APP_USAGE"},
            {"request": ("analyticsReportRequests", req)},
            id_="rep-sessions",
        )
        out = {"payload": payload, "instances": []}
        for day in ("2030-02-27", "2030-03-01"):
            inst = self.add(
                "analyticsReportInstances",
                {"granularity": "DAILY", "processingDate": day},
                {"report": ("analyticsReports", rep)},
                id_=f"inst-{day}",
            )
            url = self.files.put(f"/segments/{day}.gz", payload)
            self.add(
                "analyticsReportSegments",
                {"url": url, "checksum": hashlib.md5(payload).hexdigest(), "sizeInBytes": len(payload)},
                {"instance": ("analyticsReportInstances", inst)},
                id_=f"seg-{day}",
            )
            out["instances"].append(inst)
        return out

    def seed_reviews(self, count: int = 5) -> None:
        for n in range(count):
            created = f"2030-02-{20 + n:02d}T12:00:00-07:00"
            rid = self.add(
                "customerReviews",
                {
                    "rating": 5 - (n % 5),
                    "title": f"Review {n}",
                    "body": "Great game" if n else '=HYPERLINK("http://example.com")',
                    "reviewerNickname": f"player{n}",
                    "createdDate": created,
                    "territory": "USA" if n % 2 else "DEU",
                },
                {"app": ("apps", APP_ID)},
                id_=f"review-{n}",
            )
            if n == 1:
                resp = self.add(
                    "customerReviewResponses",
                    {"responseBody": "Thanks!", "state": "PUBLISHED", "lastModifiedDate": "2030-02-22T12:00:00-07:00"},
                    {"review": ("customerReviews", rid)},
                    id_="response-1",
                )
                self.db["customerReviews"][rid]["rels"]["response"] = ("customerReviewResponses", resp)

    # ------------------------------------------------------------------ auth
    def check_token(self, headers: Any) -> Reply | None:
        header = headers.get("Authorization") or ""
        if not header.startswith("Bearer "):
            return error(401, "NOT_AUTHORIZED", "Authentication credentials are missing or invalid.")
        token = header[len("Bearer ") :]
        try:
            head_b64, body_b64, _sig = token.split(".")
            head, body = _b64url_json(head_b64), _b64url_json(body_b64)
        except (ValueError, json.JSONDecodeError):
            return error(401, "NOT_AUTHORIZED", "malformed token")
        now = time.time()
        problems = []
        if head.get("alg") != "ES256" or head.get("typ") != "JWT":
            problems.append("header")
        if self.key_id and head.get("kid") != self.key_id:
            problems.append("kid")
        if body.get("aud") != "appstoreconnect-v1":
            problems.append("aud")
        if not (0 < body.get("exp", 0) - body.get("iat", 0) <= 1200) or body.get("exp", 0) < now:
            problems.append("lifetime")
        if self.issuer_id is not None and body.get("iss") != self.issuer_id:
            problems.append("iss")
        if self.issuer_id is None and body.get("sub") != "user":
            problems.append("sub")
        if self.verifier is not None and not self.verifier(token):
            problems.append("signature")
        if problems:
            return error(401, "NOT_AUTHORIZED", "token rejected: " + ",".join(problems))
        return None

    # ------------------------------------------------------------------ dispatch
    def dispatch(self, method: str, raw_path: str, headers: Any, body: bytes) -> Reply:
        parts = urllib.parse.urlsplit(raw_path)
        path = parts.path
        query = dict(urllib.parse.parse_qsl(parts.query, keep_blank_values=True))
        payload = json.loads(body) if body else None
        with self.lock:
            self.requests.append(
                {"method": method, "path": path, "query": query, "body": payload, "accept": headers.get("Accept")}
            )
            denied = self.check_token(headers)
            if denied is not None:
                return denied
            for fault in self.faults:
                if fault["times"] > 0 and fault["method"] == method and fault["rx"].search(path):
                    fault["times"] -= 1
                    status, hdrs, data = error(fault["status"], fault["code"], fault["detail"])
                    hdrs.update(fault["headers"])
                    return status, hdrs, data
            for route_method, rx, handler in self.routes:
                if route_method == method:
                    match = rx.fullmatch(path)
                    if match:
                        return handler(query=query, body=payload, path=path, **match.groupdict())
        return error(404, "NOT_FOUND", f"no mock route for {method} {path}")

    def _routes(self) -> list[tuple[str, re.Pattern[str], Callable[..., Reply]]]:
        table = [
            ("GET", r"/v1/apps", self.list_apps),
            ("GET", r"/v1/apps/(?P<app>[^/]+)", self.get_app),
            ("GET", r"/v1/builds", self.list_builds),
            ("GET", r"/v1/apps/(?P<app>[^/]+)/appStoreVersions", self.list_versions),
            ("POST", r"/v1/appStoreVersions", self.create_version),
            ("GET", r"/v1/appStoreVersions/(?P<vid>[^/]+)", self.get_version),
            ("PATCH", r"/v1/appStoreVersions/(?P<vid>[^/]+)", self.patch_version),
            ("GET", r"/v1/appStoreVersions/(?P<vid>[^/]+)/build", self.get_version_build),
            ("PATCH", r"/v1/appStoreVersions/(?P<vid>[^/]+)/relationships/build", self.patch_version_build),
            ("GET", r"/v1/appStoreVersions/(?P<vid>[^/]+)/appStoreVersionLocalizations", self.list_version_locs),
            ("POST", r"/v1/appStoreVersionLocalizations", self.create_version_loc),
            (
                "PATCH",
                r"/v1/appStoreVersionLocalizations/(?P<rid>[^/]+)",
                self.patch_simple("appStoreVersionLocalizations"),
            ),
            ("GET", r"/v1/appStoreVersions/(?P<vid>[^/]+)/appStoreReviewDetail", self.get_review_detail),
            ("POST", r"/v1/appStoreReviewDetails", self.create_review_detail),
            ("PATCH", r"/v1/appStoreReviewDetails/(?P<rid>[^/]+)", self.patch_simple("appStoreReviewDetails")),
            ("GET", r"/v1/appStoreVersions/(?P<vid>[^/]+)/gameCenterAppVersion", self.get_gc),
            ("POST", r"/v1/gameCenterAppVersions", self.create_gc),
            ("PATCH", r"/v1/gameCenterAppVersions/(?P<rid>[^/]+)", self.patch_simple("gameCenterAppVersions")),
            ("GET", r"/v1/apps/(?P<app>[^/]+)/appInfos", self.list_app_infos),
            ("GET", r"/v1/appInfos/(?P<iid>[^/]+)/appInfoLocalizations", self.list_info_locs),
            ("POST", r"/v1/appInfoLocalizations", self.create_info_loc),
            ("PATCH", r"/v1/appInfoLocalizations/(?P<rid>[^/]+)", self.patch_simple("appInfoLocalizations")),
            ("GET", r"/v1/reviewSubmissions", self.list_submissions),
            ("POST", r"/v1/reviewSubmissions", self.create_submission),
            ("GET", r"/v1/reviewSubmissions/(?P<sid>[^/]+)", self.get_submission),
            ("PATCH", r"/v1/reviewSubmissions/(?P<sid>[^/]+)", self.patch_submission),
            ("GET", r"/v1/reviewSubmissions/(?P<sid>[^/]+)/items", self.list_items),
            ("POST", r"/v1/reviewSubmissionItems", self.create_item),
            ("GET", r"/v1/apps/(?P<app>[^/]+)/analyticsReportRequests", self.list_report_requests),
            ("POST", r"/v1/analyticsReportRequests", self.create_report_request),
            (
                "GET",
                r"/v1/analyticsReportRequests/(?P<rid>[^/]+)",
                lambda rid, **_: self.one("analyticsReportRequests", rid),
            ),
            ("GET", r"/v1/analyticsReportRequests/(?P<rid>[^/]+)/reports", self.list_reports),
            ("GET", r"/v1/analyticsReports/(?P<rid>[^/]+)/instances", self.list_instances),
            ("GET", r"/v1/analyticsReportInstances/(?P<rid>[^/]+)/segments", self.list_segments),
            ("GET", r"/v1/salesReports", self.sales_report),
            ("GET", r"/v1/apps/(?P<app>[^/]+)/customerReviews", self.list_reviews),
            ("GET", r"/v1/test/redirect", self.redirect_elsewhere),
            ("GET", r"/v1/test/cross-origin", self.cross_origin_next),
        ]
        return [(m, re.compile(rx), fn) for m, rx, fn in table]

    # ------------------------------------------------------------------ handlers: apps/builds
    def list_apps(self, query: dict[str, str], path: str, **_: Any) -> Reply:
        return self.page("apps", sorted(self.db["apps"]), query, path)

    def get_app(self, app: str, **_: Any) -> Reply:
        if app not in self.db["apps"]:
            return error(404, "NOT_FOUND", "There is no resource of type 'apps' with id given.")
        return self.one("apps", app)

    def list_builds(self, query: dict[str, str], path: str, **_: Any) -> Reply:
        if "filter[app]" not in query:
            return error(400, "PARAMETER_ERROR.REQUIRED", "filter[app] is required in this mock")
        ids = []
        for rid, res in self.db["builds"].items():
            a = res["attributes"]
            if res["rels"]["app"][1] != query["filter[app]"]:
                continue
            if "filter[version]" in query and a["version"] != query["filter[version]"]:
                continue
            if (
                "filter[preReleaseVersion.version]" in query
                and a["_preRelease"] != query["filter[preReleaseVersion.version]"]
            ):
                continue
            if (
                "filter[preReleaseVersion.platform]" in query
                and a["_platform"] != query["filter[preReleaseVersion.platform]"]
            ):
                continue
            ids.append(rid)
        return self.page("builds", ids, query, path)

    # ------------------------------------------------------------------ handlers: versions
    def list_versions(self, app: str, query: dict[str, str], path: str, **_: Any) -> Reply:
        ids = [
            rid
            for rid in self.children("appStoreVersions", "app", app)
            if query.get("filter[platform]", "IOS") == self.attrs("appStoreVersions", rid)["platform"]
            and query.get("filter[versionString]", self.attrs("appStoreVersions", rid)["versionString"])
            == self.attrs("appStoreVersions", rid)["versionString"]
        ]
        return self.page("appStoreVersions", ids, query, path)

    def _create_version(self, version: str, platform: str, release_type: str | None, build: tuple | None) -> str:
        live = [
            rid
            for rid, res in self.db["appStoreVersions"].items()
            if res["attributes"]["appVersionState"] == "READY_FOR_DISTRIBUTION"
        ]
        vid = self.add(
            "appStoreVersions",
            {
                "versionString": version,
                "appStoreState": "PREPARE_FOR_SUBMISSION",
                "appVersionState": "PREPARE_FOR_SUBMISSION",
                "releaseType": release_type or "AFTER_APPROVAL",
                "platform": platform,
                "earliestReleaseDate": None,
                "createdDate": "2030-03-01T10:00:00-07:00",
            },
            {"app": ("apps", APP_ID), "build": build},
        )
        if live:
            for rid in self.children("appStoreVersionLocalizations", "appStoreVersion", live[0]):
                copied = dict(self.attrs("appStoreVersionLocalizations", rid))
                copied["whatsNew"] = None
                self.add("appStoreVersionLocalizations", copied, {"appStoreVersion": ("appStoreVersions", vid)})
        info = self.add(
            "appInfos",
            {"state": "PREPARE_FOR_SUBMISSION", "appStoreState": "PREPARE_FOR_SUBMISSION"},
            {"app": ("apps", APP_ID)},
            id_=f"info-{vid}",
        )
        for rid in self.children("appInfoLocalizations", "appInfo", "info-live"):
            self.add(
                "appInfoLocalizations", dict(self.attrs("appInfoLocalizations", rid)), {"appInfo": ("appInfos", info)}
            )
        return vid

    def create_version(self, body: dict[str, Any], **_: Any) -> Reply:
        data = body["data"]
        a = data["attributes"]
        for res in self.db["appStoreVersions"].values():
            if res["attributes"]["versionString"] == a["versionString"]:
                return error(409, "ENTITY_ERROR.ATTRIBUTE.INVALID.DUPLICATE", "version already exists")
            if res["attributes"]["appVersionState"] in ("PREPARE_FOR_SUBMISSION", "WAITING_FOR_REVIEW", "IN_REVIEW"):
                return error(409, "ENTITY_ERROR", "another version is in progress")
        build_rel = ((data.get("relationships") or {}).get("build") or {}).get("data")
        vid = self._create_version(
            a["versionString"], a["platform"], a.get("releaseType"), ("builds", build_rel["id"]) if build_rel else None
        )
        return reply({"data": self.serialize("appStoreVersions", vid)}, 201)

    def get_version(self, vid: str, **_: Any) -> Reply:
        if vid not in self.db["appStoreVersions"]:
            return error(404, "NOT_FOUND", "no such version")
        return self.one("appStoreVersions", vid)

    def patch_version(self, vid: str, body: dict[str, Any], **_: Any) -> Reply:
        self.attrs("appStoreVersions", vid).update(body["data"].get("attributes") or {})
        return self.one("appStoreVersions", vid)

    def get_version_build(self, vid: str, **_: Any) -> Reply:
        link = self.db["appStoreVersions"][vid]["rels"].get("build")
        return self.one("builds", link[1] if link else None)

    def patch_version_build(self, vid: str, body: dict[str, Any], **_: Any) -> Reply:
        new = body["data"]["id"]
        old = self.db["appStoreVersions"][vid]["rels"].get("build")
        self.db["appStoreVersions"][vid]["rels"]["build"] = ("builds", new)
        if self.gc_off_on_build_change and old and old[1] != new:
            for gid in self.children("gameCenterAppVersions", "appStoreVersion", vid):
                self.attrs("gameCenterAppVersions", gid)["enabled"] = False
        return 204, {}, b""

    def list_version_locs(self, vid: str, query: dict[str, str], path: str, **_: Any) -> Reply:
        return self.page(
            "appStoreVersionLocalizations",
            self.children("appStoreVersionLocalizations", "appStoreVersion", vid),
            query,
            path,
        )

    def create_version_loc(self, body: dict[str, Any], **_: Any) -> Reply:
        data = body["data"]
        vid = data["relationships"]["appStoreVersion"]["data"]["id"]
        locale = data["attributes"]["locale"]
        for rid in self.children("appStoreVersionLocalizations", "appStoreVersion", vid):
            if self.attrs("appStoreVersionLocalizations", rid)["locale"] == locale:
                return error(409, "ENTITY_ERROR.ATTRIBUTE.INVALID", f"localization {locale} already exists")
        rid = self.add(
            "appStoreVersionLocalizations", data["attributes"], {"appStoreVersion": ("appStoreVersions", vid)}
        )
        return reply({"data": self.serialize("appStoreVersionLocalizations", rid)}, 201)

    def patch_simple(self, type_: str) -> Callable[..., Reply]:
        def handler(rid: str, body: dict[str, Any], **_: Any) -> Reply:
            if rid not in self.db[type_]:
                return error(404, "NOT_FOUND", "no such resource")
            self.attrs(type_, rid).update(body["data"].get("attributes") or {})
            return self.one(type_, rid)

        return handler

    # ------------------------------------------------------------------ handlers: review details, Game Center
    def get_review_detail(self, vid: str, **_: Any) -> Reply:
        ids = self.children("appStoreReviewDetails", "appStoreVersion", vid)
        return self.one("appStoreReviewDetails", ids[0] if ids else None)

    def create_review_detail(self, body: dict[str, Any], **_: Any) -> Reply:
        vid = body["data"]["relationships"]["appStoreVersion"]["data"]["id"]
        if self.children("appStoreReviewDetails", "appStoreVersion", vid):
            return error(409, "ENTITY_ERROR", "review detail exists")
        rid = self.add(
            "appStoreReviewDetails",
            body["data"].get("attributes") or {},
            {"appStoreVersion": ("appStoreVersions", vid)},
        )
        return reply({"data": self.serialize("appStoreReviewDetails", rid)}, 201)

    def get_gc(self, vid: str, **_: Any) -> Reply:
        ids = self.children("gameCenterAppVersions", "appStoreVersion", vid)
        return self.one("gameCenterAppVersions", ids[0] if ids else None)

    def create_gc(self, body: dict[str, Any], **_: Any) -> Reply:
        vid = body["data"]["relationships"]["appStoreVersion"]["data"]["id"]
        rid = self.add("gameCenterAppVersions", {"enabled": True}, {"appStoreVersion": ("appStoreVersions", vid)})
        return reply({"data": self.serialize("gameCenterAppVersions", rid)}, 201)

    # ------------------------------------------------------------------ handlers: app info
    def list_app_infos(self, app: str, query: dict[str, str], path: str, **_: Any) -> Reply:
        return self.page("appInfos", self.children("appInfos", "app", app), query, path)

    def list_info_locs(self, iid: str, query: dict[str, str], path: str, **_: Any) -> Reply:
        return self.page("appInfoLocalizations", self.children("appInfoLocalizations", "appInfo", iid), query, path)

    def create_info_loc(self, body: dict[str, Any], **_: Any) -> Reply:
        data = body["data"]
        iid = data["relationships"]["appInfo"]["data"]["id"]
        locale = data["attributes"]["locale"]
        for rid in self.children("appInfoLocalizations", "appInfo", iid):
            if self.attrs("appInfoLocalizations", rid)["locale"] == locale:
                return error(409, "ENTITY_ERROR", "exists")
        rid = self.add("appInfoLocalizations", data["attributes"], {"appInfo": ("appInfos", iid)})
        if self.auto_version_localization:
            # Observed: App Store Connect adds the version localization by itself.
            for vid, res in self.db["appStoreVersions"].items():
                if res["attributes"]["appVersionState"] in ("PREPARE_FOR_SUBMISSION", "READY_FOR_REVIEW"):
                    have = {
                        self.attrs("appStoreVersionLocalizations", x)["locale"]
                        for x in self.children("appStoreVersionLocalizations", "appStoreVersion", vid)
                    }
                    if locale not in have:
                        self.add(
                            "appStoreVersionLocalizations",
                            {
                                "locale": locale,
                                "description": None,
                                "keywords": None,
                                "promotionalText": None,
                                "whatsNew": None,
                                "supportUrl": None,
                                "marketingUrl": None,
                            },
                            {"appStoreVersion": ("appStoreVersions", vid)},
                        )
        return reply({"data": self.serialize("appInfoLocalizations", rid)}, 201)

    # ------------------------------------------------------------------ handlers: review submissions
    def list_submissions(self, query: dict[str, str], path: str, **_: Any) -> Reply:
        if "filter[app]" not in query:
            return error(400, "PARAMETER_ERROR.REQUIRED", "filter[app] is required")
        states = set(query["filter[state]"].split(",")) if query.get("filter[state]") else None
        ids = [
            rid
            for rid in self.children("reviewSubmissions", "app", query["filter[app]"])
            if (states is None or self.attrs("reviewSubmissions", rid)["state"] in states)
            and self.attrs("reviewSubmissions", rid)["platform"] == query.get("filter[platform]", "IOS")
        ]
        return self.page("reviewSubmissions", ids, query, path)

    def create_submission(self, body: dict[str, Any], **_: Any) -> Reply:
        data = body["data"]
        app = data["relationships"]["app"]["data"]["id"]
        rid = self.add(
            "reviewSubmissions",
            {"platform": data["attributes"]["platform"], "state": "READY_FOR_REVIEW", "submittedDate": None},
            {"app": ("apps", app)},
        )
        return reply({"data": self.serialize("reviewSubmissions", rid)}, 201)

    def get_submission(self, sid: str, **_: Any) -> Reply:
        if sid not in self.db["reviewSubmissions"]:
            return error(404, "NOT_FOUND", "no such submission")
        return self.one("reviewSubmissions", sid)

    def patch_submission(self, sid: str, body: dict[str, Any], **_: Any) -> Reply:
        a = body["data"].get("attributes") or {}
        sub = self.attrs("reviewSubmissions", sid)
        if a.get("submitted"):
            items = [
                i
                for i in self.children("reviewSubmissionItems", "reviewSubmission", sid)
                if self.attrs("reviewSubmissionItems", i)["state"] not in ("REMOVED",)
            ]
            if not items:
                return error(409, "STATE_ERROR", "the submission has no items")
            sub["state"] = "WAITING_FOR_REVIEW"
            sub["submittedDate"] = "2030-03-03T10:00:00-07:00"
            for item in items:
                link = self.db["reviewSubmissionItems"][item]["rels"].get("appStoreVersion")
                if link:
                    self.attrs("appStoreVersions", link[1]).update(
                        appStoreState="WAITING_FOR_REVIEW", appVersionState="WAITING_FOR_REVIEW"
                    )
        return self.one("reviewSubmissions", sid)

    def list_items(self, sid: str, query: dict[str, str], path: str, **_: Any) -> Reply:
        return self.page(
            "reviewSubmissionItems", self.children("reviewSubmissionItems", "reviewSubmission", sid), query, path
        )

    def create_item(self, body: dict[str, Any], **_: Any) -> Reply:
        rels = body["data"]["relationships"]
        sid = rels["reviewSubmission"]["data"]["id"]
        target = next((name, value["data"]) for name, value in rels.items() if name != "reviewSubmission")
        for rid in self.children("reviewSubmissionItems", "reviewSubmission", sid):
            link = self.db["reviewSubmissionItems"][rid]["rels"].get(target[0])
            if link and link[1] == target[1]["id"]:
                return error(409, "ENTITY_ERROR", "item already in submission")
        rid = self.add(
            "reviewSubmissionItems",
            {"state": "READY_FOR_REVIEW"},
            {"reviewSubmission": ("reviewSubmissions", sid), target[0]: (target[1]["type"], target[1]["id"])},
        )
        if target[0] == "appStoreVersion":
            self.attrs("appStoreVersions", target[1]["id"]).update(
                appStoreState="READY_FOR_REVIEW", appVersionState="READY_FOR_REVIEW"
            )
        return reply({"data": self.serialize("reviewSubmissionItems", rid)}, 201)

    # ------------------------------------------------------------------ handlers: analytics
    def list_report_requests(self, app: str, query: dict[str, str], path: str, **_: Any) -> Reply:
        ids = [
            rid
            for rid in self.children("analyticsReportRequests", "app", app)
            if query.get("filter[accessType]", self.attrs("analyticsReportRequests", rid)["accessType"])
            == self.attrs("analyticsReportRequests", rid)["accessType"]
        ]
        return self.page("analyticsReportRequests", ids, query, path)

    def create_report_request(self, body: dict[str, Any], **_: Any) -> Reply:
        data = body["data"]
        app = data["relationships"]["app"]["data"]["id"]
        access = data["attributes"]["accessType"]
        for rid in self.children("analyticsReportRequests", "app", app):
            a = self.attrs("analyticsReportRequests", rid)
            if a["accessType"] == access == "ONGOING" and not a["stoppedDueToInactivity"]:
                return error(409, "ENTITY_ERROR", "an ongoing request exists")
        rid = self.add(
            "analyticsReportRequests", {"accessType": access, "stoppedDueToInactivity": False}, {"app": ("apps", app)}
        )
        return reply({"data": self.serialize("analyticsReportRequests", rid)}, 201)

    def list_reports(self, rid: str, query: dict[str, str], path: str, **_: Any) -> Reply:
        ids = [
            x
            for x in self.children("analyticsReports", "request", rid)
            if query.get("filter[category]", self.attrs("analyticsReports", x)["category"])
            == self.attrs("analyticsReports", x)["category"]
        ]
        return self.page("analyticsReports", ids, query, path)

    def list_instances(self, rid: str, query: dict[str, str], path: str, **_: Any) -> Reply:
        ids = [
            x
            for x in self.children("analyticsReportInstances", "report", rid)
            if query.get("filter[granularity]", "DAILY") == self.attrs("analyticsReportInstances", x)["granularity"]
            and query.get("filter[processingDate]", self.attrs("analyticsReportInstances", x)["processingDate"])
            == self.attrs("analyticsReportInstances", x)["processingDate"]
        ]
        return self.page("analyticsReportInstances", ids, query, path)

    def list_segments(self, rid: str, query: dict[str, str], path: str, **_: Any) -> Reply:
        return self.page(
            "analyticsReportSegments", self.children("analyticsReportSegments", "instance", rid), query, path
        )

    # ------------------------------------------------------------------ handlers: sales, reviews
    def sales_report(self, query: dict[str, str], **_: Any) -> Reply:
        required = ("filter[frequency]", "filter[reportType]", "filter[reportSubType]", "filter[vendorNumber]")
        if any(k not in query for k in required):
            return error(400, "PARAMETER_ERROR.REQUIRED", "missing filters")
        key = (query["filter[frequency]"], query.get("filter[reportDate]", "latest"))
        if key not in self.sales:
            return error(404, "NOT_FOUND", "There were no sales for the date specified.")
        return 200, {"Content-Type": "application/a-gzip"}, self.sales[key]

    def list_reviews(self, app: str, query: dict[str, str], path: str, **_: Any) -> Reply:
        ids = [
            rid
            for rid in self.children("customerReviews", "app", app)
            if query.get("filter[territory]", self.attrs("customerReviews", rid)["territory"])
            == self.attrs("customerReviews", rid)["territory"]
            and query.get("filter[rating]", str(self.attrs("customerReviews", rid)["rating"]))
            == str(self.attrs("customerReviews", rid)["rating"])
        ]
        ids.sort(
            key=lambda rid: self.attrs("customerReviews", rid)["createdDate"],
            reverse=query.get("sort") == "-createdDate",
        )
        limit = min(int(query.get("limit", "50")), self.max_page)
        cursor = int(query.get("cursor", "0"))
        page_ids = ids[cursor : cursor + limit]
        included = []
        if "response" in query.get("include", ""):
            for rid in page_ids:
                link = self.db["customerReviews"][rid]["rels"].get("response")
                if link:
                    included.append(self.serialize("customerReviewResponses", link[1]))
        return self.page("customerReviews", ids, query, path, included=included)

    # ------------------------------------------------------------------ handlers: security probes
    def redirect_elsewhere(self, **_: Any) -> Reply:
        location = self.files.put("/landing", b'{"data": []}')
        return 302, {"Location": location, "Content-Type": "text/plain"}, b""

    def cross_origin_next(self, query: dict[str, str], path: str, **_: Any) -> Reply:
        return reply({"data": [], "links": {"self": self.url + path, "next": self.files.url + "/v1/apps?cursor=1"}})
