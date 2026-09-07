"""Small cross-platform exclusive file lock used by migrations and restore."""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .errors import ConflictError


@contextmanager
def exclusive_file_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    handle = os.fdopen(fd, "a+b")
    acquired = False
    try:
        try:
            if os.name == "nt":
                import msvcrt

                lock_api: Any = msvcrt
                handle.seek(0, os.SEEK_END)
                if handle.tell() == 0:
                    handle.write(b"0")
                    handle.flush()
                handle.seek(0)
                lock_api.locking(handle.fileno(), lock_api.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except OSError as exc:
            raise ConflictError("another With. maintenance operation is already running") from exc
        yield
    finally:
        try:
            if acquired and os.name == "nt":
                import msvcrt

                lock_api = msvcrt
                handle.seek(0)
                lock_api.locking(handle.fileno(), lock_api.LK_UNLCK, 1)
            elif acquired:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()
