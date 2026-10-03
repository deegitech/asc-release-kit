"""End-to-end tests for `release` against the mock App Store Connect."""

from __future__ import annotations

import json
import os
import unittest

from helpers import KitTestCase

from asc_release_kit.errors import UsageError
from asc_release_kit.release import load_whats_new, parse_instant, same_instant, version_in_submission


class ReleasePlanTests(KitTestCase):
    def test_plan_for_new_version_writes_nothing(self) -> None:
        wn = self.write("whats-new.json", {"en-US": "Bug fixes.", "de-DE": "Fehlerbehebungen."})
        res = self.run_cli("release", "--version", "1.1.0", "--build", "42", "--whats-new", wn)
        self.assertEqual(res.code, 0, res.err)
        self.assertIn("PLAN", res.out)
        self.assertIn("create version 1.1.0 with build 42", res.out)
        self.assertIn("what's new [de-DE]", res.out)
        self.assertNoWrites()
        self.assertEqual(self.journal(), [])

    def test_plan_json_output(self) -> None:
        res = self.run_cli("release", "--version", "1.1.0", "--build", "42", "--json")
        self.assertEqual(res.code, 0, res.err)
        doc = res.json()
        self.assertEqual(doc["command"], "release")
        self.assertEqual(doc["mode"], "plan")
        self.assertGreaterEqual(doc["changes"], 1)

    def test_build_not_valid_blocks(self) -> None:
        res = self.run_cli("release", "--version", "1.1.0", "--build", "43", "--apply")
        self.assertEqual(res.code, 4)
        self.assertIn("not VALID", res.err)
        self.assertNoWrites()

    def test_wait_for_build_polls_until_valid(self) -> None:
        polls = {"n": 0}
        original = self.mock.list_builds

        def flip(**kw):  # becomes VALID on the third poll
            polls["n"] += 1
            if polls["n"] >= 3:
                self.mock.attrs("builds", "build-43")["processingState"] = "VALID"
            return original(**kw)

        self.mock.routes = [(m, rx, flip if fn == original else fn) for m, rx, fn in self.mock.routes]
        res = self.run_cli("release", "--version", "1.1.0", "--build", "43", "--wait-for-build", "5")
        self.assertEqual(res.code, 0, res.err)
        self.assertGreaterEqual(polls["n"], 3)
        self.assertIn("polling", res.out)

    def test_missing_build_message(self) -> None:
        res = self.run_cli("release", "--version", "9.9.9", "--build", "999")
        self.assertEqual(res.code, 4)
        self.assertIn("not found", res.err)

    def test_wrong_app_id_is_named_as_a_cause(self) -> None:
        os.environ["ASC_APP_ID"] = "1234567899"  # a number that isn't this app
        res = self.run_cli("release", "--version", "1.1.0", "--build", "42")
        self.assertEqual(res.code, 4, res.err)
        self.assertIn("is not VALID (not found)", res.err)
        self.assertIn("ASC_APP_ID / --app points to another app", res.err)
        self.assertIn("fix: compare the numbers with the app's TestFlight tab", res.err)

    def test_whats_new_locale_missing_on_app_blocks_before_writing(self) -> None:
        wn = self.write("whats-new.json", {"en-US": "Fixes.", "ja": "修正"})
        res = self.run_cli("release", "--version", "1.1.0", "--build", "42", "--whats-new", wn, "--apply")
        self.assertEqual(res.code, 4)
        self.assertIn("ja", res.err)
        self.assertNoWrites()

    def test_missing_whats_new_is_a_warning_without_submit(self) -> None:
        res = self.run_cli("release", "--version", "1.1.0", "--build", "42")
        self.assertEqual(res.code, 0, res.err)
        self.assertIn("What's New would be empty for de-DE, en-US", res.out)
        self.assertNoWrites()
        res = self.run_cli("release", "--version", "1.1.0", "--build", "42", "--submit")
        self.assertEqual(res.code, 0, res.err)
        self.assertIn("(--submit will refuse)", res.out)

    def test_open_submission_is_reported_in_the_plan(self) -> None:
        self.mock.add(
            "reviewSubmissions",
            {"platform": "IOS", "state": "WAITING_FOR_REVIEW", "submittedDate": None},
            {"app": ("apps", "1234567890")},
        )
        wn = self.write("whats-new.json", {"en-US": "Fixes.", "de-DE": "Fehlerbehebungen."})
        res = self.run_cli("release", "--version", "1.1.0", "--build", "42", "--whats-new", wn, "--submit")
        self.assertEqual(res.code, 0, res.err)
        self.assertIn("is WAITING_FOR_REVIEW", res.out)
        self.assertIn("--submit will refuse", res.out)

    def test_earliest_release_date_must_be_a_full_timestamp(self) -> None:
        for value in ("2030-11-01", "next tuesday", "2030-11-01T09:00:00"):
            res = self.run_cli(
                "release", "--version", "1.1.0", "--build", "42", "--release-type", "SCHEDULED",
                "--earliest-release-date", value,
            )  # fmt: skip
            with self.subTest(value=value):
                self.assertEqual(res.code, 2)
                self.assertIn("time zone", res.err)


