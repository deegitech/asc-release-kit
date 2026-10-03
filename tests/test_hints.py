"""Actionable hints: API error codes, helper-program exit codes and upload codes mapped to one-line fixes."""

from __future__ import annotations

import base64
import os
import subprocess
import unittest
from unittest import mock

from helpers import ISSUER_ID, KEY_ID, KitTestCase, shared_key

from asc_release_kit import hints, upload
from asc_release_kit.config import load_settings
from asc_release_kit.credentials import KEYCHAIN_PROMPT_LIMIT, load_api_key
from asc_release_kit.errors import CredentialError


def err(code: str, detail: str = "") -> dict[str, str]:
    return {"code": code, "detail": detail}


class ApiHintTests(unittest.TestCase):
    CASES = (
        # (status, method, path, errors, expected fix)
        (401, "GET", "/v1/apps", [err("NOT_AUTHORIZED")], hints.TOKEN_REJECTED),
        (403, "GET", "/v1/apps", [err("FORBIDDEN.REQUIRED_AGREEMENTS_MISSING_OR_EXPIRED")], hints.AGREEMENTS),
        (403, "GET", "/v1/apps", [err("FORBIDDEN_ERROR", "A required agreement is missing")], hints.AGREEMENTS),
        (403, "GET", "/v1/salesReports", [err("FORBIDDEN_ERROR")], hints.SALES_ROLE),
        (403, "GET", "/v1/financeReports", [], hints.SALES_ROLE),
        (403, "POST", "/v1/analyticsReportRequests", [err("FORBIDDEN_ERROR")], hints.ANALYTICS_CREATE),
        (403, "GET", "/v1/apps/1234567890/analyticsReportRequests", [], hints.ANALYTICS_READ),
        (403, "GET", "/v1/apps/1234567890/customerReviews", [], hints.REVIEWS_ROLE),
        (403, "PATCH", "/v1/appStoreVersions/abc", [err("FORBIDDEN_ERROR")], hints.ROLE),
        (404, "GET", "/v1/apps/1234567899", [err("NOT_FOUND")], hints.APP_NOT_FOUND),
        (404, "GET", "/v1/apps/1234567899/appStoreVersions", [err("NOT_FOUND")], hints.APP_NOT_FOUND),
        (
            409,
            "POST",
            "/v1/appStoreVersions",
            [err("ENTITY_ERROR.ATTRIBUTE.INVALID.DUPLICATE")],
            hints.VERSION_EXISTS,
        ),
        (
            409,
            "POST",
            "/v1/appStoreVersionLocalizations",
            [err("ENTITY_ERROR.ATTRIBUTE.INVALID.DUPLICATE")],
            hints.DUPLICATE,
        ),
        (409, "PATCH", "/v1/appStoreVersions/abc", [err("STATE_ERROR")], hints.STATE),
        (409, "DELETE", "/v1/appScreenshots/abc", [err("STATE_ERROR.ENTITY_STATE_INVALID")], hints.STATE),
        (409, "PATCH", "/v1/appStoreVersions/abc", [err("ENTITY_ERROR.RELATIONSHIP.INVALID")], hints.RELATIONSHIP),
        (409, "PATCH", "/v1/appStoreVersionLocalizations/x", [err("ENTITY_ERROR.ATTRIBUTE.INVALID")], hints.ATTRIBUTE),
        (409, "POST", "/v1/reviewSubmissions", [err("ENTITY_ERROR")], hints.CONFLICT),
        (400, "GET", "/v1/salesReports", [err("PARAMETER_ERROR.INVALID")], hints.PARAMETER),
        (429, "GET", "/v1/apps", [err("RATE_LIMIT_EXCEEDED")], hints.RATE_LIMIT),
        (500, "POST", "/v1/reviewSubmissionItems", [err("UNEXPECTED_ERROR")], hints.WRITE_FAILED),
        (503, "PATCH", "/v1/reviewSubmissions/abc", [], hints.WRITE_FAILED),
        (502, "GET", "/v1/apps", [], hints.READ_FAILED),
    )

    def test_mapping(self) -> None:
        for status, method, path, errors, expected in self.CASES:
            with self.subTest(status=status, path=path, codes=[e["code"] for e in errors]):
                self.assertEqual(hints.api_hint(status, method, path, errors), expected)

    def test_unknown_errors_point_to_the_troubleshooting_page(self) -> None:
        for status, method, path in ((418, "GET", "/v1/apps"), (404, "GET", "/v1/builds/abc"), (422, "PATCH", "/v1/x")):
            with self.subTest(status=status, path=path):
                hint = hints.api_hint(status, method, path, [err("SOMETHING_NEW")])
                self.assertEqual(hint, hints.UNKNOWN)
                self.assertIn("docs/troubleshooting.md", hint)

    def test_every_fix_is_one_line(self) -> None:
        fixes = [rule.fix for rule in hints.API_RULES]
        fixes += list(hints.KEYCHAIN_EXIT_HINTS.values()) + list(hints.AWS_EXIT_HINTS.values())
        fixes += [fix for _, fix in hints.UPLOAD_RULES]
        for fix in fixes:
            with self.subTest(fix=fix[:40]):
                self.assertNotIn("\n", fix)
                self.assertLess(len(fix), 400)

    def test_helper_exit_codes(self) -> None:
        self.assertIn("no Keychain item matches", hints.keychain_exit_hint(44))
        self.assertIn("security unlock-keychain", hints.keychain_exit_hint(36))
        self.assertEqual(hints.keychain_exit_hint(99), hints.KEYCHAIN_DEFAULT_HINT)
        self.assertIn("ssm:GetParameter", hints.aws_exit_hint(254))
        self.assertIn("aws configure list", hints.aws_exit_hint(253))
        self.assertIn("--query Parameter.Name", hints.aws_exit_hint(255))

    def test_upload_codes(self) -> None:
        self.assertIn("raise CFBundleVersion", hints.upload_hint(["ERROR ITMS-90189: Redundant Binary Upload."]))
        self.assertIn("raise CFBundleShortVersionString", hints.upload_hint(["ERROR ITMS-90186: Invalid Pre-Release"]))
        self.assertEqual(hints.upload_hint(["** EXPORT FAILED **"]), "")

    def test_upload_signing_and_app_record_messages(self) -> None:
        cloud = ["error: exportArchive: Cloud signing permission error", "** EXPORT FAILED **"]
        self.assertIn("Admin key", hints.upload_hint(cloud))
        local = ['error: exportArchive: No signing certificate "iOS Distribution" found']
        self.assertIn("login keychain", hints.upload_hint(local))
        record = ["ERROR: No suitable application records were found. Verify your bundle identifier is correct."]
        self.assertIn("Apps > + > New App", hints.upload_hint(record))


