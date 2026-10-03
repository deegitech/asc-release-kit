"""ES256 JSON Web Tokens for the App Store Connect API.

Follows Apple's "Generating Tokens for API Requests":

* header ``{"alg": "ES256", "kid": <key ID>, "typ": "JWT"}``;
* payload ``iss`` (team keys) or ``sub: "user"`` (individual keys), ``iat``,
  ``exp`` and ``aud: "appstoreconnect-v1"``;
* lifetime at most 20 minutes (this kit defaults to 15).

Signing uses the ``cryptography`` package when it is installed (the ``crypto``
extra) and otherwise the ``openssl`` command-line tool. ``openssl dgst -sign``
returns an ASN.1 DER signature; JWS needs the raw 64-byte ``r || s`` form, so the
conversion is done here in pure Python.
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import threading
import time
from collections.abc import Callable, Sequence
from typing import Any, Protocol

from .credentials import ApiKey, child_env, key_file
from .errors import CredentialError

#: How to get a signer when neither ``cryptography`` nor a usable ``openssl`` is there.
INSTALL_CRYPTO = 'pipx inject asc-release-kit cryptography (or: python3 -m pip install "asc-release-kit[crypto]")'
AS_DOWNLOADED = "use the AuthKey_<KEY_ID>.p8 file exactly as App Store Connect gave it to you"

AUDIENCE = "appstoreconnect-v1"
MAX_TTL_SECONDS = 20 * 60
DEFAULT_TTL_SECONDS = 15 * 60
COORDINATE_BYTES = 32  # P-256
REFRESH_MARGIN_SECONDS = 60


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64url_decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _der_length(length: int) -> bytes:
    if length < 0x80:
        return bytes([length])
    if length < 0x100:
        return b"\x81" + bytes([length])
    return b"\x82" + length.to_bytes(2, "big")


def _read_der_length(buf: bytes, index: int) -> tuple[int, int]:
    first = buf[index]
    index += 1
    if first < 0x80:
        return first, index
    count = first & 0x7F
    if count == 0 or count > 2:
        raise ValueError("unsupported DER length encoding")
    if index + count > len(buf):
        raise ValueError("truncated DER length")
    return int.from_bytes(buf[index : index + count], "big"), index + count


def der_to_raw(der: bytes, size: int = COORDINATE_BYTES) -> bytes:
    """Convert an ECDSA signature from DER ``SEQUENCE {r INTEGER, s INTEGER}`` to raw ``r || s``."""
    try:
        if not der or der[0] != 0x30:
            raise ValueError("not a DER SEQUENCE")
        length, index = _read_der_length(der, 1)
        if index + length != len(der):
            raise ValueError("DER SEQUENCE length does not match the data")
        parts = []
        for _ in range(2):
            if der[index] != 0x02:
                raise ValueError("expected a DER INTEGER")
            length, index = _read_der_length(der, index + 1)
            value = der[index : index + length]
            index += length
            if length == 0 or len(value) != length:
                raise ValueError("truncated DER INTEGER")
            if value[0] & 0x80:
                raise ValueError("negative DER INTEGER")
            value = value.lstrip(b"\x00")
            if len(value) > size:
                raise ValueError("signature value too large for P-256 (is this an ES256 key?)")
            parts.append(value.rjust(size, b"\x00"))
        if index != len(der):
            raise ValueError("trailing bytes after the DER signature")
    except IndexError:
        raise ValueError("truncated DER signature") from None
    return parts[0] + parts[1]


def raw_to_der(raw: bytes) -> bytes:
    """Convert a raw ``r || s`` signature back to DER (used to verify with openssl)."""
    if not raw or len(raw) % 2:
        raise ValueError("raw signature must have an even, non-zero length")
    half = len(raw) // 2

    def integer(value: bytes) -> bytes:
        value = value.lstrip(b"\x00") or b"\x00"
        if value[0] & 0x80:
            value = b"\x00" + value
        return b"\x02" + _der_length(len(value)) + value

    body = integer(raw[:half]) + integer(raw[half:])
    return b"\x30" + _der_length(len(body)) + body


class Signer(Protocol):
    name: str

    def sign(self, message: bytes) -> bytes:
        """Return the raw 64-byte ES256 signature of ``message``."""


class CryptographySigner:
    """ES256 with the ``cryptography`` package (no temporary files, no subprocesses)."""

    name = "cryptography"

    def __init__(self, pem: bytes) -> None:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ec

        try:
            key = serialization.load_pem_private_key(pem, password=None)
        except Exception:  # noqa: BLE001 - never echo key parsing details
            raise CredentialError(
                "The private key could not be parsed (expected an unencrypted .p8 key).", fix=AS_DOWNLOADED
            ) from None
        if not isinstance(key, ec.EllipticCurvePrivateKey) or key.curve.name != "secp256r1":
            raise CredentialError("App Store Connect keys are EC P-256 keys; this key is not.", fix=AS_DOWNLOADED)
        self._key = key

    def sign(self, message: bytes) -> bytes:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

        r, s = decode_dss_signature(self._key.sign(message, ec.ECDSA(hashes.SHA256())))
        return r.to_bytes(COORDINATE_BYTES, "big") + s.to_bytes(COORDINATE_BYTES, "big")


ProcessRunner = Callable[[Sequence[str], bytes], "subprocess.CompletedProcess[bytes]"]


def _run_with_input(argv: Sequence[str], data: bytes) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(list(argv), input=data, capture_output=True, check=False, timeout=60, env=child_env())


DEFAULT_OPENSSL = "openssl"


def resolve_openssl(binary: str = DEFAULT_OPENSSL) -> str:
    """Find the openssl program that will see the key file, refusing ones a checkout could plant.

    * the default, ``openssl``, is looked up on PATH and must resolve to an
      absolute path (a relative PATH entry such as ``.`` could pick a program
      from the current directory);
    * ``ASC_OPENSSL`` must be an absolute path to an executable outside the
      current directory that neither the group nor others can modify.
    """
    custom = binary != DEFAULT_OPENSSL
    if custom and not os.path.isabs(binary):
        raise CredentialError(
            "ASC_OPENSSL must be the absolute path of the openssl program, e.g. /usr/bin/openssl.",
            fix='export ASC_OPENSSL="$(command -v openssl)", or unset ASC_OPENSSL to use openssl from PATH',
        )
    resolved = shutil.which(binary)
    if resolved is None:
        raise CredentialError(
            "No signer available: install the optional extra (pip install 'asc-release-kit[crypto]') "
            "or make the `openssl` command available (ASC_OPENSSL).",
            fix=INSTALL_CRYPTO,
        )
    if not os.path.isabs(resolved):
        raise CredentialError(
            "`openssl` was found through a relative PATH entry (such as `.`); refusing to run a program from the "
            "current directory with your key. Fix PATH or set ASC_OPENSSL to an absolute path.",
            fix="remove relative entries such as . from PATH, or export ASC_OPENSSL=/usr/bin/openssl; "
            + INSTALL_CRYPTO,
        )
    if custom:
        real = os.path.realpath(resolved)
        cwd = os.path.realpath(os.getcwd())
        at_root = os.path.dirname(cwd) == cwd  # e.g. a container whose working directory is /
        if not at_root and (real == cwd or real.startswith(cwd + os.sep)):
            raise CredentialError(
                "ASC_OPENSSL points inside the current directory; refusing to hand the key to a program that "
                "came with a checkout."
            )
        if os.name == "posix" and os.stat(real).st_mode & 0o022:
            raise CredentialError("ASC_OPENSSL points to a program that other users can modify; refusing to use it.")
        return real
    return resolved


class OpenSSLSigner:
    """ES256 through ``openssl dgst -sha256 -sign`` with DER-to-raw conversion.

    Works with OpenSSL and LibreSSL (the ``openssl`` that ships with macOS). Keys
    that live in memory are written to a 0600 temp file for each signature and
    removed straight after.
    """

    name = "openssl"

    def __init__(self, api_key: ApiKey, binary: str = DEFAULT_OPENSSL, runner: ProcessRunner | None = None) -> None:
        self._binary = resolve_openssl(binary)
        self._api_key = api_key
        self._run = runner or _run_with_input

    def sign(self, message: bytes) -> bytes:
        with key_file(self._api_key) as path:
            try:
                proc = self._run([self._binary, "dgst", "-sha256", "-sign", path], message)
            except (OSError, subprocess.SubprocessError) as exc:
                raise CredentialError(f"openssl could not be run: {exc.__class__.__name__}.") from None
        if proc.returncode != 0:
            raise CredentialError(
                f"openssl could not sign the token (exit {proc.returncode}). Is the key a valid, unencrypted .p8 file?",
                fix=AS_DOWNLOADED,
            )
        try:
            return der_to_raw(proc.stdout)
        except ValueError as exc:
            raise CredentialError(f"openssl returned an unexpected signature: {exc}.") from None


def cryptography_available() -> bool:
    try:
        import cryptography  # noqa: F401
    except ImportError:
        return False
    return True


def make_signer(api_key: ApiKey, preference: str = "auto", openssl: str = DEFAULT_OPENSSL) -> Signer:
    """Pick a signer: ``cryptography`` when available (or required), else ``openssl``."""
    if preference not in ("auto", "cryptography", "openssl"):
        raise CredentialError("Signer must be auto, cryptography or openssl.")
    if preference in ("auto", "cryptography"):
        if cryptography_available():
            return CryptographySigner(api_key.pem)
        if preference == "cryptography":
            raise CredentialError(
                "ASC_SIGNER=cryptography but the package isn't installed: pip install 'asc-release-kit[crypto]'.",
                fix=INSTALL_CRYPTO,
            )
    return OpenSSLSigner(api_key, openssl)


def claims_for(api_key: ApiKey, issued_at: int, ttl: int) -> dict[str, Any]:
    if not 0 < ttl <= MAX_TTL_SECONDS:
        raise CredentialError("Token lifetime must be between 1 second and 20 minutes.")
    payload: dict[str, Any] = {"iat": issued_at, "exp": issued_at + ttl, "aud": AUDIENCE}
    if api_key.key_type == "individual":
        payload["sub"] = "user"
    else:
        if not api_key.issuer_id:
            raise CredentialError("Team keys need an issuer ID.")
        payload["iss"] = api_key.issuer_id
    return payload


def encode_token(api_key: ApiKey, signer: Signer, issued_at: int | None = None, ttl: int = DEFAULT_TTL_SECONDS) -> str:
    """Build and sign a token. The result is a secret: never print or store it."""
    now = int(time.time()) if issued_at is None else int(issued_at)
    header = {"alg": "ES256", "kid": api_key.key_id, "typ": "JWT"}
    segments = [
        b64url(json.dumps(header, separators=(",", ":")).encode("utf-8")),
        b64url(json.dumps(claims_for(api_key, now, ttl), separators=(",", ":")).encode("utf-8")),
    ]
    signing_input = ".".join(segments).encode("ascii")
    signature = signer.sign(signing_input)
    if len(signature) != 2 * COORDINATE_BYTES:
        raise CredentialError("The signer returned a signature of the wrong size.")
    return ".".join(segments + [b64url(signature)])


def decode_unverified(token: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return a token's header and payload without verifying it (diagnostics and tests)."""
    try:
        header_b64, payload_b64, _ = token.split(".")
        return json.loads(b64url_decode(header_b64)), json.loads(b64url_decode(payload_b64))
    except (ValueError, json.JSONDecodeError):
        raise ValueError("not a JWT") from None


