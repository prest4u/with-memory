"""The single immediate-transaction primitive for every SQLite mutation."""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path

from .locking import exclusive_file_lock


@contextmanager
def immediate_transaction(
    connection: sqlite3.Connection,
    lock_path: Path,
    thread_lock: threading.RLock,
    *,
    lock_context: AbstractContextManager[None] | None = None,
) -> Iterator[None]:
    """Serialize writers, begin immediately, commit once, and roll back on any failure."""
    with lock_context if lock_context is not None else exclusive_file_lock(lock_path), thread_lock:
        migration = connection.execute("SELECT value FROM schema_meta WHERE key = 'migration_backup'").fetchone()
        track_rollback_boundary = bool(migration and migration[0])
        connection.execute("BEGIN IMMEDIATE")
        try:
            yield
            if track_rollback_boundary:
                connection.execute(
                    """
                    INSERT INTO schema_meta(key, value) VALUES ('v2_write_count', '1')
                    ON CONFLICT(key) DO UPDATE SET value =
                        CAST(CAST(schema_meta.value AS INTEGER) + 1 AS TEXT)
                    """
                )
        except Exception:
            connection.rollback()
            raise
        else:
            connection.commit()
