"""Settings resolution and redaction rules."""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest

from asc_release_kit.config import DEFAULT_BASE_URL, load_settings
from asc_release_kit.errors import UsageError
from asc_release_kit.redact import REDACTED, Redactor, summarize_private_text

TOML_OK = sys.version_info >= (3, 11)
if not TOML_OK:  # pragma: no cover - depends on the interpreter
    try:
        import tomli  # noqa: F401

        TOML_OK = True
    except ImportError:
        pass  # tomli missing: the TOML tests are skipped


class ConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def write(self, name: str, text: str) -> str:
        path = os.path.join(self.tmp, name)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
        return path

    def test_defaults(self) -> None:
        s = load_settings(None, {}, cwd=self.tmp)
        self.assertEqual(s.platform, "IOS")
        self.assertEqual(s.key_type, "team")
        self.assertEqual(s.token_ttl_seconds, 900)
        self.assertEqual(s.api_base_url, DEFAULT_BASE_URL)
        self.assertTrue(DEFAULT_BASE_URL.startswith("https://api.") and DEFAULT_BASE_URL.endswith(".apple.com"))
        self.assertIsNone(s.config_path)

    def test_json_config_discovered_in_cwd(self) -> None:
        self.write(
            "asc-release-kit.json", json.dumps({"app": {"id": "1234567890", "platform": "mac_os"}, "_comment": {}})
        )
        s = load_settings(None, {}, cwd=self.tmp)
        self.assertEqual(s.app_id, "1234567890")
        self.assertEqual(s.platform, "MAC_OS")
        self.assertEqual(s.origin("app_id"), "config")
        self.assertEqual(s.config_origin, "cwd")
        explicit = load_settings(os.path.join(self.tmp, "asc-release-kit.json"), {}, cwd=self.tmp)
        self.assertEqual(explicit.config_origin, "flag")
        from_env = load_settings(None, {"ASC_CONFIG": os.path.join(self.tmp, "asc-release-kit.json")}, cwd="/")
        self.assertEqual(from_env.config_origin, "env")

    @unittest.skipUnless(TOML_OK, "TOML needs Python 3.11+ or tomli")
    def test_toml_config(self) -> None:
        path = self.write(
            "custom.toml",
            '[auth]\nkey_id = "ABC123DEFG"\ntoken_ttl_seconds = 600\n[reports]\nvendor_number = 87654321\n',
        )
        s = load_settings(path, {}, cwd=self.tmp)
        self.assertEqual(s.key_id, "ABC123DEFG")
        self.assertEqual(s.token_ttl_seconds, 600)
        self.assertEqual(s.vendor_number, "87654321")

    def test_precedence_flag_over_env_over_config(self) -> None:
        self.write("asc-release-kit.json", json.dumps({"app": {"id": "1111111111"}}))
        s = load_settings(None, {"ASC_APP_ID": "2222222222"}, cwd=self.tmp)
        self.assertEqual((s.app_id, s.origin("app_id")), ("2222222222", "env"))
        s = load_settings(None, {"ASC_APP_ID": "2222222222"}, {"app_id": "3333333333"}, cwd=self.tmp)
        self.assertEqual((s.app_id, s.origin("app_id")), ("3333333333", "flag"))

    def test_rejects_secrets_and_unknown_keys(self) -> None:
        for body in (
            {"auth": {"private_key": "x"}},
            {"auth": {"token": "x"}},
            {"app": {"nmae": "x"}},
            {"nope": {"a": 1}},
        ):
            path = self.write("bad.json", json.dumps(body))
            with self.subTest(body=body), self.assertRaises(UsageError):
                load_settings(path, {}, cwd=self.tmp)

    def test_settings_that_steer_credentials_are_refused_in_files(self) -> None:
        """A config file can be committed to a repository: it must not redirect the token or the key."""
        for section, key, value, env_name in (
            ("auth", "signer", "openssl", "ASC_SIGNER"),
            ("auth", "openssl", "./tools/openssl", "ASC_OPENSSL"),
            ("api", "base_url", "http://127.0.0.1:9999", "ASC_API_BASE_URL"),
        ):
            self.write("asc-release-kit.json", json.dumps({section: {key: value}}))
            with self.subTest(key=f"{section}.{key}"), self.assertRaises(UsageError) as ctx:
                load_settings(None, {}, cwd=self.tmp)  # discovered in the current directory
            self.assertIn(f"{section}.{key}", str(ctx.exception))
            self.assertIn(env_name, str(ctx.exception))
        os.remove(os.path.join(self.tmp, "asc-release-kit.json"))
        s = load_settings(None, {"ASC_SIGNER": "openssl", "ASC_OPENSSL": "/usr/bin/openssl"}, cwd=self.tmp)
        self.assertEqual((s.signer, s.origin("signer"), s.openssl), ("openssl", "env", "/usr/bin/openssl"))

    def test_journal_path_from_a_config_file_stays_in_the_project(self) -> None:
        for bad in ("/tmp/elsewhere.jsonl", "../outside.jsonl", "logs/../../outside.jsonl"):
            path = self.write("bad.json", json.dumps({"journal": {"path": bad}}))
            with self.subTest(path=bad), self.assertRaises(UsageError):
                load_settings(path, {}, cwd=self.tmp)
        path = self.write("ok.json", json.dumps({"journal": {"path": "logs/asc-journal.jsonl"}}))
        self.assertEqual(load_settings(path, {}, cwd=self.tmp).journal_path, "logs/asc-journal.jsonl")
        s = load_settings(None, {"ASC_JOURNAL": "/var/tmp/asc-journal.jsonl"}, cwd=self.tmp)
        self.assertEqual(s.journal_path, "/var/tmp/asc-journal.jsonl")

    def test_value_validation(self) -> None:
        for env in (
            {"ASC_APP_ID": "com.example"},
            {"ASC_TOKEN_TTL": "3600"},
            {"ASC_KEY_TYPE": "admin"},
            {"ASC_PLATFORM": "ANDROID"},
            {"ASC_SIGNER": "rsa"},
            {"ASC_VENDOR_NUMBER": "v-1"},
        ):
            with self.subTest(env=env), self.assertRaises(UsageError):
                load_settings(None, env, cwd=self.tmp)

    def test_explicit_missing_config(self) -> None:
        with self.assertRaises(UsageError):
            load_settings(os.path.join(self.tmp, "missing.json"), {}, cwd=self.tmp)
        with self.assertRaises(UsageError):
            load_settings(None, {"ASC_CONFIG": os.path.join(self.tmp, "missing.json")}, cwd=self.tmp)


