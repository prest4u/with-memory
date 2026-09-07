from __future__ import annotations

import hashlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from eric_memory.errors import ConflictError, DatabaseNotFoundError, ValidationError
from eric_memory.service import MemoryService
from tests.helpers import TempServiceTest


class ConnectionModeTests(unittest.TestCase):
    def test_non_init_commands_do_not_create_a_database_or_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            missing = Path(temporary) / "not-created"
            for mode in ("ro", "rw-existing"):
                with self.assertRaises(DatabaseNotFoundError):
                    MemoryService(missing, mode=mode)  # type: ignore[arg-type]
                self.assertFalse(missing.exists())

    def test_read_only_status_search_and_doctor_do_not_change_database_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            data = Path(temporary) / "data"
            creator = MemoryService.for_init(data)
            creator.init(data_dir=data, obsidian_enabled=False)
            creator.add("Read-only checks must not mutate SQLite.", entities=["readonly"])
            creator.close()
            db = data.resolve() / "memory.db"
            before_stat = (db.stat().st_size, db.stat().st_mtime_ns)
            before_hash = hashlib.sha256(db.read_bytes()).hexdigest()
            reader = MemoryService(data, mode="ro")
            try:
                reader.status()
                reader.search("readonly", include_files=False)
                report = reader.doctor()
                self.assertEqual(report["update_verification"], {"ok": True, "algorithm": "Ed25519"})
            finally:
                reader.close()
            self.assertEqual((db.stat().st_size, db.stat().st_mtime_ns), before_stat)
            self.assertEqual(hashlib.sha256(db.read_bytes()).hexdigest(), before_hash)


class SafetyBridgeTests(TempServiceTest):
    def test_invalid_supersede_rolls_back_new_fact(self) -> None:
        before = self.service.store.counts()["facts"]
        with self.assertRaises(KeyError):
            self.service.add("This insertion must roll back atomically.", supersedes=999_999)
        self.assertEqual(self.service.store.counts()["facts"], before)

    def test_supersession_cycle_and_non_active_predecessor_are_rejected(self) -> None:
        first = self.service.add("First current pointer.")["fact"]["fact_id"]
        second = self.service.add("Second current pointer.")["fact"]["fact_id"]
        self.service.deprecate(first, superseded_by=second)
        with self.assertRaises(ConflictError):
            self.service.deprecate(second, superseded_by=first)
        self.assertEqual(self.service.store.get_fact(second).status, "active")

    def test_query_limits_and_sql_wildcards_are_literal(self) -> None:
        literal = self.service.add("A literal %_ marker is searchable.")["fact"]["fact_id"]
        self.service.add("An unrelated ordinary marker is present.")
        found = self.service.search("%_", include_files=False)
        self.assertEqual([item["fact_id"] for item in found["facts"]], [literal])
        for limit in (-1, 0, 101):
            with self.assertRaises(ValidationError):
                self.service.search("marker", limit=limit, include_files=False)

    def test_symlinks_are_skipped_and_a_symlink_root_is_rejected(self) -> None:
        if not hasattr(os, "symlink"):
            self.skipTest("symbolic links are unavailable")
        root = self.data_dir / "approved"
        outside = self.data_dir / "outside"
        root.mkdir()
        outside.mkdir()
        (root / "inside.txt").write_text("inside", encoding="utf-8")
        (outside / "secret.txt").write_text("outside", encoding="utf-8")
        try:
            (root / "escape.txt").symlink_to(outside / "secret.txt")
            (root / "escape-dir").symlink_to(outside, target_is_directory=True)
            root_alias = self.data_dir / "approved-alias"
            root_alias.symlink_to(root, target_is_directory=True)
        except OSError as exc:
            self.skipTest(f"symbolic link creation is unavailable: {exc}")
        source = self.service.source_approve(str(root))["source"]
        report = self.service.source_scan(source["source_uid"])
        self.assertEqual(report["status"], "complete")
        indexed = {Path(item["path"]).name for item in self.service.store.list_files()}
        self.assertEqual(indexed, {"inside.txt"})
        with self.assertRaises(ValidationError):
            self.service.source_approve(str(root_alias))

    def test_deleted_and_missing_files_become_stale_immediately(self) -> None:
        root = self.data_dir / "stale-root"
        root.mkdir()
        note = root / "note.txt"
        note.write_text("current", encoding="utf-8")
        source = self.service.source_approve(str(root))["source"]
        self.service.source_scan(source["source_uid"])
        note.unlink()
        second = self.service.source_scan(source["source_uid"])
        self.assertEqual(second["stats"]["stale"], 1)
        self.assertEqual(self.service.store.search_files("note"), [])
        root.rmdir()
        missing = self.service.source_scan(source["source_uid"])
        self.assertEqual(missing["error"]["code"], "SOURCE_MISSING")

    def test_truncated_scan_is_an_error_and_cannot_advance_cursor(self) -> None:
        root = self.data_dir / "large-root"
        root.mkdir()
        for name in ("one.txt", "two.txt"):
            (root / name).write_text(name, encoding="utf-8")
        source = self.service.source_approve(str(root))["source"]
        result = self.service.harvest_begin(source["source_uid"], max_files=1)
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error"]["code"], "SCAN_TRUNCATED")
        self.assertEqual(self.service.store.scan_run(result["run_uid"])["status"], "error")
        with self.assertRaises(ConflictError):
            self.service.harvest_complete(result["run_uid"], cursor="must-not-commit")
        self.assertEqual(self.service.store.get_source(source["source_uid"])["last_cursor"], "")

    def test_large_unchanged_and_stale_file_sets_are_chunked(self) -> None:
        folder = self.data_dir / "large-source"
        folder.mkdir()
        source = self.service.source_approve(str(folder))["source"]
        run_uid = self.service.store.start_scan(source["source_uid"], "local")
        records = [
            {
                "path": str(folder / f"file-{index:04d}.txt"),
                "name": f"file-{index:04d}.txt",
                "sha256": f"{index:064x}"[-64:],
                "size": 0,
                "mtime_ns": index + 1,
            }
            for index in range(1605)
        ]
        self.service.store.apply_scan(
            run_uid,
            changed=records,
            unchanged_paths=[],
            seen_paths={item["path"] for item in records},
            truncated=False,
        )
        second = self.service.store.start_scan(source["source_uid"], "local")
        kept = [item["path"] for item in records[:1201]]
        result = self.service.store.apply_scan(
            second,
            changed=[],
            unchanged_paths=kept,
            seen_paths=set(kept),
            truncated=False,
        )
        self.assertEqual(result["unchanged"], 1201)
        self.assertEqual(result["stale"], 404)

    def test_projection_failure_reports_committed_dirty(self) -> None:
        with patch("eric_memory.service.write_vault", side_effect=OSError("read-only vault")):
            result = self.service.add("SQLite remains authoritative on projection failure.")
        self.assertTrue(result["committed"])
        self.assertEqual(result["projection"], "dirty")
        self.assertIsNotNone(self.service.store.get_fact(result["fact"]["fact_id"]))

    def test_well_formed_scope_tag_is_structured_when_scope_is_omitted(self) -> None:
        fact = self.service.add("Tag migration produces a structured scope.", tags="project:With")["fact"]
        self.assertEqual(fact["scope"], {"kind": "project", "key": "With"})