class CredentialHintTests(unittest.TestCase):
    def setUp(self) -> None:
        self.pem, _ = shared_key()
        self.env = {"ASC_KEY_ID": KEY_ID, "ASC_ISSUER_ID": ISSUER_ID, "ASC_KEYCHAIN_SERVICE": "asc-release-kit"}

    def load(self, stdout: bytes, code: int = 0):
        settings = load_settings(None, self.env, cwd=os.path.dirname(__file__))
        return load_api_key(
            settings,
            self.env,
            runner=lambda argv: subprocess.CompletedProcess(argv, code, stdout, b""),
            platform="darwin",
        )

    def test_keychain_value_cut_at_128_characters(self) -> None:
        full = base64.b64encode(self.pem)
        self.assertGreater(len(full), KEYCHAIN_PROMPT_LIMIT, "a whole key doesn't fit the prompt")
        first_pem_line = b"-----BEGIN " + b"PRIVATE KEY-----"  # split so secret scanners don't flag the test
        for stored in (full[:KEYCHAIN_PROMPT_LIMIT], first_pem_line):
            with self.subTest(stored=stored[:12]), self.assertRaises(CredentialError) as ctx:
                self.load(stored + b"\n")
            self.assertIn(f"only {len(stored)} characters", str(ctx.exception))
            self.assertIn('-w "$(base64 < AuthKey_', ctx.exception.fix)
        self.assertEqual(self.load(full + b"\n").source, "keychain", "the whole value still works")

    def test_keychain_exit_code_becomes_a_fix(self) -> None:
        with self.assertRaises(CredentialError) as ctx:
            self.load(b"", code=44)
        self.assertIn("exited with 44", str(ctx.exception))
        self.assertIn("no Keychain item matches", ctx.exception.fix)

    def test_truncated_ci_secret(self) -> None:
        env = {"ASC_KEY_ID": KEY_ID, "ASC_ISSUER_ID": ISSUER_ID, "ASC_PRIVATE_KEY": self.pem.decode()[:120]}
        settings = load_settings(None, env, cwd=os.path.dirname(__file__))
        with self.assertRaises(CredentialError) as ctx:
            load_api_key(settings, env)
        self.assertIn("through the END PRIVATE KEY line", ctx.exception.fix)
        self.assertIn("gh secret set ASC_PRIVATE_KEY", ctx.exception.fix)
        self.assertNotIn("-----", ctx.exception.fix, "the redactor would mask text between PEM markers")

    def test_keychain_value_cut_elsewhere_gets_the_keychain_fix(self) -> None:
        stored = base64.b64encode(self.pem)[:200]  # longer than the prompt keeps, still not the whole key
        with self.assertRaises(CredentialError) as ctx:
            self.load(stored + b"\n")
        self.assertIn('-w "$(base64 < AuthKey_', ctx.exception.fix)
        self.assertNotIn("gh secret", ctx.exception.fix)

    def test_truncated_ssm_value_gets_the_ssm_fix(self) -> None:
        env = {"ASC_KEY_ID": KEY_ID, "ASC_ISSUER_ID": ISSUER_ID, "ASC_SSM_PARAMETER": "/example/asc/private-key"}
        settings = load_settings(None, env, cwd=os.path.dirname(__file__))
        with self.assertRaises(CredentialError) as ctx:
            load_api_key(settings, env, runner=lambda argv: subprocess.CompletedProcess(argv, 0, self.pem[:100], b""))
        self.assertIn("aws ssm put-parameter --overwrite", ctx.exception.fix)


