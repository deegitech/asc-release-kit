"""Load the App Store Connect API private key from exactly one safe source.

Supported sources, in the order they are described in the README:

* ``ASC_PRIVATE_KEY_PATH`` / ``auth.private_key_path``: a ``.p8`` file that only
  its owner can read (``chmod 600``); anything looser is refused;
* ``ASC_PRIVATE_KEY``: the PEM text itself (or base64 of it), for CI secrets;
* ``ASC_KEYCHAIN_SERVICE`` (+ optional ``ASC_KEYCHAIN_ACCOUNT``): a macOS
  Keychain generic password read with ``security find-generic-password -w``;
* ``ASC_SSM_PARAMETER``: an AWS Systems Manager SecureString read with the
  ``aws`` CLI.

Sources set by flags or environment variables win over the config file. Two
sources at the same level are an error, so a stray variable can't silently swap
the key. The key never goes into argv, logs or state files; tools that need a
key *file* (openssl, xcodebuild, altool, notarytool) get a short-lived copy in a
private temporary directory that is removed right after use. Helper programs run
with ``ASC_PRIVATE_KEY`` removed from their environment (see ``child_env``).
"""

from __future__ import annotations

import base64
import binascii
import contextlib
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field

from .config import Settings
from .errors import CredentialError
from .hints import KEYS_PAGE, aws_exit_hint, keychain_exit_hint

Runner = Callable[[Sequence[str]], "subprocess.CompletedProcess[bytes]"]

SOURCE_LABELS = {
    "file": "key file (path not shown)",
    "env": "ASC_PRIVATE_KEY environment variable",
    "keychain": "macOS Keychain item",
    "ssm": "AWS SSM SecureString parameter",
}

#: A .p8 key is about 250 bytes; anything far larger isn't one.
MAX_KEY_BYTES = 64 * 1024

NO_KEY_HELP = (
    "No App Store Connect API key configured. Set ONE of: ASC_PRIVATE_KEY_PATH (a chmod 600 .p8 "
    "file), ASC_PRIVATE_KEY (PEM text, e.g. a CI secret), ASC_KEYCHAIN_SERVICE (macOS Keychain) or "
    "ASC_SSM_PARAMETER (AWS SSM). Also set ASC_KEY_ID and, for team keys, ASC_ISSUER_ID."
)
NO_KEY_FIX = (
    "create a key under " + KEYS_PAGE + " > Team Keys, then e.g. "
    "export ASC_PRIVATE_KEY_PATH=~/.config/asc-release-kit/AuthKey_ABC123DEFG.p8 (docs/setup.md, steps 2-4)"
)

#: macOS's interactive password prompt (`security add-generic-password ... -w` with no value)
#: keeps only this many characters (observed October 2026); a base64 .p8 key is a little over 300.
KEYCHAIN_PROMPT_LIMIT = 128
KEYCHAIN_STORE_COMMAND = (
    'security add-generic-password -U -s asc-release-kit -a ABC123DEFG -w "$(base64 < AuthKey_ABC123DEFG.p8)"'
)
#: How to store a complete key again, by key source. The PEM's first and last lines are named
#: in words: the redactor masks any text between two PEM markers, fixes included.
WHOLE_KEY_FIXES = {
    "env": (
        "store the whole .p8 file, every line from the BEGIN PRIVATE KEY line through the END PRIVATE KEY line "
        "(for a GitHub secret: gh secret set ASC_PRIVATE_KEY < AuthKey_ABC123DEFG.p8)"
    ),
    "keychain": (
        "store the whole .p8 file again, with the value on the command line (your service and account names): "
        + KEYCHAIN_STORE_COMMAND
    ),
    "ssm": (
        "store the whole .p8 file again: aws ssm put-parameter --overwrite --type SecureString "
        "--name /example/asc/private-key --value file://AuthKey_ABC123DEFG.p8"
    ),
    "file": (
        "use the AuthKey_<KEY_ID>.p8 file exactly as App Store Connect gave it: every line from the BEGIN PRIVATE "
        "KEY line through the END PRIVATE KEY line"
    ),
}
WHOLE_FILE_FIX = WHOLE_KEY_FIXES["env"]


