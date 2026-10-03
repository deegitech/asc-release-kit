"""The files in examples/ stay valid and use placeholders only."""

from __future__ import annotations

import importlib.util
import json
import os
import plistlib
import re
import sys
import unittest

from asc_release_kit.aso import load_denylist, load_document, validate_document
from asc_release_kit.config import load_settings
from asc_release_kit.release import load_review_notes, load_whats_new

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXAMPLES = os.path.join(ROOT, "examples")


def example(name: str) -> str:
    path = os.path.join(EXAMPLES, name)
    if not os.path.exists(path):
        raise unittest.SkipTest("examples/ is only in a source checkout")
    return path


class ExampleTests(unittest.TestCase):
    def test_aso_example_is_clean(self) -> None:
        doc = load_document(example("aso.json"))
        issues = validate_document(doc, load_denylist(example("denylist.txt")))
        self.assertEqual([i.as_dict() for i in issues], [])

    def test_denylist_example(self) -> None:
        terms = load_denylist(example("denylist.txt"))
        self.assertIn("#1", terms)
        self.assertIn("free", terms)
        self.assertFalse(any(t.startswith("# ") for t in terms))

    def test_whats_new_and_notes(self) -> None:
        self.assertEqual(set(load_whats_new(example("whats-new.json"))), {"en-US", "en-GB", "de-DE"})
        self.assertIn("Sandbox", load_review_notes(example("review-notes.txt")))

    def test_json_config(self) -> None:
        settings = load_settings(example("asc-release-kit.json"), {}, cwd=EXAMPLES)
        self.assertEqual(settings.app_id, "1234567890")
        self.assertEqual(settings.vendor_number, "87654321")
        self.assertEqual(settings.bundle_id, "com.example.mygame")
        self.assertNotIn("1234567890", settings.vendor_number, "placeholders must not overlap")

    def test_configs_hold_no_environment_only_settings(self) -> None:
        with open(example("asc-release-kit.json"), encoding="utf-8") as handle:
            doc = json.load(handle)
        self.assertNotIn("signer", doc.get("auth", {}))
        self.assertNotIn("api", doc)
        with open(example("asc-release-kit.toml"), encoding="utf-8") as handle:
            text = handle.read()
        self.assertIsNone(re.search(r"^\s*(signer|openssl|base_url)\s*=", text, re.M))

    @unittest.skipUnless(
        sys.version_info >= (3, 11) or importlib.util.find_spec("tomli") is not None, "needs tomllib or tomli"
    )
    def test_toml_config_matches_json(self) -> None:
        toml = load_settings(example("asc-release-kit.toml"), {}, cwd=EXAMPLES)
        json_ = load_settings(example("asc-release-kit.json"), {}, cwd=EXAMPLES)
        for name in (
            "key_id",
            "issuer_id",
            "key_type",
            "private_key_path",
            "app_id",
            "platform",
            "bundle_id",
            "vendor_number",
            "journal_path",
            "token_ttl_seconds",
            "signer",
        ):
            self.assertEqual(getattr(toml, name), getattr(json_, name), name)

    def test_export_options(self) -> None:
        with open(example("ExportOptions.plist"), "rb") as handle:
            options = plistlib.load(handle)
        self.assertEqual(options["method"], "app-store-connect")
        self.assertEqual(options["destination"], "upload")
        self.assertIs(options["manageAppVersionAndBuildNumber"], False)

    def test_workflow_example_passes_inputs_through_env(self) -> None:
        with open(example("github-actions-release.yml"), encoding="utf-8") as handle:
            text = handle.read()
        run_lines = [line for line in text.splitlines() if "run:" in line or line.startswith("          ")]
        self.assertFalse([line for line in run_lines if "${{ inputs." in line and "run:" in line])
        self.assertIn("secrets.ASC_PRIVATE_KEY", text)
        self.assertIsNone(re.search(r"-----BEGIN", text))

    def test_workflow_example_limits_the_key_to_the_steps_that_need_it(self) -> None:
        with open(example("github-actions-release.yml"), encoding="utf-8") as handle:
            lines = handle.read().splitlines()
        key_lines = [i for i, line in enumerate(lines) if "secrets.ASC_PRIVATE_KEY" in line]
        self.assertEqual(len(key_lines), 3, "doctor, plan and apply")
        for index in key_lines:
            self.assertTrue(lines[index].startswith("          "), "step-level env, not job-level")
        for line in lines:
            if line.strip().startswith("- uses:") or line.strip().startswith("uses:"):
                self.assertRegex(line, r"@[0-9a-f]{40}\b", "actions are pinned to a commit")


if __name__ == "__main__":
    unittest.main()
