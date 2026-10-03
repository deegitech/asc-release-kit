"""A small App Store Connect API client built on ``urllib``.

Safety properties:

* the bearer token is only ever sent to the configured API origin, which must be
  ``https://*.apple.com``; loopback addresses (local mock servers) need
  ``ASC_ALLOW_INSECURE_LOOPBACK=1`` in the environment;
* the ``Authorization`` header is attached as an *unredirected* header, so a
  redirect can never carry it to another host;
* pagination links are followed only when they stay on the same origin;
* pre-signed download URLs (analytics segments) are fetched without the token;
* a client created for a plan (``read_only``) refuses POST, PATCH and DELETE;
* reads are retried on 429/5xx with backoff, writes only on 429. A write that
  fails with a 5xx or a network error is never repeated: it may have happened, so
  the command stops (or re-reads, in the review-submission steps);
* every write is journaled with personal details redacted, including writes that
  got no answer.
"""

from __future__ import annotations

import http.client
import json
import os
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any

from . import __version__
from .config import DEFAULT_BASE_URL
from .errors import ApiError, SecurityError, TransportError, UsageError
from .hints import NETWORK_FIX, NETWORK_WRITE_FIX, api_hint
from .journal import Journal
from .redact import Redactor

USER_AGENT = f"asc-release-kit/{__version__} (+https://github.com/deegitech/asc-release-kit)"
WRITE_METHODS = frozenset({"POST", "PATCH", "DELETE"})
RETRYABLE_READ_STATUSES = frozenset({429, 500, 502, 503, 504})
LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
#: Environment switch that lets local mock servers (loopback addresses) receive tokens.
LOOPBACK_ENV = "ASC_ALLOW_INSECURE_LOOPBACK"
MAX_BACKOFF_SECONDS = 60.0

# Indirection so tests can make retries instant.
_sleep: Callable[[float], None] = time.sleep


def loopback_allowed(env: Mapping[str, str] | None = None) -> bool:
    """True when ``ASC_ALLOW_INSECURE_LOOPBACK=1``: only for local mock servers and tests."""
    return (os.environ if env is None else env).get(LOOPBACK_ENV) == "1"


def check_base_url(url: str, *, allow_loopback: bool | None = None) -> str:
    """Only Apple's HTTPS hosts may receive the token (loopback too, when explicitly allowed)."""
    if allow_loopback is None:
        allow_loopback = loopback_allowed()
    parts = urllib.parse.urlsplit(url)
    host = (parts.hostname or "").lower()
    try:
        parts.port  # noqa: B018 - raises ValueError for a malformed port
    except ValueError:
        raise UsageError("The API base URL has an invalid port.") from None
    apple = parts.scheme == "https" and (host == "apple.com" or host.endswith(".apple.com"))
    loopback = allow_loopback and parts.scheme in ("http", "https") and host in LOOPBACK_HOSTS
    if not (apple or loopback):
        hint = ""
        if host in LOOPBACK_HOSTS:
            hint = f" Loopback addresses are for local mock servers only and need {LOOPBACK_ENV}=1."
        raise UsageError(
            f"Refusing to send App Store Connect credentials to {parts.scheme or '?'}://{host or '?'}: "
            "the API base URL must be an https://*.apple.com URL." + hint
        )
    if parts.path.rstrip("/") or parts.query or parts.fragment or parts.username or parts.password:
        raise UsageError(
            "ASC_API_BASE_URL must be scheme://host[:port] only, without a path such as /v1 "
            "(the kit adds /v1/... itself)."
        )
    return url.rstrip("/")


def _origin(url: str) -> tuple[str, str, int]:
    parts = urllib.parse.urlsplit(url)
    default_port = 443 if parts.scheme == "https" else 80
    return parts.scheme, (parts.hostname or "").lower(), parts.port or default_port


def _path_only(url: str) -> str:
    return urllib.parse.urlsplit(url).path


