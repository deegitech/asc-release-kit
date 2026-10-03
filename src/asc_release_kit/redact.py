"""Scrub secrets and personal contact details from anything the kit prints or stores.

Two layers:

* literal secrets registered at runtime (key ID, issuer ID, key path, the vendor
  number in sales commands, every token minted in this process) are replaced
  wherever they appear; all-digit values only where they stand alone, so a vendor
  number never garbles a longer number such as an app ID;
* pattern rules catch private keys, JWTs, bearer headers, e-mail addresses and
  international phone numbers.

The journal goes one step further: App Review contact fields are replaced
outright, and review notes (which often carry demo-account hints) are stored
only as a length and a short SHA-256 prefix.
"""

from __future__ import annotations

import hashlib
import os
import re
from collections.abc import Iterable
from typing import Any

REDACTED = "[redacted]"

#: App Review contact and demo-account fields. Never printed, never journaled.
CONTACT_FIELDS = frozenset(
    {
        "contactFirstName",
        "contactLastName",
        "contactPhone",
        "contactEmail",
        "demoAccountName",
        "demoAccountPassword",
    }
)

#: Free-text fields that may contain private hints; journaled as length + hash.
PRIVATE_TEXT_FIELDS = frozenset({"notes"})

_PEM_RE = re.compile(
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?(?:-----END [A-Z0-9 ]*PRIVATE KEY-----|\Z)",
    re.S,
)
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")
_BEARER_RE = re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]{8,}")
_EMAIL_RE = re.compile(r"(?<![\w.+-])[\w.+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
_PHONE_RE = re.compile(r"(?<![\w+])\+\d[\d ().-]{6,}\d")


def summarize_private_text(text: str) -> str:
    """Describe a private text without revealing it (length + short SHA-256)."""
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
    return f"[redacted: {len(text)} chars, sha256 {digest}]"


class Redactor:
    """Collects literal secrets and scrubs strings and JSON-like structures."""

    def __init__(self) -> None:
        self._literals: set[str] = set()

    def add_secret(self, *values: str | None) -> None:
        """Register literal values that must never be shown (short values are ignored)."""
        for value in values:
            if not value:
                continue
            text = str(value)
            if len(text) < 4:
                continue
            self._literals.add(text)
            if os.sep in text or text.startswith("~"):
                expanded = os.path.expanduser(text)
                self._literals.add(expanded)
                self._literals.add(os.path.realpath(expanded))

    def add_secrets(self, values: Iterable[str | None]) -> None:
        self.add_secret(*values)

    def text(self, value: str) -> str:
        """Return ``value`` with every known secret and sensitive pattern masked."""
        if not value:
            return value
        out = str(value)
        for literal in sorted(self._literals, key=len, reverse=True):
            if literal not in out:
                continue
            if literal.isdigit():
                out = re.sub(rf"(?<!\d){re.escape(literal)}(?!\d)", REDACTED, out)
            else:
                out = out.replace(literal, REDACTED)
        out = _PEM_RE.sub("[redacted private key]", out)
        out = _JWT_RE.sub(REDACTED, out)
        out = _BEARER_RE.sub(lambda m: m.group(1) + REDACTED, out)
        out = _EMAIL_RE.sub("[redacted email]", out)
        out = _PHONE_RE.sub("[redacted phone]", out)
        return out

    def record(self, value: Any) -> Any:
        """Deep-copy ``value`` for the journal with contact details removed."""
        if isinstance(value, dict):
            out: dict[str, Any] = {}
            for key, item in value.items():
                if key in CONTACT_FIELDS and item not in (None, ""):
                    out[key] = REDACTED
                elif key in PRIVATE_TEXT_FIELDS and isinstance(item, str) and item:
                    out[key] = summarize_private_text(item)
                else:
                    out[key] = self.record(item)
            return out
        if isinstance(value, (list, tuple)):
            return [self.record(item) for item in value]
        if isinstance(value, str):
            return self.text(value)
        return value
