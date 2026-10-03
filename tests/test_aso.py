"""ASO: validation rules, apply (including the auto-created localization 409) and storefront coverage."""

from __future__ import annotations

import json
import os
import stat
import unittest

from helpers import KitTestCase

from asc_release_kit.aso import (
    AsoDocument,
    compile_denylist,
    denylist_hits,
    validate_document,
    validate_locale,
)


def issues_for(values: dict, terms: tuple = (), locale: str = "en-US") -> list:
    return validate_locale(locale, values, compile_denylist(terms))


def messages(issues: list, level: str | None = None) -> str:
    return "\n".join(f"{i.level} {i.field}: {i.message}" for i in issues if level is None or i.level == level)


class ValidationRuleTests(unittest.TestCase):
    def test_limits(self) -> None:
        issues = issues_for(
            {
                "name": "N" * 31,
                "subtitle": "S" * 31,
                "promotionalText": "P" * 171,
                "description": "D" * 4001,
                "whatsNew": "W" * 4001,
            }
        )
        errors = messages(issues, "error")
        for field in ("name", "subtitle", "promotionalText", "description", "whatsNew"):
            self.assertIn(f"error {field}:", errors)

    def test_exact_limits_pass(self) -> None:
        issues = issues_for(
            {
                "name": "N" * 30,
                "subtitle": "S" * 30,
                "promotionalText": "P" * 170,
                "description": "D" * 4000,
                "keywords": "k" * 100,
            }
        )
        self.assertEqual(messages(issues, "error"), "")

    def test_keywords_are_measured_in_bytes(self) -> None:
        # 34 two-byte letters (ı and ğ) -> 68 bytes + 33 commas = 101 bytes
        keywords = ",".join(["ığ"[i % 2] for i in range(34)])
        issues = issues_for({"keywords": keywords}, locale="tr")
        self.assertIn("bytes, limit 100 bytes", messages(issues, "error"))

    def test_keyword_format_warnings(self) -> None:
        issues = issues_for({"keywords": ",puzzle, tiles,,Puzzle,io,"})
        text = messages(issues, "warning")
        self.assertIn("leading or trailing comma", text)
        self.assertIn("empty keyword", text)
        self.assertIn("spaces around commas waste 1 byte", text)
        self.assertIn("duplicate keyword(s): Puzzle", text)
        self.assertIn("two characters or fewer: io", text)

    def test_chinese_comma_is_a_separator(self) -> None:
        issues = issues_for({"keywords": "拼图，方块,休闲"}, locale="zh-Hans")
        self.assertEqual(messages(issues), "")

    def test_cross_field_duplicates(self) -> None:
        issues = issues_for(
            {"name": "Tile Garden", "subtitle": "Relaxing tile puzzles", "keywords": "garden,match,zen"}
        )
        text = messages(issues, "warning")
        self.assertIn("subtitle: repeats word(s) from the name: tile", text)
        self.assertIn(
            "keywords: repeat word(s) already in the name or subtitle, which Apple indexes anyway: garden", text
        )

    def test_denylist_whole_words_case_insensitive(self) -> None:
        patterns = compile_denylist(["Uno", "free", "#1", "Some Brand"])
        self.assertEqual(denylist_hits("An unordered list", patterns), [])
        self.assertEqual(denylist_hits("Better than UNO!", patterns), ["Uno"])
        self.assertEqual(denylist_hits("FREE puzzles", patterns), ["free"])
        self.assertEqual(denylist_hits("the #1 game", patterns), ["#1"])
        self.assertEqual(denylist_hits("like some brand games", patterns), ["Some Brand"])
        issues = issues_for({"subtitle": "Free tile fun", "description": "free text is fine here"}, ("free",))
        self.assertIn("error subtitle: contains denylisted term(s) 'free'", messages(issues))
        self.assertNotIn("description: contains", messages(issues))

    def test_urls_and_html(self) -> None:
        issues = issues_for(
            {
                "supportUrl": "example.com/help",
                "marketingUrl": "http://example.com",
                "privacyPolicyUrl": "https://example.com/privacy",
                "description": "<b>Bold</b> claim",
            }
        )
        text = messages(issues)
        self.assertIn("error supportUrl: is not a valid http(s) URL", text)
        self.assertIn("warning marketingUrl: uses http://", text)
        self.assertNotIn("privacyPolicyUrl", text)
        self.assertIn("warning description: looks like it contains HTML", text)

    def test_required_fields_cannot_be_blank(self) -> None:
        text = messages(issues_for({"name": "", "description": "", "keywords": "", "supportUrl": "", "whatsNew": ""}))
        for field in ("name", "description", "keywords", "supportUrl"):
            self.assertIn(f"error {field}: is empty", text)
        self.assertIn("warning whatsNew", text)
        self.assertIn("error name: must be at least 2", messages(issues_for({"name": "X"})))

    def test_emoji_counts_warning(self) -> None:
        issues = issues_for({"name": "Puzzle " + "\U0001f600" * 23})  # 30 code points, 53 UTF-16 units
        self.assertEqual(messages(issues, "error"), "")
        self.assertIn("UTF-16", messages(issues, "warning"))


