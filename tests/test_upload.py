"""upload-build: command construction, key-file lifecycle, output masking, plan mode."""

from __future__ import annotations

import os
import plistlib
import subprocess
import sys
import unittest
import zipfile
from unittest import mock

from helpers import ISSUER_ID, KEY_ID, KitTestCase, fake_stream_runner

from asc_release_kit import upload


class UploadTestCase(KitTestCase):
    def setUp(self) -> None:
        super().setUp()
        patcher = mock.patch.object(upload.shutil, "which", lambda name: f"/usr/bin/{name}")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.archive = os.path.join(self.tmp, "Example.xcarchive")
        os.makedirs(self.archive)
        with open(os.path.join(self.archive, "Info.plist"), "wb") as handle:
            plistlib.dump(
                {
                    "ApplicationProperties": {
                        "CFBundleIdentifier": "com.example.mygame",
                        "CFBundleShortVersionString": "1.1.0",
                        "CFBundleVersion": "42",
                    }
                },
                handle,
            )
        self.ipa = os.path.join(self.tmp, "Example.ipa")
        with zipfile.ZipFile(self.ipa, "w") as archive:
            archive.writestr(
                "Payload/Example.app/Info.plist",
                plistlib.dumps(
                    {
                        "CFBundleIdentifier": "com.example.mygame",
                        "CFBundleShortVersionString": "1.1.0",
                        "CFBundleVersion": "42",
                    },
                    fmt=plistlib.FMT_BINARY,
                ),
            )
            archive.writestr("Payload/Example.app/Example", b"\x00binary")

    def use_runner(self, lines: list[str] | None = None, code: int = 0) -> list:
        runner, calls = fake_stream_runner(lines or [], code)
        patcher = mock.patch.object(upload, "stream_process", runner)
        patcher.start()
        self.addCleanup(patcher.stop)
        return calls