class RedactorTests(unittest.TestCase):
    def test_literals_and_patterns(self) -> None:
        r = Redactor()
        r.add_secret("ABC123DEFG", "/home/someone/keys/AuthKey_ABC123DEFG.p8", None, "ab")
        header = "eyJhbGciOiJFUzI1NiJ9"
        token = header + "." + "eyJpc3MiOiJ4In0abc" + "." + "c2lnbmF0dXJlLXZhbHVlLWhlcmU"
        text = (
            f"key ABC123DEFG at /home/someone/keys/AuthKey_ABC123DEFG.p8 token {token} "
            "Authorization: Bearer abcdefghijklmnop mail you@example.com phone +1 555 0100 id 1234567890"
        )
        out = r.text(text)
        for secret in ("ABC123DEFG", "/home/someone", token, "abcdefghijklmnop", "you@example.com", "555 0100"):
            self.assertNotIn(secret, out)
        self.assertIn("1234567890", out, "plain numeric IDs are not phone numbers")
        self.assertIn(REDACTED, out)

    def test_numeric_secrets_are_masked_only_where_they_stand_alone(self) -> None:
        r = Redactor()
        r.add_secret("87654321")
        out = r.text("vendor 87654321, app 1234567890, other 187654321 and 876543210, (87654321)")
        self.assertEqual(out, "vendor [redacted], app 1234567890, other 187654321 and 876543210, ([redacted])")

    def test_private_key_block(self) -> None:
        r = Redactor()
        pem = "-----BEGIN " + "PRIVATE KEY-----\n" + "QUJD" * 20 + "\n-----END " + "PRIVATE KEY-----"
        self.assertEqual(r.text(f"oops {pem} end"), "oops [redacted private key] end")

    def test_record_masks_contact_fields_and_hashes_notes(self) -> None:
        r = Redactor()
        body = {
            "data": {
                "attributes": {
                    "contactEmail": "you@example.com",
                    "contactPhone": "+1 555 0100",
                    "demoAccountPassword": "hunter2",
                    "demoAccountRequired": True,
                    "notes": "Log in with demo / demo",
                    "description": "Write to you@example.com",
                    "contactLastName": None,
                }
            }
        }
        out = r.record(body)["data"]["attributes"]
        self.assertEqual(out["contactEmail"], REDACTED)
        self.assertEqual(out["demoAccountPassword"], REDACTED)
        self.assertIs(out["demoAccountRequired"], True)
        self.assertEqual(out["notes"], summarize_private_text("Log in with demo / demo"))
        self.assertNotIn("you@example.com", out["description"])
        self.assertIsNone(out["contactLastName"])
        self.assertEqual(body["data"]["attributes"]["contactEmail"], "you@example.com", "input must not be mutated")


if __name__ == "__main__":
    unittest.main()
