"""API client: token handling, host pinning, redirects, retries, pagination, journal."""

from __future__ import annotations

import json
import os
import socket
import stat
import unittest
from unittest import mock

from helpers import KitTestCase

from asc_release_kit import api
from asc_release_kit.api import AscClient, check_base_url
from asc_release_kit.config import APPLE_API_HOST
from asc_release_kit.context import Context
from asc_release_kit.errors import ApiError, SecurityError, TransportError, UsageError
from asc_release_kit.journal import Journal
from asc_release_kit.redact import Redactor


class BaseUrlTests(unittest.TestCase):
    def test_allowed(self) -> None:
        for url in (f"https://{APPLE_API_HOST}", f"https://{APPLE_API_HOST}/"):
            with self.subTest(url=url):
                self.assertEqual(check_base_url(url, allow_loopback=False), f"https://{APPLE_API_HOST}")

    def test_loopback_needs_the_explicit_switch(self) -> None:
        for url in ("http://127.0.0.1:8080", "http://localhost:1", "https://[::1]:9"):
            with self.subTest(url=url):
                check_base_url(url, allow_loopback=True)
                with self.assertRaises(UsageError) as ctx:
                    check_base_url(url, allow_loopback=False)
                self.assertIn("ASC_ALLOW_INSECURE_LOOPBACK=1", str(ctx.exception))
        with mock.patch.dict(os.environ, {"ASC_ALLOW_INSECURE_LOOPBACK": "1"}):
            check_base_url("http://127.0.0.1:8080")
        with mock.patch.dict(os.environ, {"ASC_ALLOW_INSECURE_LOOPBACK": "yes"}), self.assertRaises(UsageError):
            check_base_url("http://127.0.0.1:8080")

    def test_refused(self) -> None:
        for url in (
            f"http://{APPLE_API_HOST}",
            "https://apple.com.example.net",
            "https://evil.example",
            f"https://{APPLE_API_HOST}.attacker.io",
            "ftp://127.0.0.1",
            "",
        ):
            with self.subTest(url=url), self.assertRaises(UsageError):
                check_base_url(url, allow_loopback=True)

    def test_only_scheme_host_and_port(self) -> None:
        for url in (
            f"https://{APPLE_API_HOST}/v1",
            f"https://{APPLE_API_HOST}/v1/",
            f"https://{APPLE_API_HOST}?x=1",
            f"https://user:pw@{APPLE_API_HOST}",
            f"https://{APPLE_API_HOST}:notaport",
        ):
            with self.subTest(url=url), self.assertRaises(UsageError):
                check_base_url(url, allow_loopback=False)


