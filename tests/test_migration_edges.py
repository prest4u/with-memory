from __future__ import annotations

import hashlib
import os
import sqlite3
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from eric_memory.backup import BackupManager
from eric_memory.errors import ConfirmationRequiredError, ConflictError, IntegrityCheckError, ValidationError
from eric_memory.locking import exclusive_file_lock
from eric_memory.migration import (
    _validate_copy,
    export_incremental_before_rollback,
    migrate_apply,
    migrate_rollback,
    migration_plan,
    rollback_plan,
    schema_version,
)
from eric_memory.paths import MemoryConfig, load_config, save_config
from eric_memory.service import MemoryService
from eric_memory.store import MemoryStore, utc_now
from tests.test_migration_backup_purge import make_v1_fixture


class MigrationEdgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temporary.name).resolve() / "data"
        self.database = make_v1_fixture(self.data_dir)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_every_legacy_value_is_preserved_without_normalizing_history(self) -> None:
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                "UPDATE facts SET content='  padded legacy fact  ', category='', as_of=' 2026-01-01 ', "
                "created_at='', updated_at='2001-02-03T04:05:06Z' WHERE fact_id=7"
            )
            connection.execute(
                "UPDATE entities SET name='Ｗｉｔｈ', name_key='ｗｉｔｈ', entity_type='', created_at=''"
            )
            original = connection.execute("SELECT * FROM facts ORDER BY fact_id").fetchall()
        result = migrate_apply(self.data_dir, self.database)
        self.assertTrue(result["validation"]["legacy_values_preserved"])
        with sqlite3.connect(self.database) as connection:
            columns = (
                "fact_id,content,category,tags,trust,status,as_of,superseded_by,"
                "source_kind,source_ref,created_at,updated_at"
            )
            self.assertEqual(connection.execute(f"SELECT {columns} FROM facts ORDER BY fact_id").fetchall(), original)

    def test_revalidate_changes_between_preflight_and_lock(self) -> None:
        @contextmanager
        def acquire(path):
            with sqlite3.connect(self.database) as connection:
                connection.execute("UPDATE facts SET superseded_by=9 WHERE fact_id=7")
            with exclusive_file_lock(path):
                yield

        with patch("eric_memory.migration.exclusive_file_lock", acquire), self.assertRaises(ConflictError):
            migrate_apply(self.data_dir, self.database)
        self.assertEqual(schema_version(self.database), 1)

    def test_legacy_sqlite_writer_is_blocked_during_copy(self) -> None:
        from eric_memory.migration import _copy_entities

        def copying(source, target):
            writer = sqlite3.connect(self.database, timeout=0.01)
            try:
                with self.assertRaisesRegex(sqlite3.OperationalError, "locked"):
                    writer.execute("UPDATE facts SET category='concurrent' WHERE fact_id=7")
            finally:
                writer.close()
            _copy_entities(source, target)

        with patch("eric_memory.migration._copy_entities", copying):
            self.assertTrue(migrate_apply(self.data_dir, self.database)["applied"])

    def test_rollback_rechecks_writes_after_preflight(self) -> None:
        migrate_apply(self.data_dir, self.database)

        @contextmanager
        def acquire(path):
            writer = MemoryStore(self.database)
            try:
                writer.add_fact("A concurrent v2 write must survive.")
            finally:
                writer.close()
            with exclusive_file_lock(path):
                yield

        with (
            patch("eric_memory.migration.exclusive_file_lock", acquire),
            self.assertRaises(ConfirmationRequiredError),
        ):
            migrate_rollback(self.data_dir, self.database)
        self.assertEqual(schema_version(self.database), 2)

    def test_folder_harness_overlap_preserves_harness_ownership(self) -> None:
        root = self.data_dir / "legacy-folder"
        with sqlite3.connect(self.database) as connection:
            connection.execute("UPDATE harnesses SET session_root=?, harvest_ok=1 WHERE key='cursor'", (str(root),))
        migrate_apply(self.data_dir, self.database)
        store = MemoryStore(self.database, mode="ro")
        try:
            self.assertEqual(store.list_sources()[0]["harness_key"], "cursor")
        finally:
            store.close()

    def test_rollback_rejects_missing_managed_manifest(self) -> None:
        result = migrate_apply(self.data_dir, self.database)
        Path(result["backup"]["path"] + ".json").unlink()
        with self.assertRaises(IntegrityCheckError):
            migrate_rollback(self.data_dir, self.database)

    def test_rollback_export_keeps_more_than_500_candidates_and_source_state(self) -> None:
        import json

        migrate_apply(self.data_dir, self.database)
        service = MemoryService(self.data_dir)
        try:
            for index in range(501):
                service.candidate_add(
                    content=f"Synthetic migration candidate {index}.",
                    submission_uid=f"migration-export-{index}",
                    manual_input=True,
                )
            output = self.data_dir / "increment.jsonl"
            report = export_incremental_before_rollback(service.store, output)
            self.assertEqual(report["candidates"], 501)
            rows = [json.loads(line) for line in output.read_text().splitlines()]
            self.assertEqual(sum(row["type"] == "candidate" for row in rows), 501)
            self.assertTrue(any(row.get("table") == "sources" for row in rows))
        finally:
            service.close()

    def test_legacy_index_is_not_treated_as_completed_memory_harvest(self) -> None:
        sample = self.data_dir / "legacy-folder" / "session.txt"
        sample.write_text("Previously indexed conversation.\n")
        os.utime(sample, ns=(1_000_000_000, 1_000_000_000))
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                "INSERT INTO files(path,folder,name,sha256,size,mtime,indexed_at) VALUES (?,?,?,?,?,1,?)",
                (
                    str(sample),
                    str(sample.parent),
                    sample.name,
                    hashlib.sha256(sample.read_bytes()).hexdigest(),
                    sample.stat().st_size,
                    utc_now(),
                ),
            )
        migrate_apply(self.data_dir, self.database)
        service = MemoryService(self.data_dir)
        try:
            source_uid = service.source_list()["sources"][0]["source_uid"]
            first = service.harvest_begin(source_uid)
            self.assertEqual([record["path"] for record in first["changed_files"]], [str(sample)])
            service.harvest_complete(first["run_uid"], cursor="processed")
            self.assertEqual(service.harvest_begin(source_uid)["changed_files"], [])
        finally:
            service.close()

    def test_schema_and_plan_reject_missing_or_unsupported_metadata(self) -> None:
        with self.assertRaises(FileNotFoundError):
            schema_version(self.data_dir / "missing.sqlite3")
        invalid = self.data_dir / "invalid.sqlite3"
        sqlite3.connect(invalid).close()
        with self.assertRaises(IntegrityCheckError):
            schema_version(invalid)
        connection = sqlite3.connect(self.database)
        connection.execute("UPDATE schema_meta SET value='0' WHERE key='schema_version'")
        connection.commit()
        connection.close()
        with self.assertRaises(ValidationError):
            migration_plan(self.database)

    def test_plan_reports_normalized_collision_and_supersession_cycle(self) -> None:
        connection = sqlite3.connect(self.database)
        now = utc_now()
        connection.execute(
            """
            INSERT INTO facts(
                fact_id, content, category, tags, trust, status, as_of,
                superseded_by, source_kind, source_ref, created_at, updated_at
            ) VALUES (11, '  LEGACY   ACTIVE PROJECT FACT. ', 'project', 'project:With',
                      0.5, 'active', '2026-01-01', NULL, 'manual', '', ?, ?)
            """,
            (now, now),
        )
        connection.execute("UPDATE facts SET superseded_by=9 WHERE fact_id=7")
        connection.commit()
        connection.close()
        plan = migration_plan(self.database)
        codes = {item["code"] for item in plan["blockers"]}
        self.assertEqual(codes, {"NORMALIZED_ACTIVE_COLLISION", "SUPERSESSION_CYCLE"})
        with self.assertRaises(ConflictError):
            migrate_apply(self.data_dir, self.database)

    def test_rich_fixture_migrates_files_harvest_root_principal_and_config(self) -> None:
        harvest_root = self.data_dir / "harness-root"
        harvest_root.mkdir()
        known_file = self.data_dir / "legacy-folder" / "known.txt"
        known_file.write_text("known", encoding="utf-8")
        unknown_file = self.data_dir / "unknown.txt"
        unknown_file.write_text("unknown", encoding="utf-8")
        connection = sqlite3.connect(self.database)
        now = utc_now()
        connection.execute(
            """
            INSERT INTO harnesses(
                harness_id, key, display_name, session_root, mcp_mounted,
                harvest_ok, notes, created_at, updated_at
            ) VALUES (6, 'hermes', 'Hermes', ?, 1, 1, '', ?, ?)
            """,
            (str(harvest_root), now, now),
        )
        connection.executemany(
            """
            INSERT INTO files(file_id, path, folder, name, sha256, size, mtime, indexed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (3, str(known_file), str(known_file.parent), known_file.name, "a" * 64, 5, 1.0, now),
                (4, str(unknown_file), str(self.data_dir / "not-approved"), unknown_file.name, "b" * 64, 7, 2.0, now),
            ],
        )
        connection.commit()
        connection.close()
        save_config(
            MemoryConfig(
                data_dir=str(self.data_dir),
                vault_dir=str(self.data_dir / "vault"),
                db_path=str(self.database),
                tier="full",
                schema_version=1,
            )
        )
        result = migrate_apply(self.data_dir, self.database)
        self.assertTrue(result["applied"])
        store = MemoryStore(self.database, mode="ro")
        try:
            self.assertEqual(store.connection.execute("SELECT COUNT(*) FROM files").fetchone()[0], 2)
            self.assertEqual(store.connection.execute("SELECT COUNT(*) FROM source_files").fetchone()[0], 1)
            self.assertEqual(store.connection.execute("SELECT COUNT(*) FROM sources").fetchone()[0], 2)
            self.assertIsNotNone(store.principal_info("hermes"))
        finally:
            store.close()
        config = load_config(self.data_dir)
        assert config is not None
        self.assertEqual(config.schema_version, 2)
        self.assertEqual(config.tier, "unified")
        self.assertEqual(migration_plan(self.database)["action"], "none")
        self.assertFalse(migrate_apply(self.data_dir, self.database)["applied"])

    def test_validation_detects_rows_status_ids_and_integrity_mismatch(self) -> None:
        result = migrate_apply(self.data_dir, self.database)
        source = sqlite3.connect(f"{Path(result['backup']['path']).as_uri()}?mode=ro", uri=True)
        source.row_factory = sqlite3.Row
        target = MemoryStore(self.database)
        try:
            target.connection.execute("DELETE FROM audit WHERE audit_id=5")
            with self.assertRaisesRegex(IntegrityCheckError, "row count mismatch"):
                _validate_copy(source, target)
            target.connection.execute(
                "INSERT INTO audit(audit_id, action, fact_id, actor, detail, created_at) "
                "VALUES (5, 'add', 7, 'legacy', '', ?)",
                (utc_now(),),
            )
            target.connection.execute("UPDATE facts SET status='active' WHERE fact_id=9")
            with self.assertRaisesRegex(IntegrityCheckError, "statuses"):
                _validate_copy(source, target)
            target.connection.execute("UPDATE facts SET status='deprecated' WHERE fact_id=9")
            with (
                patch.object(target, "integrity", return_value={"ok": False}),
                self.assertRaisesRegex(IntegrityCheckError, "integrity"),
            ):
                _validate_copy(source, target)
        finally:
            target.close()
            source.close()

    def test_rollback_requires_migration_backup_and_valid_v1_snapshot(self) -> None:
        fresh = self.data_dir / "fresh.sqlite3"
        store = MemoryStore(fresh, mode="create")
        store.close()
        with self.assertRaises(ConflictError):
            rollback_plan(fresh)

        migrate_apply(self.data_dir, self.database)
        manager_result = {
            "ok": False,
            "database": {"schema_version": 1},
        }
        with (
            patch.object(BackupManager, "verify", return_value=manager_result),
            self.assertRaises(IntegrityCheckError),
        ):
            migrate_rollback(self.data_dir, self.database)

    def test_lossy_rollback_requires_incremental_export_path(self) -> None:
        migrate_apply(self.data_dir, self.database)
        store = MemoryStore(self.database)
        try:
            store.add_fact("A post-migration write needs export.")
        finally:
            store.close()
        with self.assertRaises(ValidationError):
            migrate_rollback(self.data_dir, self.database, confirm_loss=True)
