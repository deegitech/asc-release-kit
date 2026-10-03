"""Append-only JSON Lines journal of every write and every read-back verification.

Each line is one event::

    {"ts": "2030-03-03T12:00:00Z", "run": "3f2a9c1d0b7e", "command": "release",
     "event": "write", "method": "PATCH", "path": "/v1/appStoreVersionLocalizations/…",
     "status": 200, "resource": {"type": "appStoreVersionLocalizations", "id": "…"},
     "request": {...}}

Contact details and demo-account fields are replaced, review notes are reduced to
a length and a hash, and the usual secret patterns are scrubbed. The file has mode
0600 (an existing file is tightened too), new directories get 0700, and the kit
never writes the journal through a symbolic link.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any

from .errors import UsageError
from .fsutil import is_inside, open_private, private_dirs
from .redact import Redactor


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class Journal:
    """``confine_to``: a directory the journal must stay inside (used when a config file chose the path)."""

    def __init__(
        self,
        path: str,
        redactor: Redactor,
        command: str,
        run_id: str | None = None,
        confine_to: str | None = None,
    ) -> None:
        self.path = path
        self.command = command
        self.run_id = run_id or uuid.uuid4().hex[:12]
        self.confine_to = confine_to
        self._redactor = redactor
        self.events = 0

    def _location(self) -> tuple[str, str]:
        path = os.path.abspath(self.path)
        directory = os.path.dirname(path)
        if self.confine_to is not None and not is_inside(self.confine_to, directory):
            raise UsageError(
                "journal.path in the config file leads outside the current directory (through a symbolic "
                "link); set ASC_JOURNAL to keep the journal somewhere else."
            )
        return path, directory

    def preflight(self) -> None:
        """Refuse an unusable journal before the first write to App Store Connect, not after it."""
        path, _ = self._location()
        if os.path.islink(path):
            raise UsageError(f"{os.path.basename(path)} is a symbolic link; the kit doesn't write through links.")

    def record(self, event: str, **fields: Any) -> None:
        entry: dict[str, Any] = {"ts": utc_now(), "run": self.run_id, "command": self.command, "event": event}
        entry.update(self._redactor.record(fields))
        line = json.dumps(entry, ensure_ascii=False) + "\n"
        path, directory = self._location()
        private_dirs(directory)
        fd = open_private(path, append=True, follow_symlinks=False)
        with os.fdopen(fd, "a", encoding="utf-8") as handle:
            handle.write(line)
        self.events += 1
