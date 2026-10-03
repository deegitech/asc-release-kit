"""Analytics Reports, Sales and Trends and customer review exports."""

from __future__ import annotations

import csv
import gzip
import io
import json
import os
import stat
import unittest

from helpers import KitTestCase

from asc_release_kit.reviews import csv_safe


class AnalyticsTests(KitTestCase):
    def test_request_plan_then_apply_then_idempotent(self) -> None:
        res = self.run_cli("analytics", "request")
        self.assertEqual(res.code, 0, res.err)
        self.assertIn("(plan) create an ONGOING analytics report request", res.out)
        self.assertNoWrites()
        res = self.run_cli("analytics", "request", "--apply")
        self.assertEqual(res.code, 0, res.err)
        self.assertIn("created and read back", res.out)
        self.assertEqual(len(self.mock.db["analyticsReportRequests"]), 1)
        res = self.run_cli("analytics", "request", "--apply")
        self.assertIn("already exists", res.out)
        self.assertEqual(len(self.mock.db["analyticsReportRequests"]), 1)

    def test_snapshot_request(self) -> None:
        self.mock.seed_analytics()
        res = self.run_cli("analytics", "request", "--access-type", "one_time_snapshot", "--apply")
        self.assertEqual(res.code, 0, res.err)
        kinds = sorted(a["attributes"]["accessType"] for a in self.mock.db["analyticsReportRequests"].values())
        self.assertEqual(kinds, ["ONE_TIME_SNAPSHOT", "ONGOING"])

    def test_list_with_reports(self) -> None:
        self.mock.seed_analytics()
        res = self.run_cli("analytics", "list", "--reports")
        self.assertEqual(res.code, 0, res.err)
        self.assertIn("App Store Discovery and Engagement Standard", res.out)
        doc = self.run_cli("analytics", "list", "--category", "app_usage", "--json").json()
        reports = doc["result"]["requests"][0]["reports"]
        self.assertEqual([r["name"] for r in reports], ["App Sessions Standard"])

    def test_download_verifies_checksum_and_never_sends_token(self) -> None:
        seeded = self.mock.seed_analytics()
        out = os.path.join(self.tmp, "analytics")
        res = self.run_cli(
            "analytics",
            "download",
            "--report",
            "app store discovery and engagement standard",
            "--since",
            "2030-02-01",
            "--out",
            out,
            "--decompress",
        )
        self.assertEqual(res.code, 0, res.out + res.err)
        base = os.path.join(out, "app-store-discovery-and-engagement-standard", "daily")
        for day in ("2030-02-27", "2030-03-01"):
            path = os.path.join(base, day, "part-001.csv.gz")
            with open(path, "rb") as handle:
                self.assertEqual(handle.read(), seeded["payload"])
            with open(os.path.join(base, day, "part-001.csv"), "rb") as handle:
                self.assertIn(b"Example Game", handle.read())
        self.assertTrue(self.mock.files.requests)
        self.assertTrue(all(r["authorization"] is None for r in self.mock.files.requests))
        # second run: files already there with the right MD5 -> no downloads
        before = len(self.mock.files.requests)
        res = self.run_cli("analytics", "download", "--report", "rep-engagement", "--since", "2030-02-01", "--out", out)
        self.assertEqual(res.code, 0, res.err)
        self.assertIn("already downloaded", res.out)
        self.assertEqual(len(self.mock.files.requests), before)

    def test_default_downloads_newest_instance_only(self) -> None:
        self.mock.seed_analytics()
        out = os.path.join(self.tmp, "a")
        res = self.run_cli("analytics", "download", "--report", "rep-engagement", "--out", out, "--json")
        files = res.json()["result"]["files"]
        self.assertEqual(len(files), 1)
        self.assertIn("2030-03-01", files[0]["path"])

    def test_checksum_mismatch_is_refused(self) -> None:
        self.mock.seed_analytics()
        for seg in self.mock.db["analyticsReportSegments"].values():
            seg["attributes"]["checksum"] = "0" * 32
        out = os.path.join(self.tmp, "bad")
        res = self.run_cli("analytics", "download", "--report", "rep-engagement", "--out", out)
        self.assertEqual(res.code, 1)
        self.assertIn("MD5 mismatch", res.err)
        self.assertFalse(any(name.endswith(".gz") for _, _, names in os.walk(out) for name in names))

    @unittest.skipUnless(os.name == "posix", "POSIX permissions")
    def test_files_are_private(self) -> None:
        self.mock.seed_analytics()
        out = os.path.join(self.tmp, "private")
        res = self.run_cli("analytics", "download", "--report", "rep-engagement", "--out", out, "--decompress")
        self.assertEqual(res.code, 0, res.err)
        written = [os.path.join(root, name) for root, _, names in os.walk(out) for name in names]
        self.assertEqual(len(written), 2)
        for path in written:
            self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600, path)
        self.assertEqual(stat.S_IMODE(os.stat(out).st_mode), 0o700)
        self.assertFalse([p for p in written if p.endswith(".part")])

    def test_processing_date_cannot_escape_the_out_directory(self) -> None:
        self.mock.seed_analytics()
        for inst in self.mock.db["analyticsReportInstances"].values():
            inst["attributes"]["processingDate"] = "../../../escaped"
        out = os.path.join(self.tmp, "box", "out")
        res = self.run_cli("analytics", "download", "--report", "rep-engagement", "--all", "--out", out)
        self.assertEqual(res.code, 0, res.err)
        self.assertIn("without a YYYY-MM-DD processing date was skipped", res.out)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "escaped")))
        self.assertEqual([n for _, _, names in os.walk(self.tmp) for n in names if n.startswith("part-")], [])
        self.assertEqual(self.mock.files.requests, [], "nothing was downloaded")

    def test_unknown_report_and_no_request(self) -> None:
        res = self.run_cli("analytics", "download", "--report", "Nope")
        self.assertEqual(res.code, 4)
        self.assertIn("No active analytics report request", res.err)
        self.mock.seed_analytics()
        res = self.run_cli("analytics", "download", "--report", "Nope")
        self.assertEqual(res.code, 4)
        self.assertIn("analytics list --reports", res.err)