class ArchiveTests(UploadTestCase):
    def test_plan_shows_masked_command_and_runs_nothing(self) -> None:
        calls = self.use_runner()
        res = self.run_cli("upload-build", "--archive", self.archive)
        self.assertEqual(res.code, 0, res.err)
        self.assertEqual(calls, [])
        self.assertIn("xcodebuild -exportArchive", res.out)
        self.assertIn("-authenticationKeyID '<key-id>'", res.out)
        self.assertIn("<issuer-id>", res.out)
        self.assertIn("<key-file>", res.out)
        self.assertNotIn(KEY_ID, res.out)
        self.assertNotIn(ISSUER_ID, res.out)
        self.assertIn("com.example.mygame 1.1.0 (42)", res.out)

    def test_plan_never_loads_the_key(self) -> None:
        """Plan mode must not read a key file, prompt for the Keychain or call AWS."""
        os.environ.pop("ASC_PRIVATE_KEY")
        os.environ["ASC_PRIVATE_KEY_PATH"] = os.path.join(self.tmp, "missing", "AuthKey_ABC123DEFG.p8")
        calls = self.use_runner()
        with mock.patch("asc_release_kit.context.load_api_key", side_effect=AssertionError("key loaded")):
            res = self.run_cli("upload-build", "--archive", self.archive)
            self.assertEqual(res.code, 0, res.err)
            res = self.run_cli("upload-build", "--ipa", self.ipa)
            self.assertEqual(res.code, 0, res.err)
        self.assertEqual(calls, [])
        self.assertIn("isn't loaded in plan mode", res.out)
        self.assertNotIn(KEY_ID, res.out)
        res = self.run_cli("upload-build", "--archive", self.archive, "--apply")
        self.assertEqual(res.code, 2, "with --apply the missing key file is reported")
        self.assertIn("does not exist", res.err)

    def test_apply_runs_with_private_temp_key_and_cleans_up(self) -> None:
        seen = {}

        def runner(argv, on_line):
            path = argv[argv.index("-authenticationKeyPath") + 1]
            seen["key"] = (path, os.stat(path).st_mode & 0o777)
            with open(argv[argv.index("-exportOptionsPlist") + 1], "rb") as handle:
                seen["options"] = plistlib.load(handle)
            seen["argv"] = argv
            on_line(f"Authenticating with key {KEY_ID} issuer {ISSUER_ID}")
            on_line("** EXPORT SUCCEEDED **")
            return 0

        with mock.patch.object(upload, "stream_process", runner):
            res = self.run_cli("upload-build", "--archive", self.archive, "--team-id", "ABCDE12345", "--apply")
        self.assertEqual(res.code, 0, res.out + res.err)
        path, mode = seen["key"]
        self.assertEqual(mode, 0o600)
        self.assertFalse(os.path.exists(path), "temporary key copy must be gone")
        self.assertEqual(seen["options"]["method"], "app-store-connect")
        self.assertEqual(seen["options"]["destination"], "upload")
        self.assertIs(seen["options"]["manageAppVersionAndBuildNumber"], False)
        self.assertEqual(seen["options"]["teamID"], "ABCDE12345")
        self.assertIn("-allowProvisioningUpdates", seen["argv"])
        self.assertNotIn(KEY_ID, res.out)
        self.assertNotIn(ISSUER_ID, res.out)
        self.assertIn("EXPORT SUCCEEDED", res.out)
        self.assertEqual([e["event"] for e in self.journal()], ["upload"])

    def test_failure_shows_tail_and_exit_code(self) -> None:
        self.use_runner(["error: something about signing", "last line"], code=70)
        res = self.run_cli("upload-build", "--archive", self.archive, "--apply")
        self.assertEqual(res.code, 1)
        self.assertIn("| last line", res.out)
        self.assertIn("failed (exit 70)", res.err)

    def test_given_export_options_are_checked(self) -> None:
        plist = os.path.join(self.tmp, "ExportOptions.plist")
        with open(plist, "wb") as handle:
            plistlib.dump({"method": "app-store-connect", "destination": "export"}, handle)
        self.use_runner()
        res = self.run_cli("upload-build", "--archive", self.archive, "--export-options", plist)
        self.assertEqual(res.code, 0, res.err)
        self.assertIn("destination isn't 'upload'", res.out)
        self.assertIn("manageAppVersionAndBuildNumber is on", res.out)

    def test_wait_processing_polls_builds(self) -> None:
        self.use_runner(["Upload succeeded"])
        res = self.run_cli("upload-build", "--archive", self.archive, "--wait-processing", "10", "--apply")
        self.assertEqual(res.code, 0, res.out + res.err)
        self.assertIn("processed: VALID", res.out)

    def test_bundle_id_mismatch_is_refused(self) -> None:
        calls = self.use_runner()
        os.environ["ASC_BUNDLE_ID"] = "com.example.othergame"
        res = self.run_cli("upload-build", "--archive", self.archive, "--apply")
        self.assertEqual(res.code, 2)
        self.assertIn("Refusing to upload the wrong app", res.err)
        self.assertEqual(calls, [])
        os.environ["ASC_BUNDLE_ID"] = "com.example.mygame"
        self.assertEqual(self.run_cli("upload-build", "--archive", self.archive, "--apply").code, 0)

    def test_not_an_archive(self) -> None:
        empty = os.path.join(self.tmp, "Empty.xcarchive")
        os.makedirs(empty)
        self.assertEqual(self.run_cli("upload-build", "--archive", empty).code, 2)
        self.assertEqual(self.run_cli("upload-build", "--archive", os.path.join(self.tmp, "missing")).code, 2)