class TokenProvider:
    """Mints short-lived tokens on demand and reuses each until shortly before expiry.

    Tokens live only in memory. ``on_new_token`` lets the caller register each new
    token with the redactor.
    """

    def __init__(
        self,
        api_key: ApiKey,
        signer: Signer,
        ttl: int = DEFAULT_TTL_SECONDS,
        clock: Callable[[], float] = time.time,
        on_new_token: Callable[[str], None] | None = None,
    ) -> None:
        if not 60 <= ttl <= MAX_TTL_SECONDS:
            raise CredentialError("Token lifetime must be between 60 seconds and 20 minutes.")
        self._api_key = api_key
        self._signer = signer
        self._ttl = ttl
        self._clock = clock
        self._on_new_token = on_new_token
        self._lock = threading.Lock()
        self._token: str | None = None
        self._expires = 0
        self.minted = 0

    @property
    def signer_name(self) -> str:
        return self._signer.name

    def __call__(self) -> str:
        with self._lock:
            now = self._clock()
            if self._token is None or now >= self._expires - REFRESH_MARGIN_SECONDS:
                issued = int(now)
                self._token = encode_token(self._api_key, self._signer, issued, self._ttl)
                self._expires = issued + self._ttl
                self.minted += 1
                if self._on_new_token is not None:
                    self._on_new_token(self._token)
            return self._token
