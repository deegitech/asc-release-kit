"""Key loading: sources, precedence, permissions, encodings, temp copies."""

from __future__ import annotations

import base64
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

from helpers import ISSUER_ID, KEY_ID, shared_key

from asc_release_kit.config import load_settings
from asc_release_kit.credentials import (
    child_env,
    default_runner,
    key_file,
    key_filename,
    load_api_key,
    normalize_pem,
    read_keychain,
    read_ssm,
)
from asc_release_kit.errors import CredentialError


def completed(stdout: bytes = b"", code: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess([], code, stdout, b"")


class KeySourceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.pem, _ = shared_key()
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.key_path = os.path.join(self.tmp, f"AuthKey_{KEY_ID}.p8")
        with open(self.key_path, "wb") as handle:
            handle.write(self.pem)
        os.chmod(self.key_path, 0o600)
        self.base_env = {"ASC_KEY_ID": KEY_ID, "ASC_ISSUER_ID": ISSUER_ID}

    def settings(self, env: dict, config: dict | None = None):
        cfg_path = None
        if config is not None:
            import json

            cfg_path = os.path.join(self.tmp, "asc-release-kit.json")
            with open(cfg_path, "w") as handle:
                json.dump(config, handle)
        return load_settings(cfg_path, env, cwd=self.tmp)

    def test_file_source(self) -> None:
        env = dict(self.base_env, ASC_PRIVATE_KEY_PATH=self.key_path)
        key = load_api_key(self.settings(env), env)
        self.assertEqual(key.source, "file")
        self.assertEqual(key.pem, self.pem)
        self.assertNotIn(self.key_path, repr(key))
        self.assertNotIn("PRIVATE", repr(key))

    @unittest.skipUnless(os.name == "posix", "POSIX permissions")
    def test_group_readable_file_refused(self) -> None:
        os.chmod(self.key_path, 0o640)
        env = dict(self.base_env, ASC_PRIVATE_KEY_PATH=self.key_path)
        with self.assertRaises(CredentialError) as ctx:
            load_api_key(self.settings(env), env)
        self.assertIn("chmod 600", str(ctx.exception))
        self.assertNotIn(self.key_path, str(ctx.exception))

    def test_missing_file(self) -> None:
        env = dict(self.base_env, ASC_PRIVATE_KEY_PATH=os.path.join(self.tmp, "nope.p8"))
        with self.assertRaises(CredentialError):
            load_api_key(self.settings(env), env)

    @unittest.skipUnless(os.name == "posix", "POSIX permissions and links")
    def test_symlinked_key_checks_and_uses_the_real_file(self) -> None:
        link = os.path.join(self.tmp, "current.p8")
        os.symlink(self.key_path, link)
        env = dict(self.base_env, ASC_PRIVATE_KEY_PATH=link)
        key = load_api_key(self.settings(env), env)
        self.assertEqual(key.pem, self.pem)
        self.assertEqual(key.path, os.path.realpath(self.key_path), "tools get the file that was checked")
        os.chmod(self.key_path, 0o644)  # the link's own mode is irrelevant; the target is what counts
        with self.assertRaises(CredentialError) as ctx:
            load_api_key(self.settings(env), env)
        self.assertIn("chmod 600", str(ctx.exception))

    @unittest.skipUnless(hasattr(os, "mkfifo"), "needs FIFOs")
    def test_fifo_is_refused_without_blocking(self) -> None:
        fifo = os.path.join(self.tmp, "AuthKey_FIFO.p8")
        os.mkfifo(fifo, 0o600)
        env = dict(self.base_env, ASC_PRIVATE_KEY_PATH=fifo)
        started = time.monotonic()
        with self.assertRaises(CredentialError) as ctx:
            load_api_key(self.settings(env), env)
        self.assertIn("not a regular file", str(ctx.exception))
        self.assertLess(time.monotonic() - started, 5)

    @unittest.skipUnless(os.name == "posix", "POSIX permissions")
    def test_key_in_a_world_writable_directory_is_refused(self) -> None:
        shared = os.path.join(self.tmp, "shared")
        os.mkdir(shared)
        moved = os.path.join(shared, os.path.basename(self.key_path))
        shutil.copy(self.key_path, moved)
        os.chmod(moved, 0o600)
        os.chmod(shared, 0o777)
        env = dict(self.base_env, ASC_PRIVATE_KEY_PATH=moved)
        with self.assertRaises(CredentialError) as ctx:
            load_api_key(self.settings(env), env)
        self.assertIn("every user can write", str(ctx.exception))
        os.chmod(shared, 0o777 | stat.S_ISVTX)  # sticky, like /tmp: others can't swap the file
        self.assertEqual(load_api_key(self.settings(env), env).pem, self.pem)

    def test_env_pem_variants(self) -> None:
        text = self.pem.decode()
        escaped = text.strip().replace("\n", "\\n")
        b64 = base64.b64encode(self.pem).decode()
        for value in (text, escaped, b64):
            env = dict(self.base_env, ASC_PRIVATE_KEY=value)
            with self.subTest(value=value[:12]):
                key = load_api_key(self.settings(env), env)
                self.assertEqual(key.pem.strip(), self.pem.strip())
                self.assertEqual(key.source, "env")

    def test_keychain_hex_output_is_decoded(self) -> None:
        calls = []

        def runner(argv):
            calls.append(argv)
            return completed(self.pem.hex().encode() + b"\n")

        value = read_keychain("asc-release-kit", "private-key", runner, platform="darwin")
        self.assertEqual(normalize_pem(value).strip(), self.pem.strip())
        self.assertEqual(
            calls[0], ["security", "find-generic-password", "-s", "asc-release-kit", "-a", "private-key", "-w"]
        )

    def test_keychain_only_on_macos_and_errors(self) -> None:
        with self.assertRaises(CredentialError):
            read_keychain("svc", None, lambda argv: completed(b""), platform="linux")
        with self.assertRaises(CredentialError):
            read_keychain("svc", None, lambda argv: completed(b"", 44), platform="darwin")

    def test_keychain_source_end_to_end(self) -> None:
        env = dict(self.base_env, ASC_KEYCHAIN_SERVICE="asc-release-kit")
        key = load_api_key(
            self.settings(env), env, runner=lambda argv: completed(base64.b64encode(self.pem)), platform="darwin"
        )
        self.assertEqual(key.source, "keychain")

    def test_ssm_source(self) -> None:
        calls = []

        def runner(argv):
            calls.append(argv)
            return completed(self.pem)

        env = dict(self.base_env, ASC_SSM_PARAMETER="/example/asc/private-key")
        key = load_api_key(self.settings(env), env, runner=runner)
        self.assertEqual(key.source, "ssm")
        self.assertIn("--with-decryption", calls[0])
        self.assertNotIn(self.pem.decode(), " ".join(calls[0]))
        with self.assertRaises(CredentialError):
            read_ssm("/x", lambda argv: completed(b"", 255))

    def test_two_sources_at_same_level_are_an_error(self) -> None:
        env = dict(self.base_env, ASC_PRIVATE_KEY_PATH=self.key_path, ASC_PRIVATE_KEY=self.pem.decode())
        with self.assertRaises(CredentialError) as ctx:
            load_api_key(self.settings(env), env)
        self.assertIn("exactly one", str(ctx.exception))

    def test_env_source_overrides_config_source(self) -> None:
        env = dict(self.base_env, ASC_PRIVATE_KEY=self.pem.decode())
        settings = self.settings(env, {"auth": {"private_key_path": os.path.join(self.tmp, "other.p8")}})
        key = load_api_key(settings, env)
        self.assertEqual(key.source, "env")

    def test_missing_ids(self) -> None:
        env = {"ASC_PRIVATE_KEY": self.pem.decode()}
        with self.assertRaises(CredentialError):
            load_api_key(self.settings(env), env)
        env = {"ASC_PRIVATE_KEY": self.pem.decode(), "ASC_KEY_ID": KEY_ID}
        with self.assertRaises(CredentialError) as ctx:
            load_api_key(self.settings(env), env)
        self.assertIn("ASC_ISSUER_ID", str(ctx.exception))

    def test_individual_key_rejects_issuer(self) -> None:
        env = dict(self.base_env, ASC_PRIVATE_KEY=self.pem.decode(), ASC_KEY_TYPE="individual")
        with self.assertRaises(CredentialError):
            load_api_key(self.settings(env), env)
        env.pop("ASC_ISSUER_ID")
        key = load_api_key(self.settings(env), env)
        self.assertIsNone(key.issuer_id)
        self.assertEqual(key_filename(key), f"ApiKey_{KEY_ID}.p8")

    def test_no_source(self) -> None:
        with self.assertRaises(CredentialError) as ctx:
            load_api_key(self.settings(self.base_env), self.base_env)
        self.assertIn("ASC_PRIVATE_KEY_PATH", str(ctx.exception))

    def test_rejects_non_pem_and_encrypted(self) -> None:
        for value in ("not a key", "-----BEGIN ENCRYPTED PRIVATE KEY-----\nAAAA\n-----END ENCRYPTED PRIVATE KEY-----"):
            env = dict(self.base_env, ASC_PRIVATE_KEY=value)
            with self.subTest(value=value[:10]), self.assertRaises(CredentialError):
                load_api_key(self.settings(env), env)

    def test_key_file_context(self) -> None:
        env = dict(self.base_env, ASC_PRIVATE_KEY=self.pem.decode())
        key = load_api_key(self.settings(env), env)
        with key_file(key) as path:
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
            self.assertEqual(os.stat(os.path.dirname(path)).st_mode & 0o777, 0o700)
            self.assertTrue(path.endswith(f"AuthKey_{KEY_ID}.p8"))
            with open(path, "rb") as handle:
                self.assertEqual(handle.read(), key.pem)
        self.assertFalse(os.path.exists(path))
        self.assertFalse(os.path.exists(os.path.dirname(path)))
        # a key that already lives in a file is used in place
        env = dict(self.base_env, ASC_PRIVATE_KEY_PATH=self.key_path)
        file_key = load_api_key(self.settings(env), env)
        with key_file(file_key) as path:
            self.assertEqual(path, os.path.realpath(self.key_path))
        self.assertTrue(os.path.exists(self.key_path))

    def test_key_file_removed_on_error(self) -> None:
        env = dict(self.base_env, ASC_PRIVATE_KEY=self.pem.decode())
        key = load_api_key(self.settings(env), env)
        seen = {}
        with self.assertRaises(RuntimeError), key_file(key) as path:
            seen["path"] = path
            raise RuntimeError("boom")
        self.assertFalse(os.path.exists(seen["path"]))


class ChildEnvironmentTests(unittest.TestCase):
    def test_child_env_drops_only_the_private_key(self) -> None:
        env = child_env({"ASC_PRIVATE_KEY": "pem", "ASC_KEY_ID": KEY_ID, "PATH": "/usr/bin"})
        self.assertEqual(env, {"ASC_KEY_ID": KEY_ID, "PATH": "/usr/bin"})

    def test_helper_programs_never_see_the_private_key(self) -> None:
        script = "import os; print(os.environ.get('ASC_PRIVATE_KEY', 'absent'))"
        with mock.patch.dict(os.environ, {"ASC_PRIVATE_KEY": "fake-pem-text"}):
            proc = default_runner([sys.executable, "-c", script])
        self.assertEqual(proc.stdout.strip(), b"absent")


if __name__ == "__main__":
    unittest.main()