class IpaAndNotaryTests(UploadTestCase):
    def test_ipa_uses_altool_with_p8_path(self) -> None:
        calls = self.use_runner(["No errors uploading 'Example.ipa'"])
        res = self.run_cli("upload-build", "--ipa", self.ipa, "--apply")
        self.assertEqual(res.code, 0, res.out + res.err)
        argv = calls[0]["argv"]
        self.assertEqual(argv[:4], ["xcrun", "altool", "--upload-package", self.ipa])
        for flag, value in (
            ("--apple-id", "1234567890"),
            ("--bundle-id", "com.example.mygame"),
            ("--bundle-short-version-string", "1.1.0"),
            ("--bundle-version", "42"),
            ("--api-key", KEY_ID),
            ("--api-issuer", ISSUER_ID),
            ("-t", "ios"),
        ):
            self.assertEqual(argv[argv.index(flag) + 1], value)
        key_info = calls[0]["key_files"][0]
        self.assertTrue(key_info["exists"])
        self.assertEqual(key_info["mode"], 0o600)
        self.assertFalse(os.path.exists(key_info["path"]))

    def test_ipa_needs_app_id(self) -> None:
        os.environ.pop("ASC_APP_ID")
        res = self.run_cli("upload-build", "--ipa", self.ipa)
        self.assertEqual(res.code, 2)
        self.assertIn("--app", res.err)

    def test_notarize_reads_status(self) -> None:
        dmg = os.path.join(self.tmp, "Example.dmg")
        with open(dmg, "wb") as handle:
            handle.write(b"dmg")
        calls = self.use_runner(['{"id": "abc", "status": "Accepted", "message": "Processing complete"}'])
        res = self.run_cli("upload-build", "--notarize", dmg, "--apply")
        self.assertEqual(res.code, 0, res.out + res.err)
        argv = calls[0]["argv"]
        self.assertEqual(argv[:4], ["xcrun", "notarytool", "submit", dmg])
        self.assertIn("--wait", argv)
        self.assertEqual(argv[argv.index("--issuer") + 1], ISSUER_ID)
        self.assertIn("notarization status: Accepted", res.out)

    def test_notarize_invalid_fails(self) -> None:
        dmg = os.path.join(self.tmp, "Example.dmg")
        with open(dmg, "wb") as handle:
            handle.write(b"dmg")
        self.use_runner(['{"id": "abc", "status": "Invalid"}'])
        res = self.run_cli("upload-build", "--notarize", dmg, "--apply")
        self.assertEqual(res.code, 1)
        self.assertIn("notarytool log", res.err)

    def test_exactly_one_source(self) -> None:
        res = self.run_cli("upload-build", "--archive", self.archive, "--ipa", self.ipa)
        self.assertEqual(res.code, 2)
        self.assertIn("not allowed with", res.err)


class IndividualKeyUploadTests(UploadTestCase):
    team_key = False

    def test_individual_key_with_altool_and_xcodebuild(self) -> None:
        calls = self.use_runner()
        res = self.run_cli("upload-build", "--ipa", self.ipa, "--apply")
        self.assertEqual(res.code, 0, res.err)
        argv = calls[0]["argv"]
        self.assertEqual(argv[argv.index("--api-key-subject") + 1], "user")
        self.assertNotIn("--api-issuer", argv)
        res = self.run_cli("upload-build", "--archive", self.archive)
        self.assertEqual(res.code, 2)
        self.assertIn("team API key", res.err)


class StreamProcessTests(unittest.TestCase):
    """The real subprocess runner: environment, decoding and cleanup."""

    def test_tool_never_sees_the_private_key_and_bad_bytes_are_replaced(self) -> None:
        script = (
            "import os, sys\n"
            "print(os.environ.get('ASC_PRIVATE_KEY', 'absent'), flush=True)\n"
            "sys.stdout.buffer.write(b'bad \\xff byte\\n')\n"
        )
        lines: list[str] = []
        with mock.patch.dict(os.environ, {"ASC_PRIVATE_KEY": "fake-pem-text"}):
            code = upload.stream_process([sys.executable, "-c", script], lines.append)
        self.assertEqual(code, 0)
        self.assertEqual(lines[0], "absent")
        self.assertEqual(lines[1], "bad \ufffd byte")

    def test_tool_is_stopped_when_reading_fails(self) -> None:
        started: list[subprocess.Popen] = []
        real_popen = subprocess.Popen

        def spy(*args, **kwargs):
            proc = real_popen(*args, **kwargs)
            started.append(proc)
            return proc

        def explode(_line: str) -> None:
            raise RuntimeError("boom")

        script = "import time\nprint('working', flush=True)\ntime.sleep(60)\n"
        with mock.patch.object(upload.subprocess, "Popen", spy), self.assertRaises(RuntimeError):
            upload.stream_process([sys.executable, "-c", script], explode)
        self.assertIsNotNone(started[0].poll(), "the tool must not keep running after the kit gave up")
