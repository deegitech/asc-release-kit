"""Exception types and the exit codes the command line maps them to."""

from __future__ import annotations

from typing import Any

EXIT_OK = 0
EXIT_CHECKS_FAILED = 1
EXIT_USAGE = 2
EXIT_API = 3
EXIT_BLOCKED = 4


class KitError(Exception):
    """Base class for errors that the CLI reports without a traceback.

    ``fix`` is an optional one-line instruction (a command, a menu path, a setting)
    that the command line prints as ``fix: ...`` under the error, and that
    ``doctor`` shows next to a failed check.
    """

    exit_code = EXIT_CHECKS_FAILED

    def __init__(self, message: str = "", *, fix: str = "") -> None:
        super().__init__(message)
        self.fix = fix


class UsageError(KitError):
    """Bad flags, bad configuration or an unreadable input file."""

    exit_code = EXIT_USAGE


class CredentialError(UsageError):
    """The API key could not be loaded safely."""


class SecurityError(UsageError):
    """A safety rule refused the operation (for example a cross-origin pagination link)."""


class CheckFailed(KitError):
    """Validation or read-back verification failed."""

    exit_code = EXIT_CHECKS_FAILED


class Blocked(KitError):
    """App Store Connect is in a state that does not allow the action (checks run before writes)."""

    exit_code = EXIT_BLOCKED


class TransportError(KitError):
    """The network request failed before App Store Connect answered."""

    exit_code = EXIT_API


class ApiError(KitError):
    """App Store Connect answered with an unexpected HTTP status.

    ``hint`` (the fix from ``hints.api_hint``) becomes ``fix``, like every other error's:
    the command line prints it as a ``fix:`` line and ``--json`` output carries it as
    ``fix``. The message itself holds the request and Apple's codes only.
    """

    exit_code = EXIT_API

    def __init__(
        self,
        method: str,
        path: str,
        status: int,
        errors: list[dict[str, Any]] | None = None,
        hint: str = "",
    ) -> None:
        self.method = method
        self.path = path
        self.status = status
        self.errors = errors or []
        super().__init__(self._message(), fix=hint)

    @property
    def hint(self) -> str:
        return self.fix

    def _message(self) -> str:
        lines = [f"{self.method} {self.path} -> HTTP {self.status}"]
        for err in self.errors[:3]:
            code = str(err.get("code") or "").strip()
            detail = str(err.get("detail") or err.get("title") or "").strip()
            text = ": ".join(part for part in (code, detail) if part)
            if text:
                lines.append(text)
        return "\n  ".join(lines)

    @property
    def codes(self) -> list[str]:
        return [str(err.get("code") or "") for err in self.errors]
