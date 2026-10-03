"""Human-readable or JSON output for every command.

Text mode streams lines as work happens. JSON mode collects the same events and
prints one document at the end, so scripts can parse the result. When a command
fails in JSON mode, the document still carries every event collected until then
(read-back checks, writes already made) next to the error.
"""

from __future__ import annotations

import json
import sys
from typing import Any, TextIO

from .redact import Redactor

GLYPHS = {
    "ok": "✓",
    "fail": "✗",
    "warn": "!",
    "change": "✎",
    "plan": "(plan)",
    "info": "·",
    "same": "=",
}
ASCII_GLYPHS = {
    "ok": "[ok]",
    "fail": "[x]",
    "warn": "[!]",
    "change": "[w]",
    "plan": "(plan)",
    "info": "-",
    "same": "=",
}


def _can_encode(stream: TextIO, text: str) -> bool:
    encoding = getattr(stream, "encoding", None) or "utf-8"
    try:
        text.encode(encoding)
    except (UnicodeEncodeError, LookupError):
        return False
    return True


class Reporter:
    """Prints progress lines; in plan mode, changes are marked ``(plan)`` instead of ``✎``."""

    def __init__(
        self,
        *,
        apply: bool = False,
        json_mode: bool = False,
        ascii_only: bool = False,
        stream: TextIO | None = None,
        redactor: Redactor | None = None,
    ) -> None:
        self.apply = apply
        self.json_mode = json_mode
        self.stream = stream or sys.stdout
        self.redactor = redactor or Redactor()
        self.events: list[dict[str, Any]] = []
        self.notes: list[str] = []
        #: basename of the config file in use, if any (shown in every command's output)
        self.config: str | None = None
        self.failures = 0
        self.warnings = 0
        self.changes = 0
        use_ascii = ascii_only or not _can_encode(self.stream, "✓✗✎·")
        self.glyphs = ASCII_GLYPHS if use_ascii else GLYPHS

    # -- primitives -------------------------------------------------------------------------
    def _emit(self, kind: str, text: str, indent: int = 1, **data: Any) -> None:
        text = self.redactor.text(text)
        if self.json_mode:
            event: dict[str, Any] = {"kind": kind, "text": text}
            event.update(data)
            self.events.append(event)
            return
        glyph = self.glyphs.get(kind, "")
        prefix = "  " * indent + (glyph + " " if glyph else "")
        print(prefix + text, file=self.stream)

    def title(self, text: str) -> None:
        if self.json_mode:
            self.events.append({"kind": "title", "text": self.redactor.text(text)})
        else:
            print(self.redactor.text(text), file=self.stream)

    def blank(self) -> None:
        if not self.json_mode:
            print(file=self.stream)

    def info(self, text: str, **data: Any) -> None:
        self._emit("info", text, **data)

    def ok(self, text: str, **data: Any) -> None:
        self._emit("ok", text, **data)

    def same(self, text: str, **data: Any) -> None:
        self._emit("same", text, **data)

    def warn(self, text: str, **data: Any) -> None:
        self.warnings += 1
        self._emit("warn", text, **data)

    def fail(self, text: str, **data: Any) -> None:
        self.failures += 1
        self._emit("fail", text, **data)

    def check(self, passed: bool, text: str, **data: Any) -> bool:
        (self.ok if passed else self.fail)(text, **data)
        return passed

    def change(self, text: str, **data: Any) -> None:
        """A write: done (apply) or proposed (plan)."""
        self.changes += 1
        self._emit("change" if self.apply else "plan", text, **data)

    def note(self, text: str) -> None:
        """Context for the reader, such as which config file is in use.

        Text mode prints it to stderr, so it never mixes with data a command writes
        to stdout (a CSV export, for example); JSON mode puts it in ``notes``.
        """
        text = self.redactor.text(text)
        self.notes.append(text)
        if not self.json_mode:
            print(f"note: {text}", file=sys.stderr)

    def plain(self, text: str) -> None:
        """Unprefixed text line (tables)."""
        if self.json_mode:
            self.events.append({"kind": "text", "text": self.redactor.text(text)})
        else:
            print(self.redactor.text(text), file=self.stream)

    def document(self, command: str, result: dict[str, Any] | None = None) -> dict[str, Any]:
        return {
            "command": command,
            "mode": "apply" if self.apply else "plan",
            "config": self.config,
            "notes": self.notes,
            "changes": self.changes,
            "warnings": self.warnings,
            "failures": self.failures,
            "events": self.events,
            "result": self.redactor.record(result) if result is not None else None,
        }

    def finish(self, command: str, result: dict[str, Any] | None = None) -> None:
        if not self.json_mode:
            return
        print(json.dumps(self.document(command, result), indent=2, ensure_ascii=False), file=self.stream)

    def error_document(self, command: str, message: str, exit_code: int) -> dict[str, Any]:
        """The JSON document for a failed command: the events so far plus the error."""
        document = self.document(command)
        document.update(error=self.redactor.text(message), exitCode=exit_code)
        return document