class CliFixLineTests(KitTestCase):
    def test_credential_error_prints_fix_and_points_to_doctor(self) -> None:
        os.environ.pop("ASC_PRIVATE_KEY")
        res = self.run_cli("apps", "list")
        self.assertEqual(res.code, 2)
        self.assertIn("fix: create a key under App Store Connect > Users and Access > Integrations", res.err)
        self.assertIn("`asc-release-kit doctor` checks the setup", res.err)

    def test_json_error_carries_the_fix(self) -> None:
        os.environ.pop("ASC_PRIVATE_KEY")
        doc = self.run_cli("apps", "list", "--json").json()
        self.assertEqual(doc["exitCode"], 2)
        self.assertIn("Team Keys", doc["fix"])

    def test_truncated_key_fix_is_printed_unmasked(self) -> None:
        """The fix names the PEM's BEGIN and END lines; the redactor must not swallow it."""
        for value in (self.private_pem.decode("ascii")[:120], "not a key at all"):
            os.environ["ASC_PRIVATE_KEY"] = value
            for args in (("apps", "list"), ("doctor", "--offline")):
                with self.subTest(value=value[:10], args=args):
                    res = self.run_cli(*args)
                    printed = res.out + res.err
                    self.assertIn("fix: store the whole .p8 file, every line from the BEGIN PRIVATE KEY line", printed)
                    self.assertIn("through the END PRIVATE KEY line", printed)
                    self.assertNotIn("[redacted private key]", printed)

    def test_json_api_error_carries_the_fix(self) -> None:
        self.mock.fail(
            "GET", r"^/v1/apps$", status=403, code="FORBIDDEN.REQUIRED_AGREEMENTS_MISSING_OR_EXPIRED", times=5
        )
        res = self.run_cli("apps", "list", "--json")
        self.assertEqual(res.code, 3, res.err)
        doc = res.json()
        self.assertIn("The Account Holder has to accept Apple's latest agreements", doc["fix"])
        self.assertNotIn("fix:", doc["error"])
        self.assertEqual(res.err.count("fix: The Account Holder"), 1, "printed once, on its own line")

    def test_network_error_prints_a_fix(self) -> None:
        import socket

        with socket.socket() as sock:  # a loopback port with nobody listening
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        os.environ["ASC_API_BASE_URL"] = f"http://127.0.0.1:{port}"
        res = self.run_cli("apps", "list")
        self.assertEqual(res.code, 3, res.err)
        self.assertIn("network error", res.err)
        self.assertIn("fix: check the internet connection and any proxy (HTTPS_PROXY)", res.err)

    def test_unknown_api_error_points_to_troubleshooting(self) -> None:
        self.mock.fail("GET", r"^/v1/apps$", status=422, code="SOMETHING_NEW", times=5)
        res = self.run_cli("apps", "list")
        self.assertEqual(res.code, 3, res.err)
        self.assertIn("fix: look up the HTTP status and the error code above in https://", res.err)

    def test_duplicate_version_number_on_create(self) -> None:
        self.mock.fail("POST", r"^/v1/appStoreVersions$", status=409, code="ENTITY_ERROR.ATTRIBUTE.INVALID.DUPLICATE")
        res = self.run_cli("release", "--version", "1.1.0", "--build", "42", "--whats-new-text", "Fixes.", "--apply")
        self.assertEqual(res.code, 3, res.err)
        self.assertIn("fix: App Store Connect already has a version with this number", res.err)
        self.assertIn("higher version number", res.err)
        self.assertNotIn("re-run the command", res.err, "re-running would only hit the same 409")
        self.assertNotIn("`asc-release-kit doctor`", res.err, "not a setup problem")

    def test_write_5xx_says_rerun_not_retry(self) -> None:
        self.mock.fail("POST", r"^/v1/analyticsReportRequests$", status=500)
        res = self.run_cli("analytics", "request", "--apply")
        self.assertEqual(res.code, 3, res.err)
        self.assertIn("fix: Apple's server failed. The write may or may not have taken effect", res.err)

    def test_forbidden_points_to_doctor(self) -> None:
        self.mock.fail("GET", r"^/v1/apps$", status=403, code="FORBIDDEN_ERROR", times=5)
        res = self.run_cli("apps", "list")
        self.assertEqual(res.code, 3)
        self.assertIn("fix: The API key's role doesn't allow this request", res.err)
        self.assertIn("`asc-release-kit doctor`", res.err)

    def test_config_error_prints_fix(self) -> None:
        self.write("asc-release-kit.json", {"api": {"base_url": "https://example.com"}})
        res = self.run_cli("apps", "list")
        self.assertEqual(res.code, 2)
        self.assertIn("fix: delete 'api.base_url' from the config file and export ASC_API_BASE_URL instead", res.err)

    def test_upload_failure_with_a_known_code(self) -> None:
        archive = os.path.join(self.tmp, "Example.xcarchive")
        os.makedirs(archive)
        import plistlib

        with open(os.path.join(archive, "Info.plist"), "wb") as handle:
            plistlib.dump({"ApplicationProperties": {"CFBundleIdentifier": "com.example.mygame"}}, handle)

        def runner(argv, on_line):
            on_line("ERROR ITMS-90189: Redundant Binary Upload. You've already uploaded a build with build number 42.")
            return 70

        with (
            mock.patch.object(upload.shutil, "which", lambda name: f"/usr/bin/{name}"),
            mock.patch.object(upload, "stream_process", runner),
        ):
            res = self.run_cli("upload-build", "--archive", archive, "--apply")
        self.assertEqual(res.code, 1)
        self.assertIn("fix: this build number was already uploaded for this version", res.err)


if __name__ == "__main__":
    unittest.main()