class DocumentTests(KitTestCase):
    def test_load_issues(self) -> None:
        path = self.write(
            "aso.json",
            {
                "localizations": {
                    "en-us": {"name": "Tile Garden", "keyword": "typo"},
                    "xx-XX": {"name": "Nope"},
                    "_note": "ignored",
                }
            },
        )
        res = self.run_cli("aso", "validate", path)
        self.assertEqual(res.code, 1)
        self.assertIn("write the locale as 'en-US'", res.out)
        self.assertIn("unknown field", res.out)
        self.assertIn("unknown locale code", res.out)

    def test_invalid_json_is_usage_error(self) -> None:
        path = self.write("bad.json", '{"localizations": {')
        res = self.run_cli("aso", "validate", path)
        self.assertEqual(res.code, 2)
        self.assertIn("invalid JSON at line 1", res.err)

    def test_strict_and_json_output(self) -> None:
        path = self.write("aso.json", {"localizations": {"en-US": {"keywords": "puzzle, tiles"}}})
        self.assertEqual(self.run_cli("aso", "validate", path).code, 0)
        self.assertEqual(self.run_cli("aso", "validate", path, "--strict").code, 1)
        doc = self.run_cli("aso", "validate", path, "--json").json()
        self.assertEqual(doc["result"]["warnings"], 1)
        self.assertEqual(doc["result"]["locales"]["en-US"]["keywords"], {"used": 13, "limit": 100, "bytes": 1})

    def test_denylist_file_and_inline(self) -> None:
        deny = self.write("deny.txt", "# competitors\nOtherGame\n\n")
        path = self.write(
            "aso.json",
            {
                "denylist": ["BrandX"],
                "localizations": {
                    "en-US": {"name": "Tile Garden", "subtitle": "Better than OtherGame", "keywords": "brandx,tiles"}
                },
            },
        )
        res = self.run_cli("aso", "validate", path, "--denylist", deny)
        self.assertEqual(res.code, 1)
        self.assertIn("'OtherGame'", res.out)
        self.assertIn("'BrandX'", res.out)

    def test_validate_document_api(self) -> None:
        doc = AsoDocument("x.json", {}, {"en-US": {"name": "Tile Garden"}})
        self.assertEqual(validate_document(doc), [])


ASO_DOC = {
    "app": {"version": "1.1.0"},
    "localizations": {
        "en-US": {
            "name": "Example Game: Tiles",
            "subtitle": "Calm puzzles",
            "keywords": "puzzle,tiles,calm,zen",
            "promotionalText": "New levels every week.",
            "description": "A calm tile puzzle with 200 levels.",
            "whatsNew": "New levels.",
            "supportUrl": "https://example.com/support",
            "privacyPolicyUrl": "https://example.com/privacy",
        },
        "en-GB": {
            "name": "Example Game: Tiles",
            "subtitle": "Calm puzzles",
            "keywords": "puzzle,tiles,calm,zen",
            "description": "A calm tile puzzle with 200 levels.",
            "whatsNew": "New levels.",
            "supportUrl": "https://example.com/support",
            "privacyPolicyUrl": "https://example.com/privacy",
        },
    },
}


