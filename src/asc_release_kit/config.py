"""Settings resolution: command-line flags > environment > config file > defaults.

The config file holds identifiers and preferences only. Private keys and tokens
are refused here on purpose; see ``credentials.py`` for where keys may live.

A config file is often committed to an app's repository, so it may come from a
branch or pull request you haven't reviewed. Settings that decide where the token
goes or which program sees the key (``ASC_API_BASE_URL``, ``ASC_SIGNER``,
``ASC_OPENSSL``) are therefore read from the environment only, and a journal path
set in a config file has to stay inside the project.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from pathlib import PurePath
from typing import Any

from .errors import UsageError

CONFIG_NAMES = ("asc-release-kit.toml", "asc-release-kit.json")
PLATFORMS = ("IOS", "MAC_OS", "TV_OS", "VISION_OS")
KEY_TYPES = ("team", "individual")
SIGNERS = ("auto", "cryptography", "openssl")
#: Apple's App Store Connect API host.
APPLE_API_HOST = ".".join(("api", "appstoreconnect", "apple", "com"))
DEFAULT_BASE_URL = "https://" + APPLE_API_HOST
DEFAULT_JOURNAL = "asc-journal.jsonl"

#: Shared fixes for settings errors (``doctor`` prints them next to the failed check).
APP_ID_FIX = (
    "`asc-release-kit apps list` prints the Apple ID (first column); in App Store Connect: Apps > your app > "
    "App Information > Apple ID (a number such as 1234567890, not the bundle ID)"
)
VENDOR_FIX = 'App Store Connect > Payments and Financial Reports shows it at the top left ("Vendor #"), e.g. 87654321'
EXAMPLE_CONFIG_FIX = "compare with examples/asc-release-kit.toml: sections [auth], [app], [reports] and [journal]"

#: config file section/key -> Settings attribute
FILE_KEYS: dict[tuple[str, str], str] = {
    ("auth", "key_id"): "key_id",
    ("auth", "issuer_id"): "issuer_id",
    ("auth", "key_type"): "key_type",
    ("auth", "private_key_path"): "private_key_path",
    ("auth", "keychain_service"): "keychain_service",
    ("auth", "keychain_account"): "keychain_account",
    ("auth", "ssm_parameter"): "ssm_parameter",
    ("auth", "token_ttl_seconds"): "token_ttl_seconds",
    ("app", "id"): "app_id",
    ("app", "platform"): "platform",
    ("app", "bundle_id"): "bundle_id",
    ("reports", "vendor_number"): "vendor_number",
    ("journal", "path"): "journal_path",
}

#: Settings that decide where credentials go or which program sees the key. They are
#: refused in config files (which may come from a repository you don't control) and
#: read from these environment variables only.
ENV_ONLY_FILE_KEYS: dict[tuple[str, str], str] = {
    ("auth", "signer"): "ASC_SIGNER",
    ("auth", "openssl"): "ASC_OPENSSL",
    ("api", "base_url"): "ASC_API_BASE_URL",
}

#: environment variable -> Settings attribute.
#: ``ASC_PRIVATE_KEY`` (the key itself) is read by credentials.py and never stored here.
ENV_KEYS: dict[str, str] = {
    "ASC_KEY_ID": "key_id",
    "ASC_ISSUER_ID": "issuer_id",
    "ASC_KEY_TYPE": "key_type",
    "ASC_PRIVATE_KEY_PATH": "private_key_path",
    "ASC_KEYCHAIN_SERVICE": "keychain_service",
    "ASC_KEYCHAIN_ACCOUNT": "keychain_account",
    "ASC_SSM_PARAMETER": "ssm_parameter",
    "ASC_TOKEN_TTL": "token_ttl_seconds",
    "ASC_SIGNER": "signer",
    "ASC_OPENSSL": "openssl",
    "ASC_APP_ID": "app_id",
    "ASC_PLATFORM": "platform",
    "ASC_BUNDLE_ID": "bundle_id",
    "ASC_VENDOR_NUMBER": "vendor_number",
    "ASC_JOURNAL": "journal_path",
    "ASC_API_BASE_URL": "api_base_url",
}

#: Keys that look like secrets. Refused anywhere in the config file.
FORBIDDEN_KEYS = frozenset({"private_key", "private_key_pem", "pem", "p8", "token", "jwt", "password", "secret"})


@dataclass
class Settings:
    """Resolved, validated settings. Contains identifiers, never secrets."""

    key_id: str | None = None
    issuer_id: str | None = None
    key_type: str = "team"
    private_key_path: str | None = None
    keychain_service: str | None = None
    keychain_account: str | None = None
    ssm_parameter: str | None = None
    token_ttl_seconds: int = 900
    signer: str = "auto"
    openssl: str = "openssl"
    app_id: str | None = None
    platform: str = "IOS"
    bundle_id: str | None = None
    vendor_number: str | None = None
    journal_path: str = DEFAULT_JOURNAL
    api_base_url: str = DEFAULT_BASE_URL
    config_path: str | None = None
    #: how the config file was chosen: "flag" (--config), "env" (ASC_CONFIG) or "cwd" (found in the current directory)
    config_origin: str | None = None
    #: attribute -> where its value came from: "flag", "env", "config" or "default"
    origins: dict[str, str] = field(default_factory=dict)

    def origin(self, name: str) -> str:
        return self.origins.get(name, "default")


def find_config(explicit: str | None, env: Mapping[str, str], cwd: str) -> tuple[str | None, str | None]:
    """Return ``(path, origin)`` of the config file to use, or ``(None, None)`` when there is none."""
    if explicit:
        if not os.path.isfile(explicit):
            raise UsageError(f"Config file not found: {explicit}", fix="check the --config path")
        return explicit, "flag"
    from_env = env.get("ASC_CONFIG")
    if from_env:
        if not os.path.isfile(from_env):
            raise UsageError("ASC_CONFIG points to a file that does not exist.", fix="fix or unset ASC_CONFIG")
        return from_env, "env"
    for name in CONFIG_NAMES:
        candidate = os.path.join(cwd, name)
        if os.path.isfile(candidate):
            return candidate, "cwd"
    return None, None


def _toml_module() -> Any:
    if sys.version_info >= (3, 11):
        import tomllib

        return tomllib
    try:  # pragma: no cover - depends on the interpreter
        import tomli  # type: ignore[import-not-found]
    except ModuleNotFoundError:  # pragma: no cover
        raise UsageError(
            "Reading a .toml config on Python 3.10 needs tomli: pip install 'asc-release-kit[toml]'. "
            "You can also use asc-release-kit.json with the same sections.",
            fix='pipx inject asc-release-kit tomli (or: python3 -m pip install "asc-release-kit[toml]")',
        ) from None
    return tomli  # pragma: no cover


def _load_toml(path: str) -> dict[str, Any]:
    toml = _toml_module()
    try:
        with open(path, "rb") as handle:
            return dict(toml.load(handle))
    except (OSError, ValueError) as exc:
        raise UsageError(
            f"Could not read {os.path.basename(path)}: {exc}", fix="fix the TOML syntax at the position shown"
        ) from None


def read_config_file(path: str) -> dict[str, Any]:
    """Parse a TOML or JSON config file into nested dicts."""
    if path.endswith(".toml"):
        data = _load_toml(path)
    else:
        try:
            with open(path, encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError) as exc:
            raise UsageError(
                f"Could not read {os.path.basename(path)}: {exc}", fix="fix the JSON syntax at the position shown"
            ) from None
    if not isinstance(data, dict):
        raise UsageError("The config file must contain sections such as [auth] and [app].", fix=EXAMPLE_CONFIG_FIX)
    return data


def _check_forbidden(data: Any, where: str = "") -> None:
    if isinstance(data, dict):
        for key, value in data.items():
            if str(key).lower() in FORBIDDEN_KEYS:
                raise UsageError(
                    f"The config file contains '{where}{key}'. Never store private keys or tokens "
                    "in the config file: use a chmod 600 key file, the macOS Keychain, AWS SSM or "
                    "the ASC_PRIVATE_KEY environment variable.",
                    fix=f"delete '{where}{key}' from the config file and store the key as docs/setup.md (step 3) shows",
                )
            _check_forbidden(value, f"{where}{key}.")


def _flatten(data: dict[str, Any]) -> dict[str, Any]:
    _check_forbidden(data)
    out: dict[str, Any] = {}
    for section, body in data.items():
        if section.startswith("_"):
            continue
        if not isinstance(body, dict):
            raise UsageError(f"Config section '{section}' must be a table/object.", fix=EXAMPLE_CONFIG_FIX)
        for key, value in body.items():
            if key.startswith("_"):
                continue
            env_name = ENV_ONLY_FILE_KEYS.get((section, key))
            if env_name is not None:
                raise UsageError(
                    f"'{section}.{key}' can't be set in a config file: a config file may come from a repository "
                    f"you don't control, and this setting decides where credentials go. Set {env_name} in the "
                    "environment instead.",
                    fix=f"delete '{section}.{key}' from the config file and export {env_name} instead",
                )
            attr = FILE_KEYS.get((section, key))
            if attr is None:
                raise UsageError(f"Unknown config setting '{section}.{key}'.", fix=EXAMPLE_CONFIG_FIX)
            out[attr] = value
    return out


def load_settings(
    config_path: str | None = None,
    env: Mapping[str, str] | None = None,
    overrides: Mapping[str, Any] | None = None,
    cwd: str | None = None,
) -> Settings:
    """Resolve settings from the config file, the environment and flags."""
    env = os.environ if env is None else env
    cwd = cwd or os.getcwd()
    settings = Settings()
    path, origin = find_config(config_path, env, cwd)
    if path:
        settings.config_path = path
        settings.config_origin = origin
        for attr, value in _flatten(read_config_file(path)).items():
            setattr(settings, attr, value)
            settings.origins[attr] = "config"
    for name, attr in ENV_KEYS.items():
        value = env.get(name)
        if value not in (None, ""):
            setattr(settings, attr, value)
            settings.origins[attr] = "env"
    for attr, value in (overrides or {}).items():
        if value is not None:
            setattr(settings, attr, value)
            settings.origins[attr] = "flag"
    _validate(settings)
    return settings


_STRING_ATTRS = (
    "key_id",
    "issuer_id",
    "private_key_path",
    "keychain_service",
    "keychain_account",
    "ssm_parameter",
    "openssl",
    "bundle_id",
    "journal_path",
    "api_base_url",
)


def _validate(settings: Settings) -> None:
    for name in _STRING_ATTRS:
        value = getattr(settings, name)
        if value is not None and not isinstance(value, str):
            setattr(settings, name, str(value))
    for item in fields(settings):
        value = getattr(settings, item.name)
        if isinstance(value, str):
            setattr(settings, item.name, value.strip())
    defaults = {"journal_path": DEFAULT_JOURNAL, "api_base_url": DEFAULT_BASE_URL, "openssl": "openssl"}
    for name in _STRING_ATTRS:
        if not getattr(settings, name):
            setattr(settings, name, defaults.get(name))
    settings.key_type = str(settings.key_type).lower()
    if settings.key_type not in KEY_TYPES:
        raise UsageError(
            "Key type must be 'team' or 'individual'.",
            fix="ASC_KEY_TYPE=team for keys from the Team Keys tab, individual for a key from your own profile",
        )
    settings.platform = str(settings.platform).upper()
    if settings.platform not in PLATFORMS:
        raise UsageError(f"Platform must be one of {', '.join(PLATFORMS)}.", fix="use IOS, MAC_OS, TV_OS or VISION_OS")
    settings.signer = str(settings.signer).lower()
    if settings.signer not in SIGNERS:
        raise UsageError(f"Signer must be one of {', '.join(SIGNERS)}.", fix="unset ASC_SIGNER (auto picks one)")
    try:
        settings.token_ttl_seconds = int(settings.token_ttl_seconds)
    except (TypeError, ValueError):
        raise UsageError(
            "token_ttl_seconds must be a whole number of seconds.", fix="use 900 (the default) or leave it out"
        ) from None
    if not 60 <= settings.token_ttl_seconds <= 1200:
        raise UsageError(
            "token_ttl_seconds must be between 60 and 1200 (Apple's 20-minute limit).",
            fix="use 900 (the default) or leave it out",
        )
    if settings.app_id is not None:
        settings.app_id = str(settings.app_id)
        if not settings.app_id.isdigit():
            raise UsageError("The app ID is the numeric Apple ID of the app (for example 1234567890).", fix=APP_ID_FIX)
    if settings.vendor_number is not None:
        settings.vendor_number = str(settings.vendor_number)
        if not settings.vendor_number.isdigit():
            raise UsageError(
                "The vendor number must be numeric (App Store Connect > Payments and Financial Reports).",
                fix=VENDOR_FIX,
            )
    if settings.origin("journal_path") == "config":
        journal = PurePath(settings.journal_path)
        if journal.is_absolute() or journal.drive or ".." in journal.parts:
            raise UsageError(
                "journal.path in a config file must be a relative path inside the project (no '..'). "
                "Set ASC_JOURNAL to keep the journal somewhere else.",
                fix="use a relative path such as asc-journal.jsonl, or export ASC_JOURNAL instead",
            )
