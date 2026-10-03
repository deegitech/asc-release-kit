"""ES256 token tests: DER/raw conversion, claims, both signing back ends, caching."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from helpers import ISSUER_ID, KEY_ID, have_cryptography, openssl_path, shared_key, verify_es256

from asc_release_kit.credentials import ApiKey
from asc_release_kit.errors import CredentialError
from asc_release_kit.jwt import (
    AUDIENCE,
    CryptographySigner,
    OpenSSLSigner,
    TokenProvider,
    _run_with_input,
    b64url,
    b64url_decode,
    claims_for,
    decode_unverified,
    der_to_raw,
    encode_token,
    make_signer,
    raw_to_der,
    resolve_openssl,
)


def team_key(pem: bytes, path: str | None = None) -> ApiKey:
    return ApiKey(KEY_ID, ISSUER_ID, "team", pem, "file" if path else "env", path)


class DerConversionTests(unittest.TestCase):
    def test_round_trip_with_leading_zero_padding(self) -> None:
        raw = bytes([0x00] * 2 + [0x81] * 30) + bytes([0x7F] + [0x01] * 31)
        der = raw_to_der(raw)
        self.assertEqual(der[0], 0x30)
        self.assertEqual(der_to_raw(der), raw)

    def test_high_bit_gets_sign_byte(self) -> None:
        raw = bytes([0x80] * 32) + bytes([0x01] * 32)
        der = raw_to_der(raw)
        # SEQUENCE, len, INTEGER, len 33, 0x00 sign byte
        self.assertEqual(der[2:5], b"\x02\x21\x00")
        self.assertEqual(der_to_raw(der), raw)

    def test_short_integers_are_left_padded(self) -> None:
        der = b"\x30\x06\x02\x01\x05\x02\x01\x07"
        raw = der_to_raw(der)
        self.assertEqual(len(raw), 64)
        self.assertEqual(raw[31], 5)
        self.assertEqual(raw[63], 7)
        self.assertEqual(raw[:31], b"\x00" * 31)

    def test_rejects_malformed(self) -> None:
        for bad in (
            b"",
            b"\x31\x00",
            b"\x30\x06\x02\x01\x05\x02\x01",
            b"\x30\x03\x02\x01\x05",
            b"\x30\x06\x02\x01\x85\x02\x01\x07",
            b"\x30\x06\x02\x01\x05\x02\x01\x07\x00",
        ):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                der_to_raw(bad)

    def test_rejects_p384_sized_values(self) -> None:
        der = raw_to_der(bytes([0x01] * 48) + bytes([0x01] * 48))
        with self.assertRaises(ValueError):
            der_to_raw(der)

    def test_long_form_length(self) -> None:
        raw = bytes([0x01] * 64)
        body = b"\x02\x20" + raw[:32] + b"\x02\x20" + raw[32:]
        der = b"\x30\x81" + bytes([len(body)]) + body
        self.assertEqual(der_to_raw(der), raw)


class ClaimTests(unittest.TestCase):
    def test_team_claims(self) -> None:
        claims = claims_for(team_key(b""), 1000, 900)
        self.assertEqual(claims, {"iat": 1000, "exp": 1900, "aud": AUDIENCE, "iss": ISSUER_ID})

    def test_individual_claims_use_sub(self) -> None:
        key = ApiKey(KEY_ID, None, "individual", b"", "env")
        claims = claims_for(key, 1000, 600)
        self.assertEqual(claims["sub"], "user")
        self.assertNotIn("iss", claims)

    def test_lifetime_capped_at_twenty_minutes(self) -> None:
        with self.assertRaises(CredentialError):
            claims_for(team_key(b""), 0, 1201)
        with self.assertRaises(CredentialError):
            TokenProvider(team_key(b""), mock.Mock(), ttl=30)

    def test_b64url(self) -> None:
        self.assertEqual(b64url(b"\xfb\xff"), "-_8")
        self.assertEqual(b64url_decode("-_8"), b"\xfb\xff")


class SignerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.private_pem, self.public_pem = shared_key()

    def _check(self, signer) -> None:
        token = encode_token(team_key(self.private_pem), signer, issued_at=1_700_000_000, ttl=900)
        header, payload = decode_unverified(token)
        self.assertEqual(header, {"alg": "ES256", "kid": KEY_ID, "typ": "JWT"})
        self.assertEqual(payload["exp"] - payload["iat"], 900)
        self.assertEqual(len(b64url_decode(token.rsplit(".", 1)[1])), 64)
        self.assertTrue(verify_es256(token, self.public_pem))
        tampered = token[:-4] + ("AAAA" if not token.endswith("AAAA") else "BBBB")
        self.assertFalse(verify_es256(tampered, self.public_pem))

    @unittest.skipUnless(have_cryptography(), "cryptography not installed")
    def test_cryptography_signer(self) -> None:
        self._check(CryptographySigner(self.private_pem))

    @unittest.skipUnless(openssl_path(), "openssl not available")
    def test_openssl_signer_with_in_memory_key_uses_and_removes_temp_file(self) -> None:
        seen = []
        real_run = subprocess.run

        def spy(argv, data):
            path = argv[argv.index("-sign") + 1]
            seen.append((path, os.stat(path).st_mode & 0o777))
            return real_run(argv, input=data, capture_output=True, check=False)

        signer = OpenSSLSigner(team_key(self.private_pem), runner=spy)
        self._check(signer)
        self.assertEqual(len(seen), 1)
        path, mode = seen[0]
        self.assertEqual(mode, 0o600)
        self.assertFalse(os.path.exists(path), "temporary key copy must be deleted")

    @unittest.skipUnless(openssl_path(), "openssl not available")
    def test_openssl_failure_is_reported_without_details(self) -> None:
        signer = OpenSSLSigner(team_key(b"-----BEGIN PRIVATE KEY-----\nAAAA\n-----END PRIVATE KEY-----\n"))
        with self.assertRaises(CredentialError) as ctx:
            encode_token(team_key(b""), signer)
        self.assertIn("openssl could not sign", str(ctx.exception))

    def test_missing_openssl_binary(self) -> None:
        with self.assertRaises(CredentialError):
            OpenSSLSigner(team_key(self.private_pem), binary="definitely-not-openssl-xyz")

    def test_make_signer_preferences(self) -> None:
        key = team_key(self.private_pem)
        with mock.patch("asc_release_kit.jwt.cryptography_available", return_value=False):
            if openssl_path():
                self.assertEqual(make_signer(key, "auto").name, "openssl")
            with self.assertRaises(CredentialError):
                make_signer(key, "cryptography")
        with self.assertRaises(CredentialError):
            make_signer(key, "rsa")
        if have_cryptography():
            self.assertEqual(make_signer(key, "auto").name, "cryptography")

    @unittest.skipUnless(have_cryptography(), "cryptography not installed")
    def test_cryptography_rejects_non_p256(self) -> None:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ec

        pem = ec.generate_private_key(ec.SECP384R1()).private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        )
        with self.assertRaises(CredentialError):
            CryptographySigner(pem)


class OpenSSLProgramTests(unittest.TestCase):
    """ASC_OPENSSL decides which program receives the key file, so it is checked."""

    def setUp(self) -> None:
        self.tmp = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def fake_program(self, folder: str, mode: int = 0o755) -> str:
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, "openssl")
        with open(path, "w") as handle:
            handle.write("#!/bin/sh\nexit 1\n")
        os.chmod(path, mode)
        return path

    def test_custom_program_must_be_an_absolute_path(self) -> None:
        for value in ("tools/openssl", "./openssl", "openssl3"):
            with self.subTest(value=value), self.assertRaises(CredentialError):
                resolve_openssl(value)

    @unittest.skipUnless(os.name == "posix", "POSIX permissions")
    def test_program_inside_the_current_directory_is_refused(self) -> None:
        checkout = os.path.join(self.tmp, "checkout")
        planted = self.fake_program(os.path.join(checkout, "bin"))
        cwd = os.getcwd()
        os.chdir(checkout)
        self.addCleanup(os.chdir, cwd)
        with self.assertRaises(CredentialError) as ctx:
            resolve_openssl(planted)
        self.assertIn("inside the current directory", str(ctx.exception))

    @unittest.skipUnless(os.name == "posix", "POSIX permissions")
    def test_program_others_can_modify_is_refused(self) -> None:
        loose = self.fake_program(os.path.join(self.tmp, "loose"), mode=0o775)
        with self.assertRaises(CredentialError) as ctx:
            resolve_openssl(loose)
        self.assertIn("other users can modify", str(ctx.exception))
        tight = self.fake_program(os.path.join(self.tmp, "tight"), mode=0o755)
        self.assertEqual(resolve_openssl(tight), tight)

    @unittest.skipUnless(os.name == "posix", "POSIX PATH semantics")
    def test_default_is_never_found_through_a_relative_path_entry(self) -> None:
        self.fake_program(os.path.join(self.tmp, "bin"))
        cwd = os.getcwd()
        os.chdir(self.tmp)
        self.addCleanup(os.chdir, cwd)
        with mock.patch.dict(os.environ, {"PATH": "bin"}), self.assertRaises(CredentialError) as ctx:
            resolve_openssl()
        self.assertIn("relative PATH entry", str(ctx.exception))

    def test_signer_process_never_sees_the_private_key(self) -> None:
        script = "import os, sys; sys.stdout.write(os.environ.get('ASC_PRIVATE_KEY', 'absent'))"
        with mock.patch.dict(os.environ, {"ASC_PRIVATE_KEY": "fake-pem-text"}):
            proc = _run_with_input([sys.executable, "-c", script], b"")
        self.assertEqual(proc.stdout, b"absent")


class TokenProviderTests(unittest.TestCase):
    def test_reuses_until_near_expiry_and_reports_new_tokens(self) -> None:
        signer = mock.Mock()
        signer.name = "fake"
        signer.sign.return_value = b"\x01" * 64
        now = [1000.0]
        seen = []
        provider = TokenProvider(team_key(b""), signer, ttl=600, clock=lambda: now[0], on_new_token=seen.append)
        first = provider()
        now[0] += 500
        self.assertEqual(provider(), first)
        now[0] += 50  # within 60 s of expiry
        second = provider()
        self.assertNotEqual(first, second)
        self.assertEqual(seen, [first, second])
        self.assertEqual(provider.minted, 2)


if __name__ == "__main__":
    unittest.main()