class ApplyTests(KitTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.vid = self.mock.seed_editable_version("1.1.0", build="build-42")
        self.path = self.write("aso.json", ASO_DOC)

    def loc(self, type_: str, parent_rel: str, parent: str, locale: str) -> dict:
        for rid in self.mock.children(type_, parent_rel, parent):
            if self.mock.attrs(type_, rid)["locale"] == locale:
                return self.mock.attrs(type_, rid)
        return {}

    def test_plan_writes_nothing(self) -> None:
        res = self.run_cli("aso", "apply", self.path)
        self.assertEqual(res.code, 0, res.err)
        self.assertIn("en-GB: create App Info localization", res.out)
        self.assertIn("or update it if App Store Connect creates it automatically", res.out)
        self.assertNoWrites()

    def test_apply_handles_auto_created_localization_and_verifies(self) -> None:
        res = self.run_cli("aso", "apply", self.path, "--apply")
        self.assertEqual(res.code, 0, res.out + res.err)
        info_id = f"info-{self.vid}"
        self.assertEqual(self.loc("appInfoLocalizations", "appInfo", info_id, "en-GB")["name"], "Example Game: Tiles")
        en_gb = self.loc("appStoreVersionLocalizations", "appStoreVersion", self.vid, "en-GB")
        self.assertEqual(en_gb["keywords"], "puzzle,tiles,calm,zen")
        self.assertEqual(
            self.loc("appStoreVersionLocalizations", "appStoreVersion", self.vid, "en-US")["whatsNew"], "New levels."
        )
        self.assertIn("en-GB: 7 field(s) match", res.out)
        # Apple created the version localization itself, so the kit must not have POSTed one.
        self.assertNotIn("POST /v1/appStoreVersionLocalizations", self.mock.write_paths())
        verify = [e for e in self.journal() if e["event"] == "verify"]
        self.assertTrue(verify and verify[-1]["ok"])

    def test_409_on_create_falls_back_to_update(self) -> None:
        self.mock.auto_version_localization = False
        original = self.mock.create_version_loc

        def create_with_conflict(**kw):
            # App Store Connect creates it "behind our back" between the read and the POST.
            body = kw["body"]
            locale = body["data"]["attributes"]["locale"]
            self.mock.add(
                "appStoreVersionLocalizations",
                {"locale": locale, "description": None, "keywords": None},
                {"appStoreVersion": ("appStoreVersions", self.vid)},
            )
            return original(**kw)

        self.mock.routes = [(m, rx, create_with_conflict if fn == original else fn) for m, rx, fn in self.mock.routes]
        res = self.run_cli("aso", "apply", self.path, "--apply")
        self.assertEqual(res.code, 0, res.out + res.err)
        self.assertIn("409", res.out)
        self.assertEqual(
            self.loc("appStoreVersionLocalizations", "appStoreVersion", self.vid, "en-GB")["description"],
            "A calm tile puzzle with 200 levels.",
        )

    def test_second_apply_is_a_no_op(self) -> None:
        self.assertEqual(self.run_cli("aso", "apply", self.path, "--apply").code, 0)
        writes = len(self.mock.writes())
        res = self.run_cli("aso", "apply", self.path, "--apply")
        self.assertEqual(res.code, 0, res.err)
        self.assertEqual(len(self.mock.writes()), writes)
        self.assertIn("en-US: up to date", res.out)

    def test_validation_errors_stop_before_any_request(self) -> None:
        doc = json.loads(json.dumps(ASO_DOC))
        doc["localizations"]["en-US"]["name"] = "N" * 40
        res = self.run_cli("aso", "apply", self.write("bad.json", doc), "--apply")
        self.assertEqual(res.code, 1)
        self.assertEqual(self.mock.requests, [])

    def test_live_version_only_accepts_anytime_fields(self) -> None:
        res = self.run_cli("aso", "apply", self.path, "--version", "1.0.0", "--apply")
        self.assertEqual(res.code, 4)
        self.assertIn("locked fields requested", res.err)
        res = self.run_cli(
            "aso",
            "apply",
            self.path,
            "--version",
            "1.0.0",
            "--fields",
            "promotionalText",
            "--locales",
            "en-US",
            "--apply",
        )
        self.assertEqual(res.code, 0, res.out + res.err)
        self.assertEqual(
            self.loc("appStoreVersionLocalizations", "appStoreVersion", "ver-live", "en-US")["promotionalText"],
            "New levels every week.",
        )

    def test_unknown_field_and_locale_filters(self) -> None:
        self.assertEqual(self.run_cli("aso", "apply", self.path, "--fields", "nmae").code, 2)
        self.assertEqual(self.run_cli("aso", "apply", self.path, "--locales", "fr-FR").code, 2)
        self.assertEqual(self.run_cli("aso", "apply", self.path, "--locales", "xx-YY").code, 2)
        res = self.run_cli("aso", "apply", self.path, "--locales", "en-us")  # any case, like in the file
        self.assertEqual(res.code, 0, res.err)
        self.assertNotIn("en-GB", res.out)

    def test_app_in_the_file_must_match_the_configured_app(self) -> None:
        doc = json.loads(json.dumps(ASO_DOC))
        doc["app"]["id"] = "1234567891"  # ASC_APP_ID is 1234567890
        path = self.write("other-app.json", doc)
        res = self.run_cli("aso", "apply", path)
        self.assertEqual(res.code, 2)
        self.assertIn("app 1234567891", res.err)
        self.assertIn("--app", res.err)
        self.assertEqual(self.mock.requests, [], "nothing is read for the wrong app")
        version_reads = lambda: [r["path"] for r in self.mock.requests if r["path"].endswith("/appStoreVersions")]  # noqa: E731
        self.run_cli("aso", "apply", path, "--app", "1234567891")  # --app decides
        self.assertEqual(version_reads(), ["/v1/apps/1234567891/appStoreVersions"])
        os.environ.pop("ASC_APP_ID")  # nothing configured: the file's app is used
        self.run_cli("aso", "apply", path)
        self.assertEqual(version_reads()[-1], "/v1/apps/1234567891/appStoreVersions")

    def test_platform_in_the_file_must_match_the_configured_platform(self) -> None:
        doc = json.loads(json.dumps(ASO_DOC))
        doc["app"]["platform"] = "mac_os"
        path = self.write("mac.json", doc)
        os.environ["ASC_PLATFORM"] = "IOS"
        res = self.run_cli("aso", "apply", path)
        self.assertEqual(res.code, 2)
        self.assertIn("platform MAC_OS", res.err)
        os.environ.pop("ASC_PLATFORM")  # only the default: the file wins
        res = self.run_cli("aso", "apply", path)
        self.assertIn("no MAC_OS version 1.1.0", res.err)

    def test_missing_version(self) -> None:
        res = self.run_cli("aso", "apply", self.path, "--version", "9.9.9")
        self.assertEqual(res.code, 4)
        self.assertIn("no IOS version 9.9.9", res.err)

    def test_mismatch_after_write_fails_verification(self) -> None:
        original = self.mock.patch_simple("appStoreVersionLocalizations")

        def lossy_patch(rid, body, **kw):
            body["data"]["attributes"].pop("keywords", None)  # the server silently drops a field
            return original(rid=rid, body=body, **kw)

        self.mock.routes = [
            (m, rx, lossy_patch if (m == "PATCH" and "appStoreVersionLocalizations" in rx.pattern) else fn)
            for m, rx, fn in self.mock.routes
        ]
        res = self.run_cli("aso", "apply", self.path, "--apply")
        self.assertEqual(res.code, 1)
        self.assertIn("mismatch in keywords", res.out)


class StorefrontCommandTests(KitTestCase):
    def test_overlap_report(self) -> None:
        doc = {
            "localizations": {
                "en-US": {"keywords": "puzzle,tiles"},
                "es-MX": {"keywords": "rompecabezas,torre"},
                "pt-BR": {"keywords": "quebra,torre"},
            }
        }
        res = self.run_cli("aso", "storefronts", self.write("aso.json", doc), "--storefront", "USA")
        self.assertEqual(res.code, 0, res.err)
        self.assertIn("es-MX and pt-BR both apply here and repeat keyword(s): torre", res.out)

    def test_compact_by_default(self) -> None:
        doc = {"localizations": {"en-GB": {"keywords": "puzzle,tiles"}, "de-DE": {"keywords": "puzzle,ruhe"}}}
        path = self.write("aso.json", doc)
        res = self.run_cli("aso", "storefronts", path)
        self.assertEqual(res.code, 0, res.err)
        self.assertIn("DEU  Germany", res.out)  # overlap: puzzle
        self.assertNotIn("GBR  United Kingdom", res.out)  # no overlap there, listed only with --all
        self.assertIn("GBR  United Kingdom", self.run_cli("aso", "storefronts", path, "--all").out)

    def test_locales_flag_and_json(self) -> None:
        doc = self.run_cli("aso", "storefronts", "--locales", "en-GB,tr", "--json").json()
        rows = {row["storefront"]: row for row in doc["result"]["storefronts"]}
        self.assertEqual(rows["TUR"]["yours"], ["en-GB", "tr"])
        self.assertTrue(rows["TUR"]["defaultIsYours"])
        self.assertFalse(rows["USA"]["defaultIsYours"] if "USA" in rows else False)

    def test_storefront_with_none_of_your_locales(self) -> None:
        res = self.run_cli("aso", "storefronts", "--locales", "en-US", "--storefront", "TUR")
        self.assertEqual(res.code, 0, res.err)
        self.assertIn("none of your locales: shoppers see your primary language", res.out)
        self.assertNotIn("one of your other languages", res.out)

    def test_unknown_inputs(self) -> None:
        self.assertEqual(self.run_cli("aso", "storefronts", "--locales", "xx").code, 2)
        self.assertEqual(self.run_cli("aso", "storefronts", "--locales", "en-US", "--storefront", "Atlantis").code, 2)
        self.assertEqual(self.run_cli("aso", "storefronts").code, 2)


class PullTests(KitTestCase):
    def test_pull_writes_a_private_valid_file_without_writing_to_apple(self) -> None:
        out = os.path.join(self.tmp, "pulled.json")
        res = self.run_cli("aso", "pull", "--out", out)
        self.assertEqual(res.code, 0, res.out + res.err)
        self.assertNoWrites()
        with open(out, encoding="utf-8") as handle:
            doc = json.load(handle)
        self.assertEqual(doc["app"], {"id": "1234567890", "version": "1.0.0", "platform": "IOS"})
        self.assertEqual(sorted(doc["localizations"]), ["de-DE", "en-US"])
        en = doc["localizations"]["en-US"]
        self.assertEqual(en["name"], "Example Game")
        self.assertEqual(en["keywords"], "puzzle,tiles,calm")
        self.assertEqual(en["privacyPolicyUrl"], "https://example.com/privacy")
        self.assertNotIn("whatsNew", en)
        self.assertNotIn("privacyChoicesUrl", en, "empty fields are left out")
        if os.name == "posix":
            self.assertEqual(stat.S_IMODE(os.stat(out).st_mode), 0o600)
        self.assertIn("Nothing was changed in App Store Connect", res.out)
        self.assertEqual(self.run_cli("aso", "validate", out).code, 0)

    def test_pull_refuses_to_overwrite_without_force(self) -> None:
        out = self.write("aso.json", {"keep": "me"})
        res = self.run_cli("aso", "pull", "--out", out)
        self.assertEqual(res.code, 2)
        self.assertIn("--force", res.err)
        with open(out, encoding="utf-8") as handle:
            self.assertEqual(json.load(handle), {"keep": "me"})
        self.assertEqual(self.run_cli("aso", "pull", "--out", out, "--force").code, 0)

    def test_pull_prefers_the_editable_version_and_round_trips(self) -> None:
        self.mock.seed_editable_version("1.1.0", build="build-42")
        out = os.path.join(self.tmp, "editable.json")
        res = self.run_cli("aso", "pull", "--out", out, "--json")
        self.assertEqual(res.code, 0, res.err)
        self.assertEqual(res.json()["result"]["version"], "1.1.0")
        res = self.run_cli("aso", "apply", out)  # what was pulled is what App Store Connect holds
        self.assertEqual(res.code, 0, res.out + res.err)
        self.assertIn("Plan: 0 change(s)", res.out)


if __name__ == "__main__":
    unittest.main()
