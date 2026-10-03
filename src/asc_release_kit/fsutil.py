"""Local files the kit writes: private to the current user, written atomically.

Reports, review exports, pulled metadata and the journal can hold sales figures,
reviewer nicknames or identifiers. On POSIX systems every file the kit writes gets
mode 0600 (an existing file is tightened too) and every directory it creates gets
mode 0700. On Windows the directory's own access rules apply.
"""

from __future__ import annotations

import contextlib
import errno
import os
import stat
import tempfile

from .errors import UsageError

PRIVATE_FILE = 0o600
PRIVATE_DIR = 0o700


def _name(path: str) -> str:
    return os.path.basename(path.rstrip(os.sep)) or path


def private_dirs(path: str) -> None:
    """Create ``path`` and missing parents with mode 0700; existing directories are left as they are."""
    if not path:
        return
    try:
        os.makedirs(path, mode=PRIVATE_DIR, exist_ok=True)
    except OSError as exc:
        raise UsageError(f"Can't create the directory {_name(path)}: {exc.strerror or exc}.") from None


def _make_private(fd: int, name: str) -> None:
    if os.name != "posix" or not stat.S_ISREG(os.fstat(fd).st_mode):
        return  # devices such as /dev/stdout are left alone
    try:
        os.fchmod(fd, PRIVATE_FILE)
    except PermissionError:
        raise UsageError(f"{name} belongs to another user, so the kit can't make it private (mode 0600).") from None


def open_private(path: str, *, append: bool = False, follow_symlinks: bool = True) -> int:
    """Open ``path`` for writing with mode 0600 and return the file descriptor.

    An existing regular file is switched to 0600 as well, because ``O_CREAT``
    only applies the mode to new files. ``append`` adds to the end (the journal);
    otherwise the file is truncated. With ``follow_symlinks=False`` a symbolic link
    as the last path component is refused.
    """
    flags = os.O_WRONLY | os.O_CREAT | (os.O_APPEND if append else os.O_TRUNC)
    flags |= getattr(os, "O_CLOEXEC", 0)
    if not follow_symlinks:
        flags |= getattr(os, "O_NOFOLLOW", 0)
    name = _name(path)
    try:
        fd = os.open(path, flags, PRIVATE_FILE)
    except OSError as exc:
        if exc.errno == errno.ELOOP and not follow_symlinks:
            raise UsageError(f"{name} is a symbolic link; the kit doesn't write through links.") from None
        raise UsageError(f"Can't write {name}: {exc.strerror or exc}.") from None
    try:
        _make_private(fd, name)
    except BaseException:
        os.close(fd)
        raise
    return fd


def write_private(path: str, data: bytes) -> None:
    """Write ``data`` to ``path`` atomically with mode 0600.

    The bytes go to a new temporary file next to ``path`` (created 0600 under a
    random name, so a file or link planted at a predictable name can't redirect the
    write) and then replace ``path`` in a single rename. When ``path`` is a symbolic
    link, the link itself is replaced; its target is never written.
    """
    name = _name(path)
    directory = os.path.dirname(path) or "."
    private_dirs(directory)
    try:
        fd, tmp = tempfile.mkstemp(prefix=f".{name}.", suffix=".part", dir=directory)
    except OSError as exc:
        raise UsageError(f"Can't write {name}: {exc.strerror or exc}.") from None
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.replace(tmp, path)
    except OSError as exc:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise UsageError(f"Can't write {name}: {exc.strerror or exc}.") from None
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def is_inside(root: str, path: str) -> bool:
    """True when ``path`` resolves (following links) to ``root`` or somewhere below it."""
    real_root = os.path.realpath(root)
    real = os.path.realpath(path)
    return real == real_root or real.startswith(real_root.rstrip(os.sep) + os.sep)
