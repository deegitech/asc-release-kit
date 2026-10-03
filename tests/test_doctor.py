"""`doctor`: read-only setup checks, each failure with its fix, against the mock App Store Connect."""

from __future__ import annotations

import base64
import gzip
import json
import os
import shutil
import subprocess
import sys
import time
import unittest
from unittest import mock

from helpers import ISSUER_ID, KEY_ID, KitTestCase, have_cryptography

from asc_release_kit import doctor

VENDOR = "87654321"


def lines(text: str, glyph: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip().startswith(glyph)]


class DoctorTestCase(KitTestCase):
    def setUp(self) -> None:
        super().setUp()
        for name, value in (
            ("_which", lambda program: f"/usr/bin/{program}"),
            ("_runner", None),
            ("_platform", None),
            ("_xcode_select", lambda: (0, "/Applications/Xcode.app/Contents/Developer")),
        ):
            patcher = mock.patch.object(doctor, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def key_file(self, name: str = f"AuthKey_{KEY_ID}.p8", mode: int = 0o600, directory: str = "keys") -> str:
        folder = os.path.join(self.tmp, directory)
        os.makedirs(folder, mode=0o700, exist_ok=True)
        path = os.path.join(folder, name)
        with open(path, "wb") as handle:
            handle.write(self.private_pem)
        os.chmod(path, mode)
        os.environ.pop("ASC_PRIVATE_KEY", None)
        os.environ["ASC_PRIVATE_KEY_PATH"] = path
        return path

    def assertNoSecrets(self, text: str, *extra: str) -> None:
        secrets = [KEY_ID, ISSUER_ID, "PRIVATE KEY", "eyJ", self.tmp, *extra]
        if os.environ.get("ASC_VENDOR_NUMBER"):
            secrets.append(VENDOR)  # otherwise the same digits appear as the placeholder in a fix
        for secret in secrets:
            self.assertNotIn(secret, text)


class HealthyTests(DoctorTestCase):
    def test_everything_passes_and_nothing_is_written(self) -> None:
        self.mock.seed_analytics()
        self.mock.sales[("DAILY", "latest")] = gzip.compress(b"Provider\tUnits\nAPPLE\t1\n")
        os.environ["ASC_VENDOR_NUMBER"] = VENDOR
        res = self.run_cli("doctor")
        self.assertEqual(res.code, 0, res.out + res.err)
        self.assertEqual(lines(res.out, "✗"), [])
        for text in (
            "one key source",
            "token signed (ES256)",
            "Apple accepted the token; 2 app(s) visible",
            "app 1234567890 is visible: com.example.mygame",
            "Sales and Trends accepted the vendor number",
            "an active ONGOING analytics report request exists",
            "requests left this hour",
            "clock is within 60 s",
            "0 failed",
        ):
            self.assertIn(text, res.out)
        if sys.platform == "darwin" and shutil.which("xcode-select"):
            self.assertIn("Xcode is selected", res.out)
        self.assertNoSecrets(res.out + res.err)
        self.assertEqual({r["method"] for r in self.mock.requests}, {"GET"})
        self.assertEqual(self.journal(), [], "doctor never writes the journal")

    def test_key_file_checks(self) -> None:
        self.key_file()
        res = self.run_cli("doctor", "--offline")
        self.assertEqual(res.code, 0, res.out + res.err)
        self.assertIn("readable by you only", res.out)
        self.assertIn("outside any git repository", res.out)
        self.assertIn("name matches the key ID", res.out)
        if have_cryptography():
            self.assertIn("EC P-256 private key", res.out)
        self.assertNoSecrets(res.out + res.err)

    def test_offline_contacts_nothing(self) -> None:
        res = self.run_cli("doctor", "--offline")
        self.assertEqual(res.code, 0, res.out + res.err)
        self.assertIn("skipped: --offline", res.out)
        self.assertEqual(self.mock.requests, [])

    def test_json_document(self) -> None:
        res = self.run_cli("doctor", "--json")
        self.assertEqual(res.code, 0, res.err)
        result = res.json()["result"]
        self.assertTrue(result["ok"])
        self.assertEqual(result["failed"], 0)
        ids = [check["id"] for check in result["checks"]]
        for expected in ("settings.key_id", "key.source", "token.sign", "api.token", "api.app", "api.sales"):
            self.assertIn(expected, ids)
        sales = next(c for c in result["checks"] if c["id"] == "api.sales")
        self.assertEqual(sales["status"], "info")
        self.assertIn("Vendor #", sales["fix"])
        self.assertNoSecrets(res.out)


class FailureTests(DoctorTestCase):
    def test_missing_key_is_a_failure_with_a_fix(self) -> None:
        os.environ.pop("ASC_PRIVATE_KEY")
        res = self.run_cli("doctor")
        self.assertEqual(res.code, 1, res.out)
        self.assertIn("✗ no private key source is configured", res.out)
        self.assertIn("fix: store the .p8 key in ONE place", res.out)
        self.assertIn("skipped until the checks above pass", res.out)
        self.assertEqual(self.mock.requests, [])

    @unittest.skipUnless(os.name == "posix", "POSIX permissions")
    def test_group_readable_key_file(self) -> None:
        path = self.key_file(mode=0o644)
        res = self.run_cli("doctor")
        self.assertEqual(res.code, 1, res.out)
        self.assertIn("readable by other users", res.out)
        self.assertIn('fix: chmod 600 "$ASC_PRIVATE_KEY_PATH"', res.out)
        self.assertNoSecrets(res.out + res.err, path)

    def test_key_in_a_git_repository_and_name_mismatch_warn(self) -> None:
        os.makedirs(os.path.join(self.tmp, "repo", ".git"))
        self.key_file(name="AuthKey_ZZZZ999999.p8", directory="repo")
        res = self.run_cli("doctor")
        self.assertEqual(res.code, 0, res.out)
        warnings = lines(res.out, "!")
        self.assertTrue(any("inside a git repository" in w for w in warnings), warnings)
        self.assertTrue(any("names a different key ID" in w for w in warnings), warnings)
        self.assertEqual(self.run_cli("doctor", "--strict").code, 1, "--strict turns warnings into failures")

    def test_identifier_mistakes(self) -> None:
        os.environ["ASC_ISSUER_ID"] = "ABCDE12345"  # a team ID pasted where the issuer ID goes
        res = self.run_cli("doctor", "--offline")
        self.assertEqual(res.code, 1)
        self.assertIn("isn't a UUID", res.out)
        self.assertIn("Issuer ID shown above the Team Keys list", res.out)
        os.environ.pop("ASC_ISSUER_ID")
        os.environ.pop("ASC_KEY_ID")
        res = self.run_cli("doctor", "--offline")
        self.assertEqual(res.code, 1)
        self.assertIn("✗ key ID is not set", res.out)
        self.assertIn("Integrations > App Store Connect API > Team Keys", res.out)
        self.assertIn("✗ issuer ID is not set", res.out)
        self.assertIn("token not signed", res.out)

    def test_bad_config_file(self) -> None:
        self.write("asc-release-kit.json", {"auth": {"private_key": "x"}})
        res = self.run_cli("doctor")
        self.assertEqual(res.code, 1, res.out)
        self.assertIn("settings can't be loaded", res.out)
        self.assertIn("fix: delete 'auth.private_key' from the config file", res.out)

    def test_keychain_item_cut_off_by_the_prompt(self) -> None:
        os.environ.pop("ASC_PRIVATE_KEY")
        os.environ["ASC_KEYCHAIN_SERVICE"] = "asc-release-kit"
        stored = base64.b64encode(self.private_pem).decode()[:128]

        def runner(argv):
            return subprocess.CompletedProcess(argv, 0, stored.encode() + b"\n", b"")

        with mock.patch.object(doctor, "_runner", runner), mock.patch.object(doctor, "_platform", "darwin"):
            res = self.run_cli("doctor", "--offline")
        self.assertEqual(res.code, 1, res.out)
        self.assertIn("holds only 128 characters", res.out)
        self.assertIn('-w "$(base64 < AuthKey_', res.out)
        self.assertNotIn(stored, res.out)

    def test_keychain_item_missing(self) -> None:
        os.environ.pop("ASC_PRIVATE_KEY")
        os.environ["ASC_KEYCHAIN_SERVICE"] = "asc-release-kit"

        def runner(argv):
            return subprocess.CompletedProcess(argv, 44, b"", b"")

        with mock.patch.object(doctor, "_runner", runner), mock.patch.object(doctor, "_platform", "darwin"):
            res = self.run_cli("doctor", "--offline")
        self.assertEqual(res.code, 1, res.out)
        self.assertIn("fix: no Keychain item matches", res.out)

    def test_token_rejected(self) -> None:
        self.mock.key_id = "OTHERKEY99"
        res = self.run_cli("doctor")
        self.assertEqual(res.code, 1, res.out)
        self.assertIn("✗ App Store Connect refused the first request: GET /v1/apps -> HTTP 401", res.out)
        self.assertIn("fix: App Store Connect rejected the token", res.out)
        self.assertNotIn("app 1234567890 is visible", res.out, "later API checks are skipped")
        self.assertNoSecrets(res.out + res.err)

    def test_agreements_not_accepted(self) -> None:
        self.mock.fail(
            "GET",
            r"^/v1/apps$",
            status=403,
            code="FORBIDDEN.REQUIRED_AGREEMENTS_MISSING_OR_EXPIRED",
            detail="A required agreement is missing or has expired.",
            times=5,
        )
        res = self.run_cli("doctor")
        self.assertEqual(res.code, 1, res.out)
        self.assertIn("fix: The Account Holder has to accept Apple's latest agreements", res.out)

    def test_app_not_visible_and_bundle_mismatch(self) -> None:
        os.environ["ASC_APP_ID"] = "1234567899"
        res = self.run_cli("doctor")
        self.assertEqual(res.code, 1, res.out)
        self.assertIn("✗ app 1234567899 isn't visible to this key", res.out)
        self.assertIn("apps list", res.out)
        os.environ["ASC_APP_ID"] = "1234567890"
        os.environ["ASC_BUNDLE_ID"] = "com.example.other"
        res = self.run_cli("doctor")
        self.assertEqual(res.code, 1, res.out)
        self.assertIn("but ASC_BUNDLE_ID / app.bundle_id says com.example.other", res.out)

    def test_sales_checks(self) -> None:
        os.environ["ASC_VENDOR_NUMBER"] = VENDOR
        res = self.run_cli("doctor")  # no report for the latest day: can't confirm, warn only
        self.assertEqual(res.code, 0, res.out)
        self.assertIn("! Sales and Trends has no report for the latest day", res.out)
        self.assertIn("sales download --date YYYY-MM-DD", res.out, "no example date to copy literally")
        self.mock.fail("GET", r"^/v1/salesReports$", status=403, code="FORBIDDEN_ERROR", times=5)
        res = self.run_cli("doctor")
        self.assertEqual(res.code, 1, res.out)
        self.assertIn("individual keys can't access Sales and Finance", res.out)
        self.assertNoSecrets(res.out + res.err)

    def test_stopped_analytics_request_warns(self) -> None:
        self.mock.seed_analytics()
        for request in self.mock.db["analyticsReportRequests"].values():
            request["attributes"]["stoppedDueToInactivity"] = True
        res = self.run_cli("doctor")
        self.assertEqual(res.code, 0, res.out)
        self.assertIn("stopped because its reports weren't downloaded", res.out)
        self.assertIn("fix: asc-release-kit analytics request --apply", res.out)

    def test_clock_skew_warns(self) -> None:
        with mock.patch.object(doctor, "_now", lambda: time.time() + 600):
            res = self.run_cli("doctor")
        self.assertEqual(res.code, 0, res.out)
        self.assertIn("ahead of Apple's", res.out)
        self.assertIn("Set time and date automatically", res.out)

    def test_network_error(self) -> None:
        import socket

        with socket.socket() as sock:  # a loopback port with nobody listening
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        os.environ["ASC_API_BASE_URL"] = f"http://127.0.0.1:{port}"
        res = self.run_cli("doctor")
        self.assertEqual(res.code, 1, res.out)
        self.assertIn("network error", res.out)
        self.assertIn("fix: check the internet connection", res.out)

    def test_connection_lost_after_the_first_request(self) -> None:
        def drop(**_):
            raise ConnectionResetError("connection reset")

        self.mock.routes = [
            (m, rx, drop if rx.pattern == r"/v1/apps/(?P<app>[^/]+)" else fn) for m, rx, fn in self.mock.routes
        ]
        res = self.run_cli("doctor")
        self.assertEqual(res.code, 1, res.out + res.err)
        self.assertIn("Apple accepted the token", res.out)
        self.assertIn("✗ GET /v1/apps/1234567890: network error", res.out)
        self.assertIn("fix: check the internet connection", res.out)

    def test_xcode_check_is_information_only(self) -> None:
        """/usr/bin/xcrun exists on every Mac, so the check asks `xcode-select -p` which Xcode is active."""
        select = "sudo xcode-select -s /Applications/Xcode.app/Contents/Developer"
        for platform, answer, expected, fix in (
            ("darwin", (0, "/Applications/Xcode.app/Contents/Developer"), "✓ Xcode is selected (Xcode.app)", None),
            ("darwin", (0, "/Library/Developer/CommandLineTools"), "only the Command Line Tools are selected", select),
            ("darwin", (2, ""), "no Xcode is selected", select),
            ("linux", (127, ""), "upload-build runs on macOS only", None),
        ):
            with (
                self.subTest(platform=platform, answer=answer),
                mock.patch.object(doctor, "_platform", platform),
                mock.patch.object(doctor, "_xcode_select", lambda answer=answer: answer),
            ):
                res = self.run_cli("doctor", "--offline", "--strict")
                self.assertEqual(res.code, 0, res.out)  # optional: never a failure, not even with --strict
                self.assertIn(expected, res.out)
                if fix:
                    self.assertIn(fix, res.out)
                if platform != "darwin":
                    self.assertNotIn("Mac App Store", res.out)

    def test_analytics_is_not_checked_when_the_app_check_fails(self) -> None:
        self.mock.seed_analytics()
        os.environ["ASC_APP_ID"] = "1234567899"
        res = self.run_cli("doctor")
        self.assertEqual(res.code, 1, res.out)
        self.assertIn("✗ app 1234567899 isn't visible to this key", res.out)
        self.assertIn("analytics request not checked: the app check above failed", res.out)
        self.assertNotIn("no analytics report request yet", res.out)
        self.assertEqual(len(lines(res.out, "✗")), 1, "one cause, one failure")
        self.assertNotIn("/v1/apps/1234567899/analyticsReportRequests", [r["path"] for r in self.mock.requests])

    def test_json_failure_lists_fixes(self) -> None:
        os.environ.pop("ASC_PRIVATE_KEY")
        res = self.run_cli("doctor", "--json")
        self.assertEqual(res.code, 1)
        doc = res.json()
        self.assertFalse(doc["result"]["ok"])
        failed = [c for c in doc["result"]["checks"] if c["status"] == "fail"]
        self.assertTrue(failed and all(c["fix"] for c in failed))
        json.dumps(doc)  # plain JSON all the way down


class IndividualKeyDoctorTests(DoctorTestCase):
    team_key = False

    def test_individual_key_cannot_read_sales(self) -> None:
        os.environ["ASC_VENDOR_NUMBER"] = VENDOR
        res = self.run_cli("doctor")
        self.assertEqual(res.code, 1, res.out)
        self.assertIn("individual key (no issuer ID", res.out)
        self.assertIn("individual keys can't access Sales and Trends", res.out)
        self.assertNotIn("/v1/salesReports", [r["path"] for r in self.mock.requests])


if __name__ == "__main__":
    unittest.main()
