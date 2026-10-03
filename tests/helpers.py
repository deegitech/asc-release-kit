"""Shared test helpers: throwaway keys, signature checks and a CLI harness around the mock server.

Every key is generated at test time; no key material is stored in the repository.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from unittest import mock

from mock_asc import APP_ID, MockASC

from asc_release_kit import api, cli, release, upload
from asc_release_kit.jwt import b64url_decode, raw_to_der

KEY_ID = "ABC123DEFG"
ISSUER_ID = "00000000-0000-0000-0000-000000000000"


def have_cryptography() -> bool:
    try:
        import cryptography  # noqa: F401
    except ImportError:
        return False
    return True


def openssl_path() -> str | None:
    return shutil.which("openssl")


_KEY_CACHE: tuple[bytes, bytes] | None = None


def generate_key() -> tuple[bytes, bytes]:
    """Return (PKCS#8 private PEM, SubjectPublicKeyInfo public PEM) for a new P-256 key."""
    if have_cryptography():
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ec

        key = ec.generate_private_key(ec.SECP256R1())
        private = key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        )
        public = key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        )
        return private, public
    binary = openssl_path()
    if binary is None:
        raise unittest.SkipTest("needs the cryptography package or the openssl command")
    ec_key = subprocess.run(
        [binary, "ecparam", "-name", "prime256v1", "-genkey", "-noout"], capture_output=True, check=True
    ).stdout
    private = subprocess.run(
        [binary, "pkcs8", "-topk8", "-nocrypt"], input=ec_key, capture_output=True, check=True
    ).stdout
    public = subprocess.run([binary, "pkey", "-pubout"], input=private, capture_output=True, check=True).stdout
    return private, public


def shared_key() -> tuple[bytes, bytes]:
    global _KEY_CACHE
    if _KEY_CACHE is None:
        _KEY_CACHE = generate_key()
    return _KEY_CACHE


def verify_es256(token: str, public_pem: bytes) -> bool:
    """Check a JWT's ES256 signature with the public key."""
    signing_input, signature = token.rsplit(".", 1)
    der = raw_to_der(b64url_decode(signature))
    if have_cryptography():
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec

        public = serialization.load_pem_public_key(public_pem)
        try:
            public.verify(der, signing_input.encode("ascii"), ec.ECDSA(hashes.SHA256()))  # type: ignore[union-attr]
        except InvalidSignature:
            return False
        return True
    binary = openssl_path()
    if binary is None:
        raise unittest.SkipTest("needs cryptography or openssl to verify signatures")
    with tempfile.TemporaryDirectory() as tmp:
        pub = os.path.join(tmp, "pub.pem")
        sig = os.path.join(tmp, "sig.der")
        with open(pub, "wb") as handle:
            handle.write(public_pem)
        with open(sig, "wb") as handle:
            handle.write(der)
        proc = subprocess.run(
            [binary, "dgst", "-sha256", "-verify", pub, "-signature", sig],
            input=signing_input.encode("ascii"),
            capture_output=True,
        )
        return proc.returncode == 0


@dataclass
class Result:
    code: int
    out: str
    err: str

    def json(self) -> Any:
        return json.loads(self.out)


class KitTestCase(unittest.TestCase):
    """Runs the real CLI against a fresh mock App Store Connect for every test."""

    team_key = True

    def setUp(self) -> None:
        self.private_pem, self.public_pem = shared_key()
        self.tmp = tempfile.mkdtemp(prefix="asckit-test-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.mock = MockASC(
            verifier=lambda token: verify_es256(token, self.public_pem),
            key_id=KEY_ID,
            issuer_id=ISSUER_ID if self.team_key else None,
        )
        self.addCleanup(self.mock.close)
        self.mock.seed()
        self.journal_path = os.path.join(self.tmp, "journal.jsonl")
        env = {k: v for k, v in os.environ.items() if not k.startswith("ASC_")}
        env.update(
            {
                "ASC_KEY_ID": KEY_ID,
                "ASC_PRIVATE_KEY": self.private_pem.decode("ascii"),
                "ASC_API_BASE_URL": self.mock.url,
                # The mock listens on 127.0.0.1; loopback hosts need this explicit switch.
                "ASC_ALLOW_INSECURE_LOOPBACK": "1",
                "ASC_APP_ID": APP_ID,
                "ASC_JOURNAL": self.journal_path,
            }
        )
        if self.team_key:
            env["ASC_ISSUER_ID"] = ISSUER_ID
        else:
            env["ASC_KEY_TYPE"] = "individual"
        patcher = mock.patch.dict(os.environ, env, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        cwd = os.getcwd()
        os.chdir(self.tmp)
        self.addCleanup(os.chdir, cwd)
        for target in (api, release, upload):
            sleeper = mock.patch.object(target, "_sleep", lambda seconds: None)
            sleeper.start()
            self.addCleanup(sleeper.stop)

    # -- helpers ---------------------------------------------------------------------------
    def run_cli(self, *args: str) -> Result:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = cli.main(list(args))
            except SystemExit as exc:  # argparse errors (2) and signals (128 + N)
                code = exc.code if isinstance(exc.code, int) else 1
        return Result(code, out.getvalue(), err.getvalue())

    def write(self, name: str, content: Any) -> str:
        path = os.path.join(self.tmp, name)
        with open(path, "w", encoding="utf-8") as handle:
            if isinstance(content, str):
                handle.write(content)
            else:
                json.dump(content, handle, ensure_ascii=False)
        return path

    def journal(self) -> list[dict[str, Any]]:
        if not os.path.exists(self.journal_path):
            return []
        with open(self.journal_path, encoding="utf-8") as handle:
            return [json.loads(line) for line in handle if line.strip()]

    def assertNoWrites(self) -> None:
        self.assertEqual(self.mock.write_paths(), [], "plan mode must not write")

    def version_by_string(self, version: str) -> str:
        for rid, res in self.mock.db["appStoreVersions"].items():
            if res["attributes"]["versionString"] == version:
                return rid
        raise AssertionError(f"no version {version}")


def fake_stream_runner(lines: list[str], code: int = 0) -> tuple[Callable[[list, Callable[[str], None]], int], list]:
    """A stand-in for subprocess streaming that records argv and the key file's state."""
    calls: list[dict[str, Any]] = []

    def run(argv: list, on_line: Callable[[str], None]) -> int:
        key_paths = [
            argv[i + 1] for i, a in enumerate(argv) if a in ("-authenticationKeyPath", "--p8-file-path", "--key")
        ]
        info = {"argv": list(argv), "key_files": []}
        for path in key_paths:
            mode = os.stat(path).st_mode & 0o777 if os.path.exists(path) else None
            info["key_files"].append({"path": path, "exists": os.path.exists(path), "mode": mode})
        calls.append(info)
        for line in lines:
            on_line(line)
        return code

    return run, calls
