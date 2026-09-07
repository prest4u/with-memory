from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from jsonschema import validate

from eric_memory.mcp_server import TOOLS_BY_NAME, dispatch_tool
from eric_memory.migration import migrate_apply, migrate_rollback
from eric_memory.paths import MemoryConfig, save_config
from tests.helpers import TempServiceTest
from tests.test_migration_backup_purge import make_v1_fixture


class PostCommitResultTests(TempServiceTest):
    def test_log_failures_preserve_committed_add_candidate_accept_and_restore_results(self) -> None:
        backup = self.service.backup_create()["backup"]
        with patch.object(self.service, "_log", side_effect=OSError("synthetic log failure")):
            added = dispatch_tool(self.service, "memory_add", {"content": "Committed despite a log failure."})
            self.assertFalse(added["isError"])
            validate(added["structuredContent"], TOOLS_BY_NAME["memory_add"]["outputSchema"])
            candidate = self.service.candidate_add(
                content="Candidate remains committed.", submission_uid="log-failure", manual_input=True
            )
            accepted = self.service.review_accept(candidate["candidate"]["candidate_uid"])
            restored = self.service.restore(backup["path"])
        for result in (added["structuredContent"], candidate, accepted, restored):
            self.assertTrue(result["committed"])
            self.assertEqual(result["operation_log"], {"status": "failed", "error_code": "OSError"})
        self.assertEqual(self.service.store.counts()["facts"], 0)

    def test_restore_config_failure_reports_actual_replaced_database(self) -> None:
        backup = self.service.backup_create()["backup"]
        self.service.add("Fact removed by successful restore.")
        with patch("eric_memory.service.save_config", side_effect=OSError("synthetic config failure")):
            result = self.service.restore(backup["path"])
        self.assertTrue(result["committed"])
        self.assertEqual(result["configuration"], "pending")
        self.assertEqual(self.service.store.counts()["facts"], 0)

    def test_migration_and_rollback_config_failure_report_committed_schema(self) -> None:
        data = self.data_dir.parent / "legacy-config-failure"
        database = make_v1_fixture(data)
        save_config(
            MemoryConfig(str(data), str(data / "vault"), str(database), schema_version=1, obsidian_enabled=False)
        )
        with patch("eric_memory.migration.save_config", side_effect=OSError("synthetic config failure")):
            migrated = migrate_apply(data, database)
            self.assertTrue(migrated["committed"])
            self.assertEqual(migrated["configuration"], "pending")
            with closing(sqlite3.connect(database)) as connection, connection:
                self.assertEqual(
                    connection.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()[0], "2"
                )
            rolled = migrate_rollback(data, database)
            self.assertTrue(rolled["committed"])
            self.assertEqual(rolled["configuration"], "pending")
            with closing(sqlite3.connect(database)) as connection, connection:
                self.assertEqual(
                    connection.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()[0], "1"
                )

    def test_purge_backup_cleanup_failure_reports_pending_and_can_be_finished(self) -> None:
        fact = self.service.add("Synthetic purge cleanup failure canary.")["fact"]
        backup = self.service.backup_create()["backup"]
        with patch.object(self.service.backups, "remove_managed", side_effect=OSError("synthetic unlink failure")):
            result = self.service.purge(fact["fact_id"], confirmation=fact["fact_uid"])
        self.assertTrue(result["committed"])
        self.assertEqual(result["purge_cleanup"], "pending")
        self.assertEqual(result["warning"]["code"], "PURGE_CLEANUP_PENDING")
        self.assertIsNone(self.service.store.get_fact(fact["fact_id"]))
        self.assertIn(backup["path"], result["remaining_managed_backups"])
        self.assertTrue(result["clean_backup"]["verified"])
        self.service.backup_remove(result["remaining_managed_backups"])
        self.service.backup_remove(result["remaining_managed_backups"])
        self.assertFalse(any(Path(path).exists() for path in result["remaining_managed_backups"]))
