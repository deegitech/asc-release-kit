"""CLI behaviour: help, version, auth check, apps list, error reporting."""

from __future__ import annotations

import contextlib
import gzip
import io
import os
import signal
import unittest
from unittest import mock

from helpers import ISSUER_ID, KEY_ID, KitTestCase

from asc_release_kit import __version__, cli
from asc_release_kit.credentials import ApiKey, key_file


class ParserTests(unittest.TestCase):
    def run_main(self, *args: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = cli.main(list(args))
            except SystemExit as exc:
                code = int(exc.code or 0)
        return code, out.getvalue(), err.getvalue()

    def test_version(self) -> None:
        code, out, _ = self.run_main("--version")
        self.assertEqual(code, 0)
        self.assertIn(__version__, out)

    def test_no_command_prints_help(self) -> None:
        code, _, err = self.run_main()
        self.assertEqual(code, 2)
        self.assertIn("commands", err)

    def test_group_without_action_prints_group_help(self) -> None:
        code, _, err = self.run_main("analytics")
        self.assertEqual(code, 2)
        self.assertIn("download", err)

    def test_epilog_lists_every_exit_code(self) -> None:
        code, out, _ = self.run_main("--help")
        self.assertEqual(code, 0)
        for text in ("0 ok", "1 checks failed", "2 usage", "3 App Store Connect", "4 stopped", "70 unexpected", "130"):
            self.assertIn(text, out)

    def test_every_command_has_help(self) -> None:
        for args in (
            ("auth", "check"),
            ("doctor",),
            ("apps", "list"),
            ("release",),
            ("aso", "validate"),
            ("aso", "apply"),
            ("aso", "pull"),
            ("aso", "storefronts"),
            ("analytics", "request"),
            ("analytics", "list"),
            ("analytics", "download"),
            ("sales", "download"),
            ("reviews", "export"),
            ("upload-build",),
        ):
            with self.subTest(args=args):
                code, out, _ = self.run_main(*args, "--help")
                self.assertEqual(code, 0)
                self.assertIn("usage:", out)


class AuthAndAppsTests(KitTestCase):
    def test_auth_check_offline_and_online(self) -> None:
        res = self.run_cli("auth", "check", "--offline")
        self.assertEqual(res.code, 0, res.err)
        self.assertIn("token signed (ES256)", res.out)
        self.assertEqual(self.mock.requests, [])
        res = self.run_cli("auth", "check")
        self.assertEqual(res.code, 0, res.err)
        self.assertIn("2 app(s) visible", res.out)
        self.assertIn("rate limit", res.out)
        for secret in (KEY_ID, ISSUER_ID, "PRIVATE KEY"):
            self.assertNotIn(secret, res.out + res.err)

    def test_auth_check_with_openssl_signer(self) -> None:
        import shutil

        if shutil.which("openssl") is None:
            self.skipTest("openssl not available")
        os.environ["ASC_SIGNER"] = "openssl"
        res = self.run_cli("auth", "check", "--json")
        self.assertEqual(res.code, 0, res.err)
        self.assertEqual(res.json()["result"]["signer"], "openssl")

    def test_auth_check_reports_missing_key(self) -> None:
        os.environ.pop("ASC_PRIVATE_KEY")
        res = self.run_cli("auth", "check")
        self.assertEqual(res.code, 2)
        self.assertIn("No App Store Connect API key configured", res.err)

    def test_apps_list(self) -> None:
        res = self.run_cli("apps", "list")
        self.assertEqual(res.code, 0, res.err)
        self.assertIn("com.example.mygame", res.out)
        doc = self.run_cli("apps", "list", "--json").json()
        self.assertEqual([a["id"] for a in doc["result"]["apps"]], ["1234567891", "1234567890"])

    def test_global_flags_after_subcommand(self) -> None:
        res = self.run_cli("apps", "list", "--json", "--ascii")
        self.assertEqual(res.json()["command"], "apps list")

    def test_errors_in_json_mode_are_json(self) -> None:
        res = self.run_cli("release", "--version", "1.1.0", "--build", "43", "--json")
        self.assertEqual(res.code, 4)
        doc = res.json()
        self.assertEqual(doc["exitCode"], 4)
        self.assertEqual(doc["command"], "release")
        self.assertIn("not VALID", doc["error"])
        self.assertTrue(doc["events"], "the events collected before the failure are kept")

    def test_failed_read_back_in_json_mode_lists_the_checks(self) -> None:
        wn = self.write("whats-new.json", {"en-US": "Fixes.", "de-DE": "Fixes."})
        original = self.mock.patch_simple("appStoreVersionLocalizations")

        def lossy(rid, body, **kw):
            body["data"]["attributes"].pop("whatsNew", None)
            return original(rid=rid, body=body, **kw)

        self.mock.routes = [
            (m, rx, lossy if (m == "PATCH" and "appStoreVersionLocalizations" in rx.pattern) else fn)
            for m, rx, fn in self.mock.routes
        ]
        res = self.run_cli("release", "--version", "1.1.0", "--build", "42", "--whats-new", wn, "--apply", "--json")
        self.assertEqual(res.code, 1, res.err)
        doc = res.json()
        failed = [e["text"] for e in doc["events"] if e["kind"] == "fail"]
        self.assertTrue(any("what's new [en-US]" in text for text in failed), failed)
        self.assertGreater(doc["changes"], 0, "the writes already made are visible")

    def test_unexpected_errors_are_redacted(self) -> None:
        boom = RuntimeError(f"boom with you@example.com and key {KEY_ID}")
        with mock.patch("asc_release_kit.cli.Context.client", side_effect=boom):
            res = self.run_cli("apps", "list")
            self.assertEqual(res.code, 70)
            self.assertNotIn("you@example.com", res.err)
            self.assertNotIn(KEY_ID, res.err)
            self.assertIn("--verbose", res.err)
            res = self.run_cli("apps", "list", "--verbose")
        self.assertIn("Traceback", res.err)
        self.assertNotIn("you@example.com", res.err)

    def test_bad_api_base_url_is_refused(self) -> None:
        os.environ["ASC_API_BASE_URL"] = "https://collector.example.net"
        res = self.run_cli("apps", "list")
        self.assertEqual(res.code, 2)
        self.assertIn("Refusing to send App Store Connect credentials", res.err)
        self.assertEqual(self.mock.requests, [])

    def test_loopback_base_url_needs_the_explicit_switch(self) -> None:
        os.environ.pop("ASC_ALLOW_INSECURE_LOOPBACK")
        with mock.patch("asc_release_kit.context.load_api_key", side_effect=AssertionError("key loaded")):
            res = self.run_cli("apps", "list")
        self.assertEqual(res.code, 2, res.err)
        self.assertIn("ASC_ALLOW_INSECURE_LOOPBACK=1", res.err)
        self.assertEqual(self.mock.requests, [], "no token was sent to the loopback listener")

    def test_config_file_in_use_is_named(self) -> None:
        self.write("asc-release-kit.json", {"app": {"platform": "IOS"}})
        res = self.run_cli("apps", "list")
        self.assertEqual(res.code, 0, res.err)
        self.assertIn("note: using config file asc-release-kit.json (found in the current directory)", res.err)
        doc = self.run_cli("apps", "list", "--json").json()
        self.assertEqual(doc["config"], "asc-release-kit.json")

    def test_auth_check_reports_where_settings_came_from_without_values(self) -> None:
        self.write("asc-release-kit.json", {"app": {"platform": "IOS"}})
        res = self.run_cli("auth", "check", "--offline")
        self.assertEqual(res.code, 0, res.err)
        self.assertIn("config file: asc-release-kit.json (found in the current directory)", res.out)
        self.assertIn("key ID env", res.out)
        self.assertIn("platform config", res.out)
        self.assertIn("vendor number not set", res.out)
        for secret in (KEY_ID, ISSUER_ID, "1234567890"):
            self.assertNotIn(secret, res.out + res.err)
        origins = self.run_cli("auth", "check", "--offline", "--json").json()["result"]["origins"]
        self.assertEqual(origins["app_id"], "env")
        self.assertEqual(origins["platform"], "config")


class SignalTests(unittest.TestCase):
    @unittest.skipUnless(hasattr(signal, "SIGTERM") and os.name == "posix", "POSIX signals")
    def test_sigterm_runs_cleanup_and_exits_143(self) -> None:
        """A cancelled CI job (SIGTERM) must not leave the temporary key copy behind."""
        seen: dict[str, str] = {}
        before = signal.getsignal(signal.SIGTERM)

        def handler(_args, _redactor) -> int:
            key = ApiKey(KEY_ID, ISSUER_ID, "team", b"fake key bytes", "env")
            with key_file(key) as path:
                seen["path"] = path
                signal.raise_signal(signal.SIGTERM)
            return 0

        with mock.patch.object(cli, "cmd_apps_list", handler), self.assertRaises(SystemExit) as ctx:
            cli.main(["apps", "list"])
        self.assertEqual(ctx.exception.code, 143)
        self.assertFalse(os.path.exists(seen["path"]), "the temporary key copy must be gone")
        self.assertIs(signal.getsignal(signal.SIGTERM), before, "the previous handler is restored")


class PlanModeGuardTests(KitTestCase):
    """Without --apply, every command runs with a client that refuses POST, PATCH and DELETE."""

    def test_every_plan_runs_without_writing(self) -> None:
        self.mock.seed_analytics()
        self.mock.seed_reviews(2)
        self.mock.sales[("DAILY", "2030-03-01")] = gzip.compress(b"Provider\tUnits\nAPPLE\t1\n")
        os.environ["ASC_VENDOR_NUMBER"] = "87654321"
        wn = self.write("whats-new.json", {"en-US": "Fixes.", "de-DE": "Fehlerbehebungen."})
        aso = self.write("aso.json", {"localizations": {"en-US": {"promotionalText": "New levels every week."}}})
        for args in (
            ("auth", "check"),
            ("doctor",),
            ("apps", "list"),
            ("release", "--version", "1.1.0", "--build", "42", "--whats-new", wn, "--submit"),
            ("aso", "apply", aso, "--version", "1.0.0", "--fields", "promotionalText"),
            ("aso", "pull", "--out", os.path.join(self.tmp, "pulled.json")),
            ("analytics", "request"),
            ("analytics", "list", "--reports"),
            ("analytics", "download", "--report", "rep-engagement", "--out", os.path.join(self.tmp, "a")),
            ("sales", "download", "--date", "2030-03-01", "--out", os.path.join(self.tmp, "s")),
            ("reviews", "export", "--out", os.path.join(self.tmp, "r.csv")),
        ):
            with self.subTest(args=args):
                res = self.run_cli(*args)
                self.assertEqual(res.code, 0, res.out + res.err)
        self.assertNoWrites()
        self.assertEqual(self.journal(), [], "plans never write the journal")


if __name__ == "__main__":
    unittest.main()
