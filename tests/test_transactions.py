from __future__ import annotations

import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

from eric_memory.transactions import immediate_transaction


class ImmediateTransactionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.connection = sqlite3.connect(self.root / "transaction.sqlite3", isolation_level=None)
        self.connection.executescript(
            """
            CREATE TABLE schema_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT INTO schema_meta VALUES ('migration_backup', '');
            INSERT INTO schema_meta VALUES ('v2_write_count', '0');
            CREATE TABLE values_table(value TEXT NOT NULL);
            """
        )
        self.lock = threading.RLock()
        self.lock_path = self.root / "transaction.lock"

    def tearDown(self) -> None:
        self.connection.close()
        self.temporary.cleanup()

    def test_commit_without_migration_does_not_move_rollback_boundary(self) -> None:
        with immediate_transaction(self.connection, self.lock_path, self.lock):
            self.connection.execute("INSERT INTO values_table VALUES ('committed')")
        self.assertEqual(self.connection.execute("SELECT COUNT(*) FROM values_table").fetchone()[0], 1)
        self.assertEqual(
            self.connection.execute("SELECT value FROM schema_meta WHERE key='v2_write_count'").fetchone()[0],
            "0",
        )

    def test_migrated_commit_increments_boundary_in_same_transaction(self) -> None:
        self.connection.execute("UPDATE schema_meta SET value='backup.sqlite3' WHERE key='migration_backup'")
        with immediate_transaction(self.connection, self.lock_path, self.lock):
            self.connection.execute("INSERT INTO values_table VALUES ('v2 write')")
        self.assertEqual(
            self.connection.execute("SELECT value FROM schema_meta WHERE key='v2_write_count'").fetchone()[0],
            "1",
        )

    def test_exception_rolls_back_body_and_boundary_increment(self) -> None:
        self.connection.execute("UPDATE schema_meta SET value='backup.sqlite3' WHERE key='migration_backup'")
        with (
            self.assertRaisesRegex(RuntimeError, "injected"),
            immediate_transaction(self.connection, self.lock_path, self.lock),
        ):
            self.connection.execute("INSERT INTO values_table VALUES ('rolled back')")
            raise RuntimeError("injected")
        self.assertEqual(self.connection.execute("SELECT COUNT(*) FROM values_table").fetchone()[0], 0)
        self.assertEqual(
            self.connection.execute("SELECT value FROM schema_meta WHERE key='v2_write_count'").fetchone()[0],
            "0",
        )