class ReleaseApplyTests(KitTestCase):
    def _apply(self, *extra: str):
        wn = self.write("whats-new.json", {"en-US": "Bug fixes and a new level.", "de-DE": "Fehlerbehebungen."})
        return self.run_cli("release", "--version", "1.1.0", "--build", "42", "--whats-new", wn, "--apply", *extra)

    def test_apply_creates_and_verifies_without_submitting(self) -> None:
        res = self._apply()
        self.assertEqual(res.code, 0, res.out + res.err)
        vid = self.version_by_string("1.1.0")
        self.assertEqual(self.mock.db["appStoreVersions"][vid]["rels"]["build"], ("builds", "build-42"))
        whats_new = {
            self.mock.attrs("appStoreVersionLocalizations", rid)["locale"]: self.mock.attrs(
                "appStoreVersionLocalizations", rid
            )["whatsNew"]
            for rid in self.mock.children("appStoreVersionLocalizations", "appStoreVersion", vid)
        }
        self.assertEqual(whats_new, {"en-US": "Bug fixes and a new level.", "de-DE": "Fehlerbehebungen."})
        review_ids = self.mock.children("appStoreReviewDetails", "appStoreVersion", vid)
        self.assertEqual(len(review_ids), 1)
        self.assertEqual(self.mock.attrs("appStoreReviewDetails", review_ids[0])["contactEmail"], "you@example.com")
        self.assertEqual(len(self.mock.children("gameCenterAppVersions", "appStoreVersion", vid)), 1)
        self.assertIn("Read-back verification", res.out)
        self.assertIn("Not submitted", res.out)
        self.assertFalse(self.mock.db["reviewSubmissions"])

    def test_contact_details_never_printed_or_journaled(self) -> None:
        res = self._apply()
        self.assertEqual(res.code, 0, res.err)
        blob = res.out + res.err + json.dumps(self.journal())
        for secret in ("you@example.com", "555 0100", "Appleseed", "Tap Play to start"):
            self.assertNotIn(secret, blob)
        review_writes = [e for e in self.journal() if e.get("path") == "/v1/appStoreReviewDetails"]
        self.assertEqual(len(review_writes), 1)
        attrs = review_writes[0]["request"]["data"]["attributes"]
        self.assertEqual(attrs["contactEmail"], "[redacted]")
        self.assertTrue(attrs["notes"].startswith("[redacted:"))

    def test_second_run_is_idempotent(self) -> None:
        self.assertEqual(self._apply().code, 0)
        before = len(self.mock.writes())
        res = self._apply()
        self.assertEqual(res.code, 0, res.err)
        self.assertEqual(len(self.mock.writes()), before, "a converged release must not write again")

    def test_game_center_link_reenabled_after_build_change(self) -> None:
        vid = self.mock.seed_editable_version("1.1.0", build="build-44")
        self.mock.add("gameCenterAppVersions", {"enabled": True}, {"appStoreVersion": ("appStoreVersions", vid)})
        self.mock.add(
            "appStoreReviewDetails",
            {
                "contactFirstName": "Jane",
                "contactLastName": "Appleseed",
                "contactPhone": "+1 555 0100",
                "contactEmail": "you@example.com",
                "notes": "n",
            },
            {"appStoreVersion": ("appStoreVersions", vid)},
        )
        res = self._apply()
        self.assertEqual(res.code, 0, res.out + res.err)
        gc = self.mock.children("gameCenterAppVersions", "appStoreVersion", vid)
        self.assertTrue(self.mock.attrs("gameCenterAppVersions", gc[0])["enabled"])
        self.assertIn("re-enable", res.out)

    def test_review_notes_from_file(self) -> None:
        notes = self.write("notes.txt", "Use the demo level.\r\n")
        res = self._apply("--review-notes", notes)
        self.assertEqual(res.code, 0, res.err)
        vid = self.version_by_string("1.1.0")
        rid = self.mock.children("appStoreReviewDetails", "appStoreVersion", vid)[0]
        self.assertEqual(self.mock.attrs("appStoreReviewDetails", rid)["notes"], "Use the demo level.")

    def test_release_type_and_schedule(self) -> None:
        res = self._apply("--release-type", "SCHEDULED", "--earliest-release-date", "2030-11-01T09:00:00Z")
        self.assertEqual(res.code, 0, res.err)
        a = self.mock.attrs("appStoreVersions", self.version_by_string("1.1.0"))
        self.assertEqual(a["releaseType"], "SCHEDULED")
        self.assertEqual(a["earliestReleaseDate"], "2030-11-01T09:00:00Z")
        self.assertIn("earliest release date reads back as 2030-11-01T09:00:00Z", res.out)

    def test_reformatted_release_date_is_the_same_moment(self) -> None:
        args = ("--release-type", "SCHEDULED", "--earliest-release-date", "2030-11-01T09:00:00Z")
        self.assertEqual(self._apply(*args).code, 0)
        vid = self.version_by_string("1.1.0")
        # App Store Connect may hand the same moment back in another notation.
        self.mock.attrs("appStoreVersions", vid)["earliestReleaseDate"] = "2030-11-01T02:00:00-07:00"
        before = len(self.mock.writes())
        res = self._apply(*args)
        self.assertEqual(res.code, 0, res.out + res.err)
        self.assertEqual(len(self.mock.writes()), before, "same instant: nothing to write")

    def test_release_date_that_did_not_stick_fails_verification(self) -> None:
        original = self.mock.patch_version

        def lossy(vid, body, **kw):
            body["data"].get("attributes", {}).pop("earliestReleaseDate", None)
            return original(vid=vid, body=body, **kw)

        self.mock.routes = [(m, rx, lossy if fn == original else fn) for m, rx, fn in self.mock.routes]
        vid = self.mock.seed_editable_version("1.1.0", build="build-42")
        self.mock.add(
            "appStoreReviewDetails",
            {"contactFirstName": "J", "contactLastName": "A", "contactPhone": "+1 555 0100", "contactEmail": "x@y.z"},
            {"appStoreVersion": ("appStoreVersions", vid)},
        )
        res = self._apply("--release-type", "SCHEDULED", "--earliest-release-date", "2030-11-01T09:00:00Z")
        self.assertEqual(res.code, 1, res.out + res.err)
        self.assertIn("earliest release date reads back as None", res.out)

    def test_rename_that_did_not_stick_fails_verification(self) -> None:
        self.mock.seed_editable_version("1.0.5", build="build-42")
        original = self.mock.patch_version

        def ignore_rename(vid, body, **kw):
            body["data"].get("attributes", {}).pop("versionString", None)
            return original(vid=vid, body=body, **kw)

        self.mock.routes = [(m, rx, ignore_rename if fn == original else fn) for m, rx, fn in self.mock.routes]
        res = self._apply("--reuse-editable")
        self.assertEqual(res.code, 1, res.out + res.err)
        self.assertIn("version string reads back as 1.0.5", res.out)

    def test_scheduled_without_date_is_usage_error(self) -> None:
        res = self._apply("--release-type", "SCHEDULED")
        self.assertEqual(res.code, 2)

    def test_existing_editable_version_needs_reuse_flag(self) -> None:
        self.mock.seed_editable_version("1.0.5")
        res = self._apply()
        self.assertEqual(res.code, 4)
        self.assertIn("--reuse-editable", res.err)
        res = self._apply("--reuse-editable")
        self.assertEqual(res.code, 0, res.out + res.err)
        self.assertEqual(
            self.mock.attrs("appStoreVersions", self.version_by_string("1.1.0"))["appVersionState"],
            "PREPARE_FOR_SUBMISSION",
        )

    def test_in_review_version_is_a_no_op(self) -> None:
        vid = self.mock.seed_editable_version("1.1.0", build="build-42")
        self.mock.attrs("appStoreVersions", vid).update(
            appStoreState="WAITING_FOR_REVIEW", appVersionState="WAITING_FOR_REVIEW"
        )
        res = self._apply("--submit")
        self.assertEqual(res.code, 0, res.err)
        self.assertIn("already WAITING_FOR_REVIEW", res.out)
        self.assertNoWrites()

    def test_partial_whats_new_blocks_submit_before_any_write(self) -> None:
        wn = self.write("whats-new.json", {"en-US": "Only English."})
        res = self.run_cli("release", "--version", "1.1.0", "--build", "42", "--whats-new", wn, "--apply", "--submit")
        self.assertEqual(res.code, 4, res.out + res.err)
        self.assertIn("What's New would be empty for de-DE", res.err)
        self.assertIn("Nothing was written", res.err)
        self.assertNoWrites()

    def test_partial_whats_new_without_submit_prepares_the_version(self) -> None:
        wn = self.write("whats-new.json", {"en-US": "Only English."})
        res = self.run_cli("release", "--version", "1.1.0", "--build", "42", "--whats-new", wn, "--apply")
        self.assertEqual(res.code, 0, res.out + res.err)
        self.assertIn("what's new [de-DE] is empty; fill it in before submitting", res.out)
        self.assertIn("Ready. Not submitted", res.out)

    def test_whats_new_text_covers_the_other_locales(self) -> None:
        wn = self.write("whats-new.json", {"en-US": "New levels."})
        res = self.run_cli(
            "release", "--version", "1.1.0", "--build", "42", "--whats-new", wn,
            "--whats-new-text", "Bug fixes.", "--apply", "--submit",
        )  # fmt: skip
        self.assertEqual(res.code, 0, res.out + res.err)
        vid = self.version_by_string("1.1.0")
        texts = {
            self.mock.attrs("appStoreVersionLocalizations", rid)["locale"]: self.mock.attrs(
                "appStoreVersionLocalizations", rid
            )["whatsNew"]
            for rid in self.mock.children("appStoreVersionLocalizations", "appStoreVersion", vid)
        }
        self.assertEqual(texts, {"en-US": "New levels.", "de-DE": "Bug fixes."})

    def test_star_entry_in_the_file_is_the_default(self) -> None:
        wn = self.write("whats-new.json", {"*": "Bug fixes.", "de-DE": "Fehlerbehebungen."})
        res = self.run_cli("release", "--version", "1.1.0", "--build", "42", "--whats-new", wn, "--apply")
        self.assertEqual(res.code, 0, res.out + res.err)
        vid = self.version_by_string("1.1.0")
        texts = {
            self.mock.attrs("appStoreVersionLocalizations", rid)["locale"]: self.mock.attrs(
                "appStoreVersionLocalizations", rid
            )["whatsNew"]
            for rid in self.mock.children("appStoreVersionLocalizations", "appStoreVersion", vid)
        }
        self.assertEqual(texts, {"en-US": "Bug fixes.", "de-DE": "Fehlerbehebungen."})

    def test_export_compliance_missing_is_a_warning_in_plan_mode(self) -> None:
        res = self.run_cli("release", "--version", "1.1.0", "--build", "44", "--submit")
        self.assertEqual(res.code, 0, res.err)
        self.assertIn("--submit will refuse", res.out)

    def test_export_compliance_missing_blocks_submit(self) -> None:
        wn = self.write("whats-new.json", {"en-US": "Fixes.", "de-DE": "Fixes."})
        res = self.run_cli("release", "--version", "1.1.0", "--build", "44", "--whats-new", wn, "--apply", "--submit")
        self.assertEqual(res.code, 4)
        self.assertIn("export compliance", res.err)
        self.assertNoWrites()