class SalesTests(KitTestCase):
    TSV = b"Provider\tSKU\tUnits\nAPPLE\tEXAMPLE-SKU\t3\nAPPLE\tEXAMPLE-SKU\t4\n"

    def setUp(self) -> None:
        super().setUp()
        self.mock.sales[("DAILY", "2030-03-01")] = gzip.compress(self.TSV)
        self.mock.sales[("DAILY", "2030-03-02")] = gzip.compress(self.TSV)
        self.mock.sales[("MONTHLY", "2030-02")] = gzip.compress(self.TSV)
        os.environ["ASC_VENDOR_NUMBER"] = "87654321"

    def test_daily_download_and_decompress(self) -> None:
        out = os.path.join(self.tmp, "sales")
        res = self.run_cli("sales", "download", "--date", "2030-03-01", "--out", out, "--decompress")
        self.assertEqual(res.code, 0, res.err)
        gz = os.path.join(out, "sales-sales-summary-daily-2030-03-01.tsv.gz")
        with open(gz, "rb") as handle:
            self.assertEqual(gzip.decompress(handle.read()), self.TSV)
        with open(gz[:-3], "rb") as handle:
            self.assertEqual(handle.read(), self.TSV)
        self.assertIn("2 row(s)", res.out)
        self.assertNotIn("87654321", res.out + res.err)
        request = [r for r in self.mock.requests if r["path"] == "/v1/salesReports"][0]
        self.assertEqual(request["accept"], "application/a-gzip")
        self.assertEqual(request["query"]["filter[vendorNumber]"], "87654321")
        self.assertEqual(request["query"]["filter[reportSubType]"], "SUMMARY")
        self.assertNotIn("filter[version]", request["query"])
        if os.name == "posix":
            self.assertEqual(stat.S_IMODE(os.stat(gz).st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(os.stat(gz[:-3]).st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(os.stat(out).st_mode), 0o700)

    def test_vendor_number_never_garbles_other_numbers(self) -> None:
        doc = self.run_cli("apps", "list", "--json").json()
        self.assertEqual(sorted(a["id"] for a in doc["result"]["apps"]), ["1234567890", "1234567891"])

    def test_range_reports_missing_days(self) -> None:
        out = os.path.join(self.tmp, "range")
        res = self.run_cli("sales", "download", "--from", "2030-03-01", "--to", "2030-03-03", "--out", out, "--json")
        self.assertEqual(res.code, 0, res.err)
        result = res.json()["result"]
        self.assertEqual(result["missing"], ["2030-03-03"])
        self.assertEqual(len(result["files"]), 2)

    def test_missing_report_is_explained(self) -> None:
        res = self.run_cli("sales", "download", "--date", "2030-01-01")
        self.assertEqual(res.code, 1)
        self.assertIn("no report", res.out)
        self.assertIn("--allow-missing", res.err)
        res = self.run_cli("sales", "download", "--date", "2030-01-01", "--allow-missing")
        self.assertEqual(res.code, 0, res.err)
        self.assertIn("no report", res.out)

    def test_combinations_follow_apples_table(self) -> None:
        for args, expected in (
            (("--report-type", "SUBSCRIPTION", "--frequency", "MONTHLY", "--date", "2030-02"), "DAILY"),
            (("--report-type", "SALES", "--report-sub-type", "DETAILED"), "SUMMARY"),
            (("--report-type", "INSTALLS", "--frequency", "YEARLY", "--date", "2030"), "--report-sub-type"),
            (("--report-type", "NEWSSTAND", "--frequency", "MONTHLY", "--date", "2030-02"), "DAILY, WEEKLY"),
        ):
            with self.subTest(args=args):
                res = self.run_cli("sales", "download", *args)
                self.assertEqual(res.code, 2, res.err)
                self.assertIn(expected, res.err)
        self.assertNoWrites()
        self.assertEqual([r for r in self.mock.requests if r["path"] == "/v1/salesReports"], [])

    def test_subscriber_report_defaults(self) -> None:
        res = self.run_cli("sales", "download", "--report-type", "subscriber", "--date", "2030-03-01", "--json")
        self.assertEqual(res.code, 0, res.err)
        query = [r for r in self.mock.requests if r["path"] == "/v1/salesReports"][-1]["query"]
        self.assertEqual(query["filter[reportSubType]"], "DETAILED")
        self.assertEqual(query["filter[version]"], "1_3", "Apple retired 1_2 of the subscription reports")
        self.assertEqual(res.json()["result"]["files"][0]["path"].rsplit(os.sep, 1)[-1],
                         "sales-subscriber-detailed-daily-2030-03-01.tsv.gz")  # fmt: skip

    def test_unlisted_version_is_a_warning(self) -> None:
        res = self.run_cli("sales", "download", "--date", "2030-03-01", "--report-version", "1_1")
        self.assertEqual(res.code, 0, res.err)
        self.assertIn("Apple's table lists version 1_0 for SALES/SUMMARY", res.out)

    def test_monthly_and_version_pin(self) -> None:
        res = self.run_cli(
            "sales",
            "download",
            "--frequency",
            "monthly",
            "--date",
            "2030-02",
            "--report-version",
            "1_0",
            "--out",
            os.path.join(self.tmp, "m"),
        )
        self.assertEqual(res.code, 0, res.err)
        request = [r for r in self.mock.requests if r["path"] == "/v1/salesReports"][-1]
        self.assertEqual(request["query"]["filter[version]"], "1_0")

    def test_argument_validation(self) -> None:
        for args in (
            ("--frequency", "MONTHLY", "--date", "2030-02-01"),
            ("--frequency", "WEEKLY"),
            ("--from", "2030-03-02", "--to", "2030-03-01"),
            ("--report-type", "NOPE", "--date", "2030-03-01"),
            ("--report-version", "v1", "--date", "2030-03-01"),
        ):
            with self.subTest(args=args):
                self.assertEqual(self.run_cli("sales", "download", *args).code, 2)
        os.environ.pop("ASC_VENDOR_NUMBER")
        res = self.run_cli("sales", "download", "--date", "2030-03-01")
        self.assertEqual(res.code, 2)
        self.assertIn("--vendor", res.err)

    def test_forbidden_gets_role_hint(self) -> None:
        self.mock.fail("GET", r"^/v1/salesReports$", status=403, code="FORBIDDEN_ERROR", times=5)
        res = self.run_cli("sales", "download", "--date", "2030-03-01")
        self.assertEqual(res.code, 3)
        self.assertIn("individual keys can't access Sales and Finance", res.err)


class ReviewTests(KitTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.mock.seed_reviews(5)

    def test_csv_to_stdout_guards_formulas(self) -> None:
        res = self.run_cli("reviews", "export")
        self.assertEqual(res.code, 0, res.err)
        rows = list(csv.DictReader(io.StringIO(res.out)))
        self.assertEqual(len(rows), 5)
        self.assertEqual(rows[0]["id"], "review-4", "newest first")
        formula = [r for r in rows if r["id"] == "review-0"][0]
        self.assertTrue(formula["body"].startswith("'="))
        with_response = [r for r in rows if r["id"] == "review-1"][0]
        self.assertEqual(with_response["responseBody"], "Thanks!")
        self.assertEqual(with_response["responseState"], "PUBLISHED")

    def test_raw_csv_and_no_nicknames(self) -> None:
        res = self.run_cli("reviews", "export", "--raw-csv", "--no-nicknames")
        rows = list(csv.DictReader(io.StringIO(res.out)))
        self.assertNotIn("reviewerNickname", rows[0])
        self.assertTrue([r for r in rows if r["id"] == "review-0"][0]["body"].startswith("=HYPERLINK"))

    @unittest.skipUnless(os.name == "posix", "POSIX permissions")
    def test_existing_export_file_is_made_private(self) -> None:
        out = os.path.join(self.tmp, "reviews.csv")
        with open(out, "w") as handle:
            handle.write("old")
        os.chmod(out, 0o644)
        res = self.run_cli("reviews", "export", "--out", out)
        self.assertEqual(res.code, 0, res.err)
        self.assertEqual(stat.S_IMODE(os.stat(out).st_mode), 0o600)

    def test_jsonl_file_since_and_filters(self) -> None:
        out = os.path.join(self.tmp, "reviews.jsonl")
        res = self.run_cli("reviews", "export", "--format", "jsonl", "--out", out, "--since", "2030-02-22")
        self.assertEqual(res.code, 0, res.err)
        with open(out, encoding="utf-8") as handle:
            rows = [json.loads(line) for line in handle]
        self.assertEqual([r["id"] for r in rows], ["review-4", "review-3", "review-2"])
        if os.name == "posix":
            self.assertEqual(stat.S_IMODE(os.stat(out).st_mode), 0o600)
        res = self.run_cli("reviews", "export", "--format", "json", "--territory", "usa", "--limit", "1")
        data = json.loads(res.out)
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["territory"], "USA")

    def test_pagination_keeps_responses(self) -> None:
        self.mock.max_page = 2
        res = self.run_cli("reviews", "export", "--format", "jsonl")
        rows = [json.loads(line) for line in res.out.splitlines()]
        self.assertEqual([r["id"] for r in rows], ["review-4", "review-3", "review-2", "review-1", "review-0"])
        self.assertEqual(rows[3]["responseBody"], "Thanks!")
        pages = [r for r in self.mock.requests if r["path"].endswith("/customerReviews")]
        self.assertEqual(len(pages), 3)

    def test_csv_safe(self) -> None:
        self.assertEqual(csv_safe("=1+1"), "'=1+1")
        self.assertEqual(csv_safe("-5 stars"), "'-5 stars")
        self.assertEqual(csv_safe("ok"), "ok")
        self.assertEqual(csv_safe(None), "")


class JournalFileTests(KitTestCase):
    def _write_something(self) -> int:
        return self.run_cli("analytics", "request", "--apply").code

    @unittest.skipUnless(os.name == "posix", "POSIX permissions")
    def test_existing_journal_is_made_private(self) -> None:
        with open(self.journal_path, "w") as handle:
            handle.write("")
        os.chmod(self.journal_path, 0o644)
        self.assertEqual(self._write_something(), 0)
        self.assertEqual(stat.S_IMODE(os.stat(self.journal_path).st_mode), 0o600)
        self.assertTrue(self.journal())

    @unittest.skipUnless(os.name == "posix", "POSIX links")
    def test_journal_is_never_written_through_a_link(self) -> None:
        target = os.path.join(self.tmp, "somebody-elses-file")
        with open(target, "w") as handle:
            handle.write("untouched\n")
        os.symlink(target, self.journal_path)
        res = self.run_cli("analytics", "request", "--apply")
        self.assertEqual(res.code, 2, res.out + res.err)
        self.assertIn("symbolic link", res.err)
        self.assertNoWrites()  # refused before the first write, not after it
        with open(target) as handle:
            self.assertEqual(handle.read(), "untouched\n")

    def test_config_file_journal_path_cannot_leave_the_project_through_a_link(self) -> None:
        if os.name != "posix":
            self.skipTest("POSIX links")
        outside = os.path.join(self.tmp, "outside")
        project = os.path.join(self.tmp, "project")
        os.makedirs(outside)
        os.makedirs(project)
        os.symlink(outside, os.path.join(project, "logs"))
        with open(os.path.join(project, "asc-release-kit.json"), "w") as handle:
            json.dump({"journal": {"path": "logs/asc-journal.jsonl"}}, handle)
        os.environ.pop("ASC_JOURNAL")
        os.chdir(project)
        res = self.run_cli("analytics", "request", "--apply")
        self.assertEqual(res.code, 2, res.out + res.err)
        self.assertIn("leads outside the current directory", res.err)
        self.assertEqual(os.listdir(outside), [])
        self.assertNoWrites()


if __name__ == "__main__":
    unittest.main()