def child_env(environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """The environment for helper programs: ours minus ``ASC_PRIVATE_KEY``.

    When the key comes from that variable (typical in CI), passing the environment on
    would hand the PEM to openssl, security, aws and Xcode's tools, and to anything
    they start. They get the key as a file path instead, when they need it at all.
    """
    source = os.environ if environ is None else environ
    return {name: value for name, value in source.items() if name != "ASC_PRIVATE_KEY"}


def default_runner(argv: Sequence[str]) -> subprocess.CompletedProcess[bytes]:
    """Run a helper program without a shell, capturing its output."""
    return subprocess.run(list(argv), capture_output=True, check=False, timeout=120, env=child_env())


@dataclass(frozen=True)
class ApiKey:
    """An App Store Connect API key in memory. ``repr`` never shows secrets."""

    key_id: str
    issuer_id: str | None
    key_type: str
    pem: bytes = field(repr=False)
    source: str = "file"
    path: str | None = field(default=None, repr=False)

    def describe(self) -> str:
        return SOURCE_LABELS.get(self.source, self.source)

    def identifiers(self) -> list[str]:
        """Values to register with the redactor."""
        return [value for value in (self.key_id, self.issuer_id, self.path) if value]


def _check_key_file(info: os.stat_result, real_path: str) -> None:
    """Refuse key files that other local users could read or replace."""
    if not stat.S_ISREG(info.st_mode):
        raise CredentialError(
            "The configured private key path is not a regular file.",
            fix="point ASC_PRIVATE_KEY_PATH at the AuthKey_<KEY_ID>.p8 file itself",
        )
    if info.st_size > MAX_KEY_BYTES:
        raise CredentialError(
            "The configured private key file is far too large to be a .p8 key.",
            fix="point ASC_PRIVATE_KEY_PATH at the AuthKey_<KEY_ID>.p8 file App Store Connect gave you",
        )
    if os.name != "posix":
        return
    if info.st_mode & 0o077:
        raise CredentialError(
            "The private key file is readable by other users. Run `chmod 600` on it "
            "(and keep it outside any git repository).",
            fix='chmod 600 "$ASC_PRIVATE_KEY_PATH" (or the file named in auth.private_key_path)',
        )
    getuid = getattr(os, "getuid", None)
    if getuid is not None and info.st_uid != getuid():
        raise CredentialError(
            "The private key file belongs to another user.",
            fix="copy it into a directory of your own (~/.config/asc-release-kit, chmod 700) and chmod 600 the copy",
        )
    parent = os.stat(os.path.dirname(real_path))
    if parent.st_mode & 0o002 and not parent.st_mode & stat.S_ISVTX:
        raise CredentialError(
            "The private key file is in a directory that every user can write to, so anyone could swap it. "
            "Move it to a private directory (chmod 700).",
            fix="mkdir -p ~/.config/asc-release-kit && chmod 700 ~/.config/asc-release-kit, then move the key there",
        )


def read_key_file(path: str) -> tuple[bytes, str]:
    """Read a key file that only its owner can read; return ``(content, resolved path)``.

    Symbolic links are resolved first, then the resolved file is opened once
    (``O_NOFOLLOW``, ``O_NONBLOCK`` so a FIFO can't stall the kit) and the checks
    run on that open descriptor: the file that is checked is the file that is read.
    """
    real = os.path.realpath(path)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_BINARY", 0)
    try:
        fd = os.open(real, flags)
    except FileNotFoundError:
        raise CredentialError(
            "The configured private key file does not exist (ASC_PRIVATE_KEY_PATH / auth.private_key_path).",
            fix="check the path; Apple's download is named AuthKey_<KEY_ID>.p8 (often in ~/Downloads)",
        ) from None
    except OSError as exc:
        raise CredentialError(
            f"The private key file can't be read: {exc.strerror}.",
            fix="check that the file is yours and readable by you: ls -l on it should show -rw-------",
        ) from None
    try:
        _check_key_file(os.fstat(fd), real)
        chunks: list[bytes] = []
        size = 0
        while True:
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_KEY_BYTES:
                raise CredentialError(
                    "The configured private key file is far too large to be a .p8 key.",
                    fix="point ASC_PRIVATE_KEY_PATH at the AuthKey_<KEY_ID>.p8 file App Store Connect gave you",
                )
            chunks.append(chunk)
    finally:
        os.close(fd)
    return b"".join(chunks), real


def normalize_pem(value: str | bytes) -> bytes:
    """Accept PEM text, PEM with literal ``\\n`` escapes, base64 of PEM, or hex of PEM."""
    text = value.decode("utf-8", "replace") if isinstance(value, bytes) else str(value)
    text = text.strip()
    if text.startswith("-----BEGIN"):
        if "\n" not in text and "\\n" in text:
            text = text.replace("\\n", "\n")
        return (text.strip() + "\n").encode("utf-8")
    compact = "".join(text.split())
    # `security find-generic-password -w` prints hex when the secret has newlines.
    if compact and re.fullmatch(r"(?:[0-9a-fA-F]{2})+", compact):
        try:
            decoded = bytes.fromhex(compact).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            decoded = ""
        if decoded.strip().startswith("-----BEGIN"):
            return normalize_pem(decoded)
    try:
        raw = base64.b64decode(compact, validate=True)
    except (binascii.Error, ValueError):
        raw = b""
    if raw.lstrip().startswith(b"-----BEGIN"):
        return normalize_pem(raw)
    raise CredentialError("The private key is not PEM text (or base64 of a .p8 file).", fix=WHOLE_FILE_FIX)


PEM_BEGIN = b"-----BEGIN "
PEM_DASHES = b"-----"
KEY_LABELS = ("PRIVATE KEY", "EC PRIVATE KEY")  # PKCS#8 (what Apple ships) and SEC1


def pem_label(pem: bytes) -> str:
    """The label of the first PEM block, e.g. ``PRIVATE KEY``; empty if there is none."""
    first = pem.lstrip().split(b"\n", 1)[0].strip()
    if first.startswith(PEM_BEGIN) and first.endswith(PEM_DASHES):
        return first[len(PEM_BEGIN) : -len(PEM_DASHES)].decode("ascii", "replace")
    return ""


def validate_pem(pem: bytes) -> None:
    label = pem_label(pem)
    if label.startswith("ENCRYPTED"):
        raise CredentialError(
            "Encrypted private keys are not supported; App Store Connect .p8 keys are unencrypted.",
            fix="use the AuthKey_<KEY_ID>.p8 file exactly as App Store Connect gave it to you",
        )
    if label not in KEY_LABELS:
        raise CredentialError(
            "The private key must be the PEM .p8 file App Store Connect gave you.",
            fix="use the AuthKey_<KEY_ID>.p8 file as App Store Connect gave it (its first line is the BEGIN "
            "PRIVATE KEY line)",
        )
    if b"-----END " + label.encode("ascii") not in pem:
        raise CredentialError("The private key PEM is truncated (no END line).", fix=WHOLE_FILE_FIX)


def read_keychain(
    service: str,
    account: str | None,
    runner: Runner = default_runner,
    platform: str = sys.platform,
) -> str:
    """Read a generic password from the macOS login Keychain."""
    if platform != "darwin":
        raise CredentialError(
            "The macOS Keychain source only works on macOS.",
            fix="on this system use a chmod 600 key file (ASC_PRIVATE_KEY_PATH) or AWS SSM (ASC_SSM_PARAMETER)",
        )
    argv = ["security", "find-generic-password", "-s", service]
    if account:
        argv += ["-a", account]
    argv.append("-w")
    try:
        proc = runner(argv)
    except (OSError, subprocess.SubprocessError) as exc:
        raise CredentialError(f"Could not run `security`: {exc.__class__.__name__}.") from None
    if proc.returncode != 0:
        raise CredentialError(
            f"Keychain lookup failed (security exited with {proc.returncode}). "
            "Check ASC_KEYCHAIN_SERVICE / ASC_KEYCHAIN_ACCOUNT.",
            fix=keychain_exit_hint(proc.returncode),
        )
    return proc.stdout.decode("utf-8", "replace").strip()


def read_ssm(parameter: str, runner: Runner = default_runner) -> str:
    """Read a SecureString parameter with the AWS CLI (credentials come from the AWS CLI's own chain)."""
    if shutil.which("aws") is None and runner is default_runner:
        raise CredentialError(
            "ASC_SSM_PARAMETER needs the AWS CLI (`aws`) on PATH.",
            fix="install the AWS CLI version 2 and check it with `aws sts get-caller-identity`",
        )
    argv = [
        "aws",
        "ssm",
        "get-parameter",
        "--name",
        parameter,
        "--with-decryption",
        "--query",
        "Parameter.Value",
        "--output",
        "text",
    ]
    try:
        proc = runner(argv)
    except (OSError, subprocess.SubprocessError) as exc:
        raise CredentialError(f"Could not run the AWS CLI: {exc.__class__.__name__}.") from None
    if proc.returncode != 0:
        raise CredentialError(
            f"AWS SSM lookup failed (aws exited with {proc.returncode}). Check the parameter name, "
            "region and your AWS credentials.",
            fix=aws_exit_hint(proc.returncode),
        )
    return proc.stdout.decode("utf-8", "replace").strip()


def _candidates(settings: Settings, env: Mapping[str, str]) -> list[str]:
    """Pick the key sources at the highest configuration level that has any."""
    levels: dict[str, list[str]] = {"high": [], "config": []}
    if env.get("ASC_PRIVATE_KEY"):
        levels["high"].append("env")
    for source, attr in (("file", "private_key_path"), ("keychain", "keychain_service"), ("ssm", "ssm_parameter")):
        if getattr(settings, attr):
            level = "config" if settings.origin(attr) == "config" else "high"
            levels[level].append(source)
    chosen = levels["high"] or levels["config"]
    if len(chosen) > 1:
        raise CredentialError(
            "Several private key sources are configured at once ("
            + ", ".join(SOURCE_LABELS[s] for s in chosen)
            + "). Keep exactly one.",
            fix="unset all but one of ASC_PRIVATE_KEY, ASC_PRIVATE_KEY_PATH, ASC_KEYCHAIN_SERVICE and ASC_SSM_PARAMETER",
        )
    return chosen


def key_sources(settings: Settings, env: Mapping[str, str]) -> list[str]:
    """The configured key source as a one-element list, or ``[]``; raises when several are set at one level."""
    return _candidates(settings, env)


def check_identifiers(settings: Settings) -> None:
    """Refuse a missing key ID, or an issuer ID that doesn't fit the key type."""
    if not settings.key_id:
        raise CredentialError(
            "Set ASC_KEY_ID (or auth.key_id in the config file) to the key's ID.",
            fix="copy the KEY ID column from " + KEYS_PAGE + " > Team Keys, then export ASC_KEY_ID=ABC123DEFG",
        )
    if settings.key_type == "team" and not settings.issuer_id:
        raise CredentialError(
            "Team keys need ASC_ISSUER_ID (Users and Access > Integrations in App Store Connect). "
            "For an individual key set ASC_KEY_TYPE=individual.",
            fix="copy the Issuer ID shown above the Team Keys list, then "
            "export ASC_ISSUER_ID=00000000-0000-0000-0000-000000000000",
        )
    if settings.key_type == "individual" and settings.issuer_id:
        raise CredentialError(
            "Individual keys don't use an issuer ID; unset ASC_ISSUER_ID / auth.issuer_id.",
            fix="unset ASC_ISSUER_ID (and delete auth.issuer_id from the config file)",
        )


def _keychain_pem(value: str) -> bytes:
    """Decode a Keychain secret, recognising one that macOS's password prompt cut off."""
    try:
        pem = normalize_pem(value)
        validate_pem(pem)
    except CredentialError:
        length = len(value.strip())
        if length <= KEYCHAIN_PROMPT_LIMIT:
            raise CredentialError(
                f"The Keychain item holds only {length} characters, so it isn't a complete key: macOS's interactive "
                f"password prompt (`security add-generic-password ... -w` with no value) keeps only the first "
                f"{KEYCHAIN_PROMPT_LIMIT} characters (observed October 2026).",
                fix="store it again with the value on the command line (your service and account names): "
                + KEYCHAIN_STORE_COMMAND,
            ) from None
        raise
    return pem


def read_private_key(
    source: str,
    settings: Settings,
    env: Mapping[str, str],
    runner: Runner = default_runner,
    platform: str = sys.platform,
) -> tuple[bytes, str | None]:
    """Read and check the key from ``source``; return ``(PEM, resolved file path or None)``.

    A key that isn't a complete PEM gets the fix for its source (the Keychain command,
    the SSM command, the CI secret), not a generic one.
    """
    path: str | None = None
    try:
        if source == "env":
            pem = normalize_pem(env["ASC_PRIVATE_KEY"])
        elif source == "file":
            pem, path = read_key_file(os.path.abspath(os.path.expanduser(str(settings.private_key_path))))
        elif source == "keychain":
            pem = _keychain_pem(
                read_keychain(str(settings.keychain_service), settings.keychain_account, runner, platform)
            )
        else:
            pem = normalize_pem(read_ssm(str(settings.ssm_parameter), runner))
        validate_pem(pem)
    except CredentialError as exc:
        if exc.fix == WHOLE_FILE_FIX:
            exc.fix = WHOLE_KEY_FIXES.get(source, WHOLE_FILE_FIX)
        raise
    return pem, path


def load_api_key(
    settings: Settings,
    env: Mapping[str, str] | None = None,
    runner: Runner = default_runner,
    platform: str = sys.platform,
) -> ApiKey:
    """Load the key described by ``settings`` and the environment."""
    env = os.environ if env is None else env
    chosen = key_sources(settings, env)
    if not chosen:
        raise CredentialError(NO_KEY_HELP, fix=NO_KEY_FIX)
    check_identifiers(settings)
    source = chosen[0]
    pem, path = read_private_key(source, settings, env, runner, platform)
    return ApiKey(
        key_id=str(settings.key_id),
        issuer_id=settings.issuer_id if settings.key_type == "team" else None,
        key_type=settings.key_type,
        pem=pem,
        source=source,
        path=path,
    )


def key_filename(api_key: ApiKey) -> str:
    """The file name Apple's tools expect (``AuthKey_`` for team keys, ``ApiKey_`` for individual keys)."""
    prefix = "ApiKey" if api_key.key_type == "individual" else "AuthKey"
    return f"{prefix}_{api_key.key_id}.p8"


@contextlib.contextmanager
def key_file(api_key: ApiKey) -> Iterator[str]:
    """Yield a path to the key, materializing a 0600 temp copy when it lives in memory.

    The copy sits in a fresh 0700 directory and is deleted when the block ends,
    even on errors, Ctrl-C, SIGTERM or SIGHUP (the command line turns those into a
    normal exit). Only a ``SIGKILL`` or a crash of the machine in the middle would
    leave it behind in the system temp directory; nothing can clean up after that.
    """
    if api_key.path:
        yield api_key.path
        return
    directory = tempfile.mkdtemp(prefix="asc-release-kit-")
    path = os.path.join(directory, key_filename(api_key))
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(api_key.pem)
        yield path
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(path)
        with contextlib.suppress(OSError):
            os.rmdir(directory)