class SubmissionTests(KitTestCase):
    def _submit(self):
        wn = self.write("whats-new.json", {"en-US": "Fixes.", "de-DE": "Fehlerbehebungen."})
        return self.run_cli("release", "--version", "1.1.0", "--build", "42", "--whats-new", wn, "--apply", "--submit")

    def test_submit_happy_path(self) -> None:
        res = self._submit()
        self.assertEqual(res.code, 0, res.out + res.err)
        vid = self.version_by_string("1.1.0")
        self.assertEqual(self.mock.attrs("appStoreVersions", vid)["appVersionState"], "WAITING_FOR_REVIEW")
        (sid,) = self.mock.db["reviewSubmissions"]
        self.assertEqual(self.mock.attrs("reviewSubmissions", sid)["state"], "WAITING_FOR_REVIEW")
        self.assertIn("is in review submission", res.out)
        events = [e for e in self.journal() if e["event"] == "verify"]
        self.assertTrue(any(e.get("target") == "submission-items" and e["ok"] for e in events))

    def test_refuses_to_submit_when_version_item_failed(self) -> None:
        """The real lesson: adding the version 500s but other items are in the draft."""
        sid = self.mock.add(
            "reviewSubmissions",
            {"platform": "IOS", "state": "READY_FOR_REVIEW", "submittedDate": None},
            {"app": ("apps", "1234567890")},
        )
        self.mock.add(
            "reviewSubmissionItems",
            {"state": "READY_FOR_REVIEW"},
            {"reviewSubmission": ("reviewSubmissions", sid), "appEvent": ("appEvents", "event-1")},
        )
        self.mock.fail("POST", r"^/v1/reviewSubmissionItems$", status=500, times=5)
        res = self._submit()
        self.assertEqual(res.code, 1, res.out + res.err)
        self.assertIn("NOT SUBMITTED", res.err)
        self.assertEqual(self.mock.attrs("reviewSubmissions", sid)["state"], "READY_FOR_REVIEW")
        self.assertFalse(
            [w for w in self.mock.writes() if w["method"] == "PATCH" and w["path"].startswith("/v1/reviewSubmissions/")]
        )
        events = [e for e in self.journal() if e.get("target") == "submission-items"]
        self.assertEqual(events[-1]["ok"], False)

    def test_item_500_that_actually_landed_is_accepted(self) -> None:
        """A 5xx on the POST may still have created the item: the re-read decides."""
        original = self.mock.create_item

        def create_then_fail(**kw):
            original(**kw)
            from mock_asc import error

            return error(500, "UNEXPECTED_ERROR", "boom")

        self.mock.routes = [(m, rx, create_then_fail if fn == original else fn) for m, rx, fn in self.mock.routes]
        res = self._submit()
        self.assertEqual(res.code, 0, res.out + res.err)
        self.assertIn("reading the submission back", res.out)

    def test_open_submission_blocks_before_any_write(self) -> None:
        self.mock.add(
            "reviewSubmissions",
            {"platform": "IOS", "state": "UNRESOLVED_ISSUES", "submittedDate": None},
            {"app": ("apps", "1234567890")},
        )
        res = self._submit()
        self.assertEqual(res.code, 4)
        self.assertIn("UNRESOLVED_ISSUES", res.err)
        self.assertIn("Nothing was written", res.err)
        self.assertNoWrites()

    def test_reuses_existing_draft_with_other_items(self) -> None:
        sid = self.mock.add(
            "reviewSubmissions",
            {"platform": "IOS", "state": "READY_FOR_REVIEW", "submittedDate": None},
            {"app": ("apps", "1234567890")},
        )
        self.mock.add(
            "reviewSubmissionItems",
            {"state": "READY_FOR_REVIEW"},
            {"reviewSubmission": ("reviewSubmissions", sid), "appEvent": ("appEvents", "event-1")},
        )
        res = self._submit()
        self.assertEqual(res.code, 0, res.out + res.err)
        self.assertEqual(list(self.mock.db["reviewSubmissions"]), [sid])
        self.assertIn("also contains: 1 × appEvent", res.out)

    def test_submit_500_reads_back(self) -> None:
        self.mock.fail("PATCH", r"^/v1/reviewSubmissions/", status=500, times=1)
        res = self._submit()
        self.assertEqual(res.code, 1)
        self.assertIn("reads back as READY_FOR_REVIEW", res.err)


