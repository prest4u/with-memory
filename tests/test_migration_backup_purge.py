from __future__ import annotations

import hashlib
import os
import sqlite3
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from eric_memory.errors import ConfirmationRequiredError, ConflictError, IntegrityCheckError, ValidationError
from eric_memory.migration import LEGACY_SCHEMA_V1, migrate_apply, migration_plan
from eric_memory.service import MemoryService
from eric_memory.store import utc_now
from tests.helpers import TempServiceTest


def make_v1_fixture(data_dir: Path) -> Path:
    data_dir.mkdir(parents=True)
    database = data_dir / "memory.db"
    connection = sqlite3.connect(database)
    connection.executescript(LEGACY_SCHEMA_V1)
    now = utc_now()
    connection.executemany(
        """
        INSERT INTO facts(
            fact_id, content, category, tags, trust, status, as_of,
            superseded_by, source_kind, source_ref, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                7,
                "Legacy active project fact.",
                "project",
                "project:With",
                0.9,
                "active",
                "2026-01-01",
                None,
                "manual",
                "",
                now,
                now,
            ),
            (
                9,
                "Legacy deprecated user fact.",
                "general",
                "project:One,workspace:Two",
                0.4,
                "deprecated",
                "2025-01-01",
                7,
                "manual",
                "",
                now,
                now,
            ),
        ],
    )
    connection.execute(
        "INSERT INTO entities(entity_id, name, name_key, entity_type, created_at) "
        "VALUES (3, 'With', 'with', 'project', ?)",
        (now,),
    )
    connection.execute("INSERT INTO fact_entities(fact_id, entity_id) VALUES (7, 3)")
    folder = data_dir / "legacy-folder"
    folder.mkdir()
    connection.execute(
        "INSERT INTO folders(folder_id, path, label, enabled, created_at) VALUES (2, ?, 'legacy', 1, ?)",
        (str(folder.resolve()), now),
    )
    connection.execute(
        """
        INSERT INTO harnesses(
            harness_id, key, display_name, session_root, mcp_mounted,
            harvest_ok, notes, created_at, updated_at
        ) VALUES (4, 'cursor', 'Cursor', '', 1, 0, '', ?, ?)
        """,
        (now, now),
    )
    connection.execute(
        "INSERT INTO audit(audit_id, action, fact_id, actor, detail, created_at) VALUES (5, 'add', 7, 'legacy', '', ?)",
        (now,),
    )
    connection.commit()
    connection.close()
    return database.resolve()


class MigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temporary.name).resolve() / "data"
        self.database = make_v1_fixture(self.data_dir)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_side_by_side_migration_preserves_ids_status_entities_and_scopes(self) -> None:
        plan = migration_plan(self.database)
        self.assertEqual(plan["action"], "migrate")
        self.assertEqual(plan["blockers"], [])
        self.assertEqual(plan["counts"]["facts"], 2)
        result = migrate_apply(self.data_dir, self.database)
        self.assertTrue(result["applied"])
        service = MemoryService(self.data_dir, mode="ro")
        try:
            self.assertEqual(service.store.schema_version, 2)
            self.assertEqual(service.store.integrity()["ok"], True)
            active = service.store.get_fact(7)
            deprecated = service.store.get_fact(9)
            assert active is not None and deprecated is not None
            expected_uid = str(uuid.uuid5(uuid.UUID(result["library_uid"]), "legacy-fact:7"))
            self.assertEqual(active.fact_uid, expected_uid)
            self.assertEqual(active.scope, {"kind": "project", "key": "With"})
            self.assertEqual(active.entities, ["With"])
            self.assertEqual(deprecated.status, "deprecated")
            self.assertEqual(deprecated.superseded_by, 7)
            self.assertEqual(deprecated.scope, {"kind": "user", "key": ""})
            self.assertEqual(
                service.store.connection.execute("SELECT COUNT(*) FROM migration_issues").fetchone()[0],
                1,
            )
        finally:
            service.close()

    def test_failed_conversion_leaves_original_database_unchanged(self) -> None:
        before = hashlib.sha256(self.database.read_bytes()).hexdigest()
        with (
            patch(
                "eric_memory.migration._validate_copy",
                side_effect=IntegrityCheckError("injected validation failure"),
            ),
            self.assertRaises(IntegrityCheckError),
        ):
            migrate_apply(self.data_dir, self.database)
        self.assertEqual(hashlib.sha256(self.database.read_bytes()).hexdigest(), before)
        self.assertEqual(migration_plan(self.database)["from_schema"], 1)

    def test_lossless_then_guarded_lossy_rollback(self) -> None:
        migrate_apply(self.data_dir, self.database)
        service = MemoryService(self.data_dir)
        try:
            self.assertTrue(service.rollback_plan()["lossless"])
            service.status()
            self.assertTrue(service.rollback_plan()["lossless"])
            added = service.add("Post-migration v2 write must be exported before rollback.")
            self.assertGreater(service.rollback_plan()["post_migration_writes"], 0)
            with self.assertRaises(ConfirmationRequiredError):
                service.migrate_rollback()
            export = self.data_dir / "exports" / "increment.jsonl"
            rolled = service.migrate_rollback(
                confirm_loss=True,
                incremental_export=str(export),
            )
            self.assertTrue(rolled["rolled_back"])
            self.assertEqual(service.store.schema_version, 1)
            self.assertIn(str(added["fact"]["fact_uid"]), export.read_text(encoding="utf-8"))
            self.assertEqual(service.store.counts()["facts"], 2)
        finally:
            service.close()


class BackupRestorePurgeTests(TempServiceTest):
    def test_restore_accepts_the_original_v1_release_schema(self) -> None:
        self.service.add("This v2 fact is replaced by the original v1 backup.")
        source = self.data_dir / "original-v1.sqlite3"
        connection = sqlite3.connect(source)
        schema = Path(__file__).parent / "fixtures" / "schema_v1_original.sql"
        connection.executescript(schema.read_text(encoding="utf-8"))
        connection.execute("INSERT INTO schema_meta(key, value) VALUES ('schema_version', '1')")
        connection.commit()
        connection.close()
        result = self.service.restore(str(source))
        self.assertEqual(result["restored"]["database"]["schema_version"], 1)
        self.assertEqual(self.service.store.counts()["facts"], 0)
        self.assertTrue(self.service.store.integrity()["ok"])

    def test_unsupported_restore_leaves_current_database_usable(self) -> None:
        self.service.add("Current library must remain usable.")
        for defect in ("version-0", "version-999", "table", "fts-trigger", "immutable-trigger"):
            version = int(defect.split("-")[1]) if defect.startswith("version-") else 2
            backup = self.service.backup_create()["backup"]
            source = self.data_dir / f"unsupported-{defect}.sqlite3"
            source.write_bytes(Path(backup["path"]).read_bytes())
            connection = sqlite3.connect(source)
            connection.execute("UPDATE schema_meta SET value = ? WHERE key = 'schema_version'", (str(version),))
            if defect == "table":
                connection.execute("DROP TABLE entity_aliases")
            elif defect == "fts-trigger":
                connection.execute("DROP TRIGGER facts_ai")
            elif defect == "immutable-trigger":
                connection.execute("DROP TRIGGER facts_content_immutable")
            connection.commit()
            connection.close()
            before = hashlib.sha256(self.service.store.db_path.read_bytes()).hexdigest()
            with self.subTest(defect=defect), self.assertRaises(ValidationError):
                self.service.restore(str(source))
            self.assertEqual(hashlib.sha256(self.service.store.db_path.read_bytes()).hexdigest(), before)
            self.assertEqual(self.service.store.counts()["facts"], 1)
            self.assertEqual(self.service.store.schema_version, 2)

    def test_restore_rejects_missing_search_postings_before_reset(self) -> None:
        fact = self.service.add("UniqueRestorePosting 中文检索完整性。")["fact"]
        for defect in ("fts", "terms"):
            backup = self.service.backup_create()["backup"]
            source = self.data_dir / f"missing-{defect}.sqlite3"
            source.write_bytes(Path(backup["path"]).read_bytes())
            with sqlite3.connect(source) as connection:
                if defect == "fts":
                    connection.execute(
                        "INSERT INTO facts_fts(facts_fts, rowid, content, tags) VALUES('delete', ?, ?, ?)",
                        (fact["fact_id"], fact["content"], fact["tags"]),
                    )
                else:
                    connection.execute("DELETE FROM fact_search_terms WHERE fact_id = ?", (fact["fact_id"],))
            with self.subTest(defect=defect), self.assertRaises(ValidationError):
                self.service.restore(str(source))
            self.assertEqual(self.service.store.counts()["facts"], 1)
            self.assertEqual(
                self.service.store.connection.execute(
                    "SELECT COUNT(*) FROM facts_fts WHERE facts_fts MATCH 'UniqueRestorePosting'"
                ).fetchone()[0],
                1,
            )

    def test_purge_blocks_interleaved_backups_until_clean_snapshot(self) -> None:
        fact = self.service.add("Synthetic purge canary for concurrent backup.")["fact"]
        writer = MemoryService(self.data_dir)
        original_create = self.service.backups.create
        blocked = []

        def create_with_competing_backup(*args, **kwargs):
            if kwargs.get("kind") in {"pre-purge", "clean-post-purge"}:
                with self.assertRaises(ConflictError):
                    writer.backup_create(kind="automatic")
                blocked.append(kwargs["kind"])
            return original_create(*args, **kwargs)

        try:
            with patch.object(self.service.backups, "create", side_effect=create_with_competing_backup):
                result = self.service.purge(fact["fact_id"], confirmation=fact["fact_uid"])
            self.assertEqual(blocked, ["pre-purge", "clean-post-purge"])
            self.assertTrue(result["clean_backup"]["verified"])
            later = writer.backup_create()["backup"]
            for item in self.service.backups.list():
                connection = sqlite3.connect(item["path"])
                try:
                    self.assertEqual(connection.execute("SELECT COUNT(*) FROM facts").fetchone()[0], 0)
                finally:
                    connection.close()
            self.assertTrue(later["verified"])
        finally:
            writer.close()

    def test_restore_locks_writers_before_recovery_snapshot(self) -> None:
        backup = self.service.backup_create()["backup"]
        writer = MemoryService(self.data_dir)
        writer.add("Committed before the recovery snapshot.")
        original_create = self.service.backups.create

        def create_locked_snapshot(*args, **kwargs):
            snapshot = original_create(*args, **kwargs)
            with self.assertRaises(ConflictError):
                writer.add("A concurrent write must not commit during restore.")
            return snapshot

        try:
            with patch.object(self.service.backups, "create", side_effect=create_locked_snapshot):
                result = self.service.restore(backup["path"])
            recovery = sqlite3.connect(result["safety_snapshot"]["path"])
            try:
                bodies = [row[0] for row in recovery.execute("SELECT content FROM facts")]
                self.assertIn("Committed before the recovery snapshot.", bodies)
                self.assertNotIn("A concurrent write must not commit during restore.", bodies)
            finally:
                recovery.close()
        finally:
            writer.close()

    def test_backup_hash_and_database_integrity_are_verified(self) -> None:
        self.service.add("Backup verification fixture.")
        backup = self.service.backup_create()["backup"]
        self.assertTrue(self.service.backup_verify(backup["path"])["ok"])
        path = Path(backup["path"])
        with path.open("ab") as handle:
            handle.write(b"tamper")
        verified = self.service.backup_verify(str(path))
        self.assertFalse(verified["hash_ok"])
        self.assertFalse(verified["ok"])

    def test_restore_is_atomic_and_repairs_projection(self) -> None:
        kept = self.service.add("This fact is present in the restore point.")["fact"]
        backup = self.service.backup_create()["backup"]
        removed = self.service.add("This later fact must disappear after restore.")["fact"]
        result = self.service.restore(backup["path"])
        self.assertTrue(result["committed"])
        self.assertEqual(result["projection"], "clean")
        self.assertIsNotNone(self.service.store.get_fact(kept["fact_id"]))
        self.assertIsNone(self.service.store.get_fact(removed["fact_id"]))
        vault_text = "\n".join(
            path.read_text(encoding="utf-8") for path in Path(self.service.config.vault_dir).glob("*.md")
        )
        self.assertNotIn("This later fact must disappear after restore.", vault_text)

    def test_purge_removes_database_projection_and_all_managed_backup_copies(self) -> None:
        content = "PURGE-CANARY-2f9b48e6 must leave every With-managed copy."
        fact = self.service.add(content)["fact"]
        containing = self.service.backup_create()["backup"]
        plan = self.service.purge_plan(fact["fact_id"])
        self.assertIn(containing["path"], plan["managed_backups_to_remove"])
        result = self.service.purge(fact["fact_id"], confirmation=fact["fact_uid"])
        self.assertIsNone(self.service.store.get_fact(fact["fact_id"]))
        self.assertTrue(result["integrity"]["ok"])
        self.assertTrue(Path(result["clean_backup"]["path"]).is_file())
        self.assertFalse(Path(containing["path"]).exists())
        tombstone = self.service.store.connection.execute(
            "SELECT * FROM purge_tombstones WHERE fact_uid = ?", (fact["fact_uid"],)
        ).fetchone()
        self.assertIsNotNone(tombstone)
        self.assertNotIn("hash", set(tombstone.keys()))
        needle = content.encode("utf-8")
        for path in self.data_dir.rglob("*"):
            if path.is_file():
                self.assertNotIn(needle, path.read_bytes(), str(path))

    def test_purge_removes_owned_projection_if_regeneration_fails(self) -> None:
        content = "PURGE-PROJECTION-CANARY must not remain in a dirty projection."
        fact = self.service.add(content)["fact"]
        with patch("eric_memory.service.write_vault", side_effect=OSError("injected projection failure")):
            result = self.service.purge(fact["fact_id"], confirmation=fact["fact_uid"])
        self.assertTrue(result["committed"])
        self.assertEqual(result["projection"], "dirty")
        self.assertTrue(result["purge_projection_cleared"])
        for name in ("记忆首页.md", "现行.md", "已过期.md", "资料夹.md", "已接工具.md"):
            self.assertFalse((Path(self.service.config.vault_dir) / name).exists())

    def test_private_filesystem_modes_are_applied_on_posix(self) -> None:
        if os.name == "nt":
            self.skipTest("POSIX mode bits do not apply on Windows")
        self.assertEqual(self.data_dir.resolve().stat().st_mode & 0o777, 0o700)
        self.assertEqual(Path(self.service.store.db_path).stat().st_mode & 0o777, 0o600)