def _param(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, tuple, set, frozenset)):
        return ",".join(str(item) for item in value)
    return str(value)


def rel(type_: str, id_: str) -> dict[str, Any]:
    """A JSON:API to-one relationship body."""
    return {"data": {"type": type_, "id": id_}}


@dataclass
class Response:
    status: int
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes = b""

    def header(self, name: str) -> str | None:
        return self.headers.get(name.lower())

    def json(self) -> Any:
        if not self.body:
            return {}
        try:
            return json.loads(self.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return {}


class AscClient:
    """JSON:API helper for App Store Connect. ``token`` is called for every request."""

    def __init__(
        self,
        token: Callable[[], str],
        *,
        base_url: str = DEFAULT_BASE_URL,
        journal: Journal | None = None,
        redactor: Redactor | None = None,
        timeout: float = 60.0,
        max_retries: int = 4,
        verbose: Callable[[str], None] | None = None,
        opener: urllib.request.OpenerDirector | None = None,
        read_only: bool = False,
        allow_loopback: bool | None = None,
    ) -> None:
        self.allow_loopback = loopback_allowed() if allow_loopback is None else allow_loopback
        self.base_url = check_base_url(base_url, allow_loopback=self.allow_loopback)
        self._origin = _origin(self.base_url)
        #: Plan mode: POST, PATCH and DELETE are refused before anything is sent.
        self.read_only = read_only
        self._token = token
        self.journal = journal
        self.redactor = redactor or Redactor()
        self.timeout = timeout
        self.max_retries = max_retries
        self._verbose = verbose
        self._opener = opener or urllib.request.build_opener()
        self.requests = 0
        self.writes = 0
        self.last_rate_limit: str | None = None
        #: the ``Date`` header of the latest answer (``doctor`` compares it with the local clock)
        self.last_server_date: str | None = None

    # -- URLs -------------------------------------------------------------------------------
    def url_for(self, path: str, params: Mapping[str, Any] | None = None) -> str:
        if path.startswith(("http://", "https://")):
            if _origin(path) != self._origin:
                raise SecurityError("Refusing to follow a link to another host with App Store Connect credentials.")
            url = path
        else:
            if not path.startswith("/"):
                path = "/" + path
            if not path.startswith(("/v1/", "/v2/", "/v3/")):
                raise ValueError(f"API paths must start with a version, e.g. /v1/apps (got {path!r})")
            url = self.base_url + path
        if params:
            pairs = [(key, _param(value)) for key, value in params.items() if value is not None]
            if pairs:
                query = urllib.parse.urlencode(pairs, safe="[],:")
                url += ("&" if "?" in url else "?") + query
        return url

    # -- core -------------------------------------------------------------------------------
    def _backoff(self, attempt: int, retry_after: str | None) -> float:
        if retry_after:
            try:
                return min(float(retry_after), MAX_BACKOFF_SECONDS)
            except ValueError:
                pass
        return min(2.0**attempt, 30.0) + random.uniform(0, 0.5)

    def request(
        self,
        method: str,
        path: str,
        params: Mapping[str, Any] | None = None,
        body: Any = None,
        *,
        accept: str = "application/json",
        expect: tuple[int, ...] = (200, 201, 204),
    ) -> Response:
        method = method.upper()
        if self.read_only and method in WRITE_METHODS:
            raise SecurityError(
                f"Refusing {method} {_path_only(path)}: this command runs as a plan (no --apply), and plans never write."
            )
        url = self.url_for(path, params)
        data = json.dumps(body).encode("utf-8") if body is not None else None
        attempt = 0
        while True:
            started = time.monotonic()
            req = urllib.request.Request(url, data=data, method=method)
            req.add_header("Accept", accept)
            req.add_header("User-Agent", USER_AGENT)
            if data is not None:
                req.add_header("Content-Type", "application/json")
            # Unredirected: urllib will not copy it onto a redirected request.
            req.add_unredirected_header("Authorization", "Bearer " + self._token())
            try:
                with self._opener.open(req, timeout=self.timeout) as resp:
                    response = Response(resp.status, {k.lower(): v for k, v in resp.headers.items()}, resp.read())
            except urllib.error.HTTPError as exc:
                headers = {k.lower(): v for k, v in exc.headers.items()} if exc.headers else {}
                try:
                    payload = exc.read() or b""
                except OSError:
                    payload = b""
                response = Response(exc.code, headers, payload)
            except (OSError, http.client.HTTPException) as exc:  # URLError, timeouts, resets, TLS errors
                if method == "GET" and attempt < self.max_retries:
                    attempt += 1
                    _sleep(self._backoff(attempt, None))
                    continue
                if method in WRITE_METHODS:
                    # The request may have reached Apple and taken effect: record it, never repeat it.
                    self.writes += 1
                    self._journal_write(method, url, body, None, error="no response; the write may have been applied")
                reason = getattr(exc, "reason", exc)
                raise TransportError(
                    f"{method} {_path_only(url)}: network error: {self.redactor.text(str(reason))}"
                    + (
                        " The write may or may not have been applied; re-run to re-read and continue."
                        if method in WRITE_METHODS
                        else ""
                    ),
                    fix=NETWORK_WRITE_FIX if method in WRITE_METHODS else NETWORK_FIX,
                ) from None
            self.requests += 1
            if response.header("x-rate-limit"):
                self.last_rate_limit = response.header("x-rate-limit")
            if response.header("date"):
                self.last_server_date = response.header("date")
            if self._verbose:
                elapsed = time.monotonic() - started
                self._verbose(self.redactor.text(f"{method} {url} -> {response.status} ({elapsed:.2f}s)"))
            retryable = response.status == 429 or (method == "GET" and response.status in RETRYABLE_READ_STATUSES)
            if retryable and attempt < self.max_retries:
                attempt += 1
                _sleep(self._backoff(attempt, response.header("retry-after")))
                continue
            break
        if method in WRITE_METHODS:
            self.writes += 1
            self._journal_write(method, url, body, response)
        if response.status not in expect:
            raise self._error(method, url, response)
        return response

    def _journal_write(
        self, method: str, url: str, body: Any, response: Response | None, error: str | None = None
    ) -> None:
        if self.journal is None:
            return
        doc = response.json() if response is not None and response.body else {}
        data = doc.get("data") if isinstance(doc, dict) else None
        resource = {"type": data.get("type"), "id": data.get("id")} if isinstance(data, dict) else None
        errors = doc.get("errors") if isinstance(doc, dict) else None
        entry: dict[str, Any] = {
            "method": method,
            "path": _path_only(url),
            "status": response.status if response is not None else None,
            "resource": resource,
            "request": body,
        }
        if errors:
            entry["errors"] = errors
        if error:
            entry["error"] = error
        self.journal.record("write", **entry)

    def _error(self, method: str, url: str, response: Response) -> ApiError:
        doc = response.json()
        raw_errors = doc.get("errors") if isinstance(doc, dict) else None
        errors: list[dict[str, Any]] = []
        for err in raw_errors or []:
            if isinstance(err, dict):
                errors.append(
                    {
                        key: self.redactor.text(str(err.get(key)))
                        for key in ("status", "code", "title", "detail")
                        if err.get(key) is not None
                    }
                )
        path = _path_only(url)
        return ApiError(method, path, response.status, errors, api_hint(response.status, method, path, errors))

    # -- JSON:API helpers -----------------------------------------------------------------------
    def get(self, path: str, params: Mapping[str, Any] | None = None) -> dict[str, Any]:
        doc = self.request("GET", path, params).json()
        return doc if isinstance(doc, dict) else {}

    def get_one(self, path: str, params: Mapping[str, Any] | None = None) -> dict[str, Any] | None:
        """``data`` of a to-one resource, or ``None`` when it doesn't exist (404 or null)."""
        try:
            doc = self.get(path, params)
        except ApiError as exc:
            if exc.status == 404:
                return None
            raise
        data = doc.get("data")
        return data if isinstance(data, dict) else None

    def pages(
        self, path: str, params: Mapping[str, Any] | None = None, page_size: int = 200
    ) -> Iterator[dict[str, Any]]:
        """Yield each page document, following same-origin ``links.next``."""
        query = dict(params or {})
        query.setdefault("limit", page_size)
        doc = self.get(path, query)
        seen: set[str] = set()
        while True:
            yield doc
            next_url = (doc.get("links") or {}).get("next")
            if not next_url or next_url in seen:
                return
            seen.add(next_url)
            doc = self.get(next_url)

    def get_all(
        self,
        path: str,
        params: Mapping[str, Any] | None = None,
        *,
        page_size: int = 200,
        max_items: int | None = None,
    ) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for doc in self.pages(path, params, page_size):
            items.extend(item for item in doc.get("data") or [] if isinstance(item, dict))
            if max_items is not None and len(items) >= max_items:
                return items[:max_items]
        return items

    def create(
        self,
        type_: str,
        attributes: Mapping[str, Any] | None = None,
        relationships: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        data: dict[str, Any] = {"type": type_}
        if attributes is not None:
            data["attributes"] = dict(attributes)
        if relationships:
            data["relationships"] = dict(relationships)
        doc = self.request("POST", f"/v1/{type_}", body={"data": data}).json()
        return (doc.get("data") if isinstance(doc, dict) else None) or {}

    def update(
        self,
        type_: str,
        id_: str,
        attributes: Mapping[str, Any] | None = None,
        relationships: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        data: dict[str, Any] = {"type": type_, "id": id_}
        if attributes:
            data["attributes"] = dict(attributes)
        if relationships:
            data["relationships"] = dict(relationships)
        doc = self.request("PATCH", f"/v1/{type_}/{id_}", body={"data": data}).json()
        return (doc.get("data") if isinstance(doc, dict) else None) or {}

    # -- downloads --------------------------------------------------------------------------
    def download(self, url: str, *, timeout: float = 300.0, max_bytes: int = 4 * 1024**3) -> bytes:
        """Fetch a pre-signed URL. The API token is never attached here."""
        parts = urllib.parse.urlsplit(url)
        host = (parts.hostname or "").lower()
        if parts.scheme != "https" and not (self.allow_loopback and parts.scheme == "http" and host in LOOPBACK_HOSTS):
            raise SecurityError("Refusing to download a report over plain HTTP.")
        attempt = 0
        while True:
            req = urllib.request.Request(url, method="GET")
            req.add_header("User-Agent", USER_AGENT)
            try:
                with self._opener.open(req, timeout=timeout) as resp:
                    chunks: list[bytes] = []
                    total = 0
                    while True:
                        chunk = resp.read(1024 * 1024)
                        if not chunk:
                            break
                        total += len(chunk)
                        if total > max_bytes:
                            raise SecurityError("Download is larger than the safety limit; aborted.")
                        chunks.append(chunk)
                    return b"".join(chunks)
            except urllib.error.HTTPError as exc:
                if exc.code in RETRYABLE_READ_STATUSES and attempt < self.max_retries:
                    attempt += 1
                    _sleep(self._backoff(attempt, exc.headers.get("Retry-After") if exc.headers else None))
                    continue
                raise TransportError(
                    f"Download failed with HTTP {exc.code}.",
                    fix="run the command again; files already downloaded and verified are skipped",
                ) from None
            except (OSError, http.client.HTTPException) as exc:
                if attempt < self.max_retries:
                    attempt += 1
                    _sleep(self._backoff(attempt, None))
                    continue
                reason = getattr(exc, "reason", exc)
                raise TransportError(f"Download failed: {self.redactor.text(str(reason))}", fix=NETWORK_FIX) from None