class SubmissionHelperTests(unittest.TestCase):
    def test_version_in_submission_ignores_removed_items(self) -> None:
        items = [
            {
                "attributes": {"state": "REMOVED"},
                "relationships": {"appStoreVersion": {"data": {"type": "appStoreVersions", "id": "v1"}}},
            },
            {
                "attributes": {"state": "READY_FOR_REVIEW"},
                "relationships": {"appEvent": {"data": {"type": "appEvents", "id": "e1"}}},
            },
        ]
        self.assertFalse(version_in_submission(items, "v1"))
        items[0]["attributes"]["state"] = "READY_FOR_REVIEW"
        self.assertTrue(version_in_submission(items, "v1"))
        self.assertFalse(version_in_submission(items, "v2"))


class WhatsNewFileTests(KitTestCase):
    def test_reads_aso_file_whats_new(self) -> None:
        path = self.write("aso.json", {"localizations": {"en-us": {"whatsNew": "Hi\r\n"}, "de-DE": {"name": "x"}}})
        self.assertEqual(load_whats_new(path), {"en-US": "Hi"})

    def test_star_entry_is_kept_and_checked(self) -> None:
        self.assertEqual(load_whats_new(self.write("s.json", {"*": " Fixes. "})), {"*": "Fixes."})
        with self.assertRaises(UsageError):
            load_whats_new(self.write("t.json", {"*": "x" * 4001}))
        res = self.run_cli("release", "--version", "1.1.0", "--build", "42", "--whats-new-text", "   ")
        self.assertEqual(res.code, 2)

    def test_rejects_unknown_locale_and_long_text(self) -> None:
        with self.assertRaises(UsageError):
            load_whats_new(self.write("a.json", {"xx-YY": "text"}))
        with self.assertRaises(UsageError):
            load_whats_new(self.write("b.json", {"en-US": "x" * 4001}))
        with self.assertRaises(UsageError):
            load_whats_new(self.write("c.json", {"en-US": "   "}))