class ClientTests(KitTestCase):
    def client(self) -> AscClient:
        ctx = Context.create(command="test", apply=True)
        return ctx.client()

    def test_every_request_carries_a_valid_token(self) -> None:
        client = self.client()
        client.get("/v1/apps")
        self.assertEqual(client.requests, 1)
        self.assertEqual(self.mock.requests[-1]["path"], "/v1/apps")

    def test_pagination_follows_next(self) -> None:
        apps = self.client().get_all("/v1/apps", page_size=1)
        self.assertEqual(len(apps), 2)
        self.assertEqual(sum(1 for r in self.mock.requests if r["path"] == "/v1/apps"), 2)

    def test_cross_origin_next_link_refused(self) -> None:
        with self.assertRaises(SecurityError):
            self.client().get_all("/v1/test/cross-origin")
        self.assertEqual(self.mock.files.requests, [], "the other host must never be contacted")

    def test_redirect_does_not_forward_token(self) -> None:
        self.client().get("/v1/test/redirect")
        landing = [r for r in self.mock.files.requests if r["path"] == "/landing"]
        self.assertEqual(len(landing), 1)
        self.assertIsNone(landing[0]["authorization"])

    def test_429_is_retried_with_retry_after(self) -> None:
        self.mock.fail(
            "GET", r"^/v1/apps$", status=429, code="RATE_LIMIT_EXCEEDED", times=2, headers={"Retry-After": "7"}
        )
        sleeps = []
        with mock.patch.object(api, "_sleep", sleeps.append):
            self.client().get("/v1/apps")
        self.assertEqual(sleeps, [7.0, 7.0])

    def test_get_5xx_retried_but_post_5xx_not(self) -> None:
        self.mock.fail("GET", r"^/v1/apps$", status=503, times=1)
        self.client().get("/v1/apps")
        self.mock.fail("POST", r"^/v1/analyticsReportRequests$", status=500, times=1)
        client = self.client()
        with self.assertRaises(ApiError) as ctx:
            client.create("analyticsReportRequests", {"accessType": "ONGOING"}, {"app": api.rel("apps", "1234567890")})
        self.assertEqual(ctx.exception.status, 500)
        posts = [r for r in self.mock.requests if r["method"] == "POST"]
        self.assertEqual(len(posts), 1, "a write must not be retried after a 5xx")

    def test_post_429_is_retried(self) -> None:
        self.mock.fail("POST", r"^/v1/analyticsReportRequests$", status=429, times=1)
        self.client().create(
            "analyticsReportRequests", {"accessType": "ONGOING"}, {"app": api.rel("apps", "1234567890")}
        )
        posts = [r for r in self.mock.requests if r["method"] == "POST"]
        self.assertEqual(len(posts), 2)

    def test_writes_are_journaled_with_private_mode(self) -> None:
        client = self.client()
        client.create(
            "appStoreReviewDetails",
            {"contactEmail": "you@example.com", "contactPhone": "+1 555 0100", "notes": "demo/demo"},
            {"appStoreVersion": api.rel("appStoreVersions", "ver-other")},
        )
        entries = self.journal()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["event"], "write")
        self.assertEqual(entries[0]["status"], 201)
        attrs = entries[0]["request"]["data"]["attributes"]
        self.assertEqual(attrs["contactEmail"], "[redacted]")
        self.assertNotIn("demo/demo", json.dumps(entries))
        if os.name == "posix":
            self.assertEqual(stat.S_IMODE(os.stat(self.journal_path).st_mode), 0o600)

    def test_api_errors_are_redacted_and_hinted(self) -> None:
        self.mock.fail(
            "GET",
            r"^/v1/apps$",
            status=403,
            code="FORBIDDEN_ERROR",
            times=5,
            detail="key ABC123DEFG may not read this; ask you@example.com",
        )
        with self.assertRaises(ApiError) as ctx:
            self.client().get("/v1/apps")
        message = str(ctx.exception)
        self.assertNotIn("ABC123DEFG", message)
        self.assertNotIn("you@example.com", message)
        self.assertNotIn("fix:", message, "the fix travels separately, as ApiError.fix")
        self.assertIn("role", ctx.exception.fix)

    def test_get_one_returns_none_for_404_and_null(self) -> None:
        client = self.client()
        new_version = self.mock.seed_editable_version("1.1.0")
        self.assertIsNone(client.get_one("/v1/appStoreVersions/missing"))  # 404
        self.assertIsNone(client.get_one(f"/v1/appStoreVersions/{new_version}/gameCenterAppVersion"))  # data: null
        self.assertIsNotNone(client.get_one("/v1/appStoreVersions/ver-live/gameCenterAppVersion"))

    def test_paths_must_be_versioned(self) -> None:
        with self.assertRaises(ValueError):
            self.client().get("apps")

    def test_download_never_sends_token(self) -> None:
        url = self.mock.files.put("/blob", b"data")
        self.assertEqual(self.client().download(url), b"data")
        self.assertIsNone(self.mock.files.requests[-1]["authorization"])

    def test_token_rejected_by_server_gives_hint(self) -> None:
        self.mock.key_id = "OTHERKEY99"
        res = self.run_cli("apps", "list")
        self.assertEqual(res.code, 3)
        self.assertIn("rejected the token", res.err)

    def test_plan_mode_client_refuses_writes_before_sending(self) -> None:
        client = Context.create(command="test").client()  # no --apply
        for call in (
            lambda: client.create("analyticsReportRequests", {"accessType": "ONGOING"}),
            lambda: client.update("appStoreVersions", "ver-live", {"versionString": "9.9.9"}),
            lambda: client.request("DELETE", "/v1/appStoreVersions/ver-live"),
        ):
            with self.assertRaises(SecurityError):
                call()
        self.assertEqual(self.mock.requests, [], "nothing may reach the server")
        client.get("/v1/apps")  # reads still work
        self.assertEqual(len(self.mock.requests), 1)

    def test_write_without_an_answer_is_journaled_and_not_repeated(self) -> None:
        with socket.socket() as sock:  # a loopback port with nobody listening
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        journal = Journal(self.journal_path, Redactor(), "test")
        client = AscClient(
            lambda: "token", base_url=f"http://127.0.0.1:{port}", journal=journal, allow_loopback=True, timeout=5
        )
        with self.assertRaises(TransportError) as ctx:
            client.create("analyticsReportRequests", {"accessType": "ONGOING"}, {"app": api.rel("apps", "1234567890")})
        self.assertIn("may or may not have been applied", str(ctx.exception))
        (entry,) = self.journal()
        self.assertEqual((entry["event"], entry["method"], entry["status"]), ("write", "POST", None))
        self.assertIn("may have been applied", entry["error"])


class IndividualKeyTests(KitTestCase):
    team_key = False

    def test_individual_key_token_accepted(self) -> None:
        res = self.run_cli("auth", "check")
        self.assertEqual(res.code, 0, res.err)
        self.assertIn("individual key", res.out)


if __name__ == "__main__":
    unittest.main()
