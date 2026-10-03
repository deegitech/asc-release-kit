"""The storefront table and its generated documentation stay correct and in sync."""

from __future__ import annotations

import importlib.util
import os
import unittest

from asc_release_kit.storefronts import ASC_LOCALES, STOREFRONTS, canonical_locale, coverage, find

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class StorefrontTableTests(unittest.TestCase):
    def test_sizes_and_uniqueness(self) -> None:
        self.assertEqual(len(STOREFRONTS), 175)
        self.assertEqual(len({s.code for s in STOREFRONTS}), 175)
        self.assertEqual(len(ASC_LOCALES), 50)

    def test_every_locale_is_known_and_used(self) -> None:
        used = set()
        for storefront in STOREFRONTS:
            for code in storefront.locales:
                self.assertIn(code, ASC_LOCALES, storefront.code)
                used.add(code)
            self.assertEqual(len(set(storefront.locales)), len(storefront.locales), storefront.code)
        self.assertEqual(used, set(ASC_LOCALES))

    def test_known_rows(self) -> None:
        usa = find("usa")
        assert usa is not None
        self.assertEqual(usa.default, "en-US")
        self.assertIn("es-MX", usa.additional)
        self.assertIn("pt-BR", usa.additional)
        self.assertEqual(find("Türkiye").locales, ("en-GB", "tr"))
        self.assertEqual(find("JPN").locales, ("ja", "en-US"))
        self.assertIsNone(find("Atlantis"))

    def test_canonical_locale(self) -> None:
        self.assertEqual(canonical_locale("EN-us"), "en-US")
        self.assertEqual(canonical_locale("zh-hans"), "zh-Hans")
        self.assertIsNone(canonical_locale("en"))

    def test_coverage(self) -> None:
        rows = {row.storefront.code: row for row in coverage(["en-GB", "tr"])}
        self.assertTrue(rows["TUR"].default_present)
        self.assertEqual(rows["TUR"].present, ("en-GB", "tr"))
        self.assertFalse(rows["USA"].present)
        self.assertGreater(sum(1 for row in rows.values() if row.default_present), 100)


class StorefrontDocTests(unittest.TestCase):
    def test_doc_matches_table(self) -> None:
        script = os.path.join(ROOT, "scripts", "render_storefront_doc.py")
        doc = os.path.join(ROOT, "docs", "storefront-locales.md")
        if not (os.path.exists(script) and os.path.exists(doc)):
            self.skipTest("source checkout only")
        spec = importlib.util.spec_from_file_location("render_storefront_doc", script)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        with open(doc, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), module.render(), "run scripts/render_storefront_doc.py")


if __name__ == "__main__":
    unittest.main()