class InstantTests(unittest.TestCase):
    def test_parse_and_compare(self) -> None:
        self.assertIsNotNone(parse_instant("2030-11-01T09:00:00Z"))
        self.assertIsNotNone(parse_instant("2030-11-01T09:00:00.000+0000"))
        self.assertIsNone(parse_instant("2030-11-01T09:00:00"))  # no time zone
        self.assertTrue(same_instant("2030-11-01T02:00:00-07:00", "2030-11-01T09:00:00Z"))
        self.assertFalse(same_instant("2030-11-01T09:00:00Z", "2030-11-02T09:00:00Z"))
        self.assertFalse(same_instant(None, "2030-11-01T09:00:00Z"))


class FirstReleaseTests(KitTestCase):
    def test_first_version_skips_whats_new_and_needs_review_details(self) -> None:
        live = self.mock.db["appStoreVersions"]["ver-live"]["attributes"]
        live.update(
            appStoreState="PREPARE_FOR_SUBMISSION", appVersionState="PREPARE_FOR_SUBMISSION", versionString="1.1.0"
        )
        self.mock.db["appStoreVersions"]["ver-live"]["rels"]["build"] = ("builds", "build-42")
        self.mock.db["appStoreReviewDetails"].clear()
        res = self.run_cli("release", "--version", "1.1.0", "--build", "42", "--apply")
        self.assertEqual(res.code, 4)
        self.assertIn("no live version to copy", res.err)


if __name__ == "__main__":
    unittest.main()
