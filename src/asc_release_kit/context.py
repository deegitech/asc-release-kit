"""Per-command wiring: settings, redactor, reporter, journal, credentials and client."""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from .api import AscClient, check_base_url, loopback_allowed
from .config import APP_ID_FIX, Settings, load_settings
from .credentials import ApiKey, load_api_key
from .errors import UsageError
from .journal import Journal
from .jwt import TokenProvider, make_signer
from .output import Reporter
from .redact import Redactor

#: How a config file was chosen, as shown to the user.
CONFIG_ORIGINS = {
    "flag": "from --config",
    "env": "from ASC_CONFIG",
    "cwd": "found in the current directory",
}


@dataclass
class Context:
    settings: Settings
    redactor: Redactor
    reporter: Reporter
    command: str
    apply: bool = False
    verbose: bool = False
    env: Mapping[str, str] = field(default_factory=lambda: os.environ)
    _api_key: ApiKey | None = None
    _client: AscClient | None = None
    _journal: Journal | None = None
    _tokens: TokenProvider | None = None

    @classmethod
    def create(
        cls,
        *,
        command: str,
        config: str | None = None,
        overrides: Mapping[str, Any] | None = None,
        apply: bool = False,
        json_mode: bool = False,
        ascii_only: bool = False,
        verbose: bool = False,
        redactor: Redactor | None = None,
        env: Mapping[str, str] | None = None,
    ) -> Context:
        env = os.environ if env is None else env
        redactor = redactor or Redactor()
        settings = load_settings(config, env, overrides)
        # The vendor number is registered by `sales download`, the only command that uses it.
        redactor.add_secret(settings.key_id, settings.issuer_id, settings.private_key_path)
        reporter = Reporter(apply=apply, json_mode=json_mode, ascii_only=ascii_only, redactor=redactor)
        if settings.config_path:
            name = os.path.basename(settings.config_path)
            reporter.config = name
            reporter.note(f"using config file {name} ({CONFIG_ORIGINS.get(settings.config_origin or '', 'chosen')})")
        return cls(
            settings=settings,
            redactor=redactor,
            reporter=reporter,
            command=command,
            apply=apply,
            verbose=verbose,
            env=env,
        )

    # -- lazily created pieces ------------------------------------------------------------------
    def api_key(self) -> ApiKey:
        if self._api_key is None:
            self._api_key = load_api_key(self.settings, self.env)
            self.redactor.add_secrets(self._api_key.identifiers())
        return self._api_key

    def tokens(self) -> TokenProvider:
        if self._tokens is None:
            key = self.api_key()
            signer = make_signer(key, self.settings.signer, self.settings.openssl)
            self._tokens = TokenProvider(
                key,
                signer,
                ttl=self.settings.token_ttl_seconds,
                on_new_token=self.redactor.add_secret,
            )
        return self._tokens

    def journal(self) -> Journal:
        if self._journal is None:
            confine = os.getcwd() if self.settings.origin("journal_path") == "config" else None
            journal = Journal(self.settings.journal_path, self.redactor, self.command, confine_to=confine)
            if self.apply:
                journal.preflight()  # a broken journal must stop the run before the first write
            self._journal = journal
        return self._journal

    def client(self) -> AscClient:
        if self._client is None:
            allow_loopback = loopback_allowed(self.env)
            # Refuse a bad API host before any key is loaded (no Keychain prompt, no SSM call).
            check_base_url(self.settings.api_base_url, allow_loopback=allow_loopback)
            self._client = AscClient(
                self.tokens(),
                base_url=self.settings.api_base_url,
                journal=self.journal(),
                redactor=self.redactor,
                verbose=self._log if self.verbose else None,
                read_only=not self.apply,
                allow_loopback=allow_loopback,
            )
        return self._client

    def _log(self, line: str) -> None:
        print(self.redactor.text(line), file=sys.stderr)

    # -- common lookups ---------------------------------------------------------------------------
    def app_id(self, flag: str | None = None) -> str:
        value = flag or self.settings.app_id
        if not value:
            raise UsageError("Pass --app APP_ID or set ASC_APP_ID / [app] id in the config file.", fix=APP_ID_FIX)
        value = str(value)
        if not value.isdigit():
            raise UsageError("The app ID is the numeric Apple ID of the app (for example 1234567890).", fix=APP_ID_FIX)
        return value

    def platform(self, flag: str | None = None) -> str:
        value = (flag or self.settings.platform or "IOS").upper()
        return value
