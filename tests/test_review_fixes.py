from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from unittest.mock import patch

from eric_memory.backup import BackupManager
from eric_memory.context_store import ContextStore
from eric_memory.doctor import doctor_report
from eric_memory.errors import ConflictError, PermissionRequiredError, ValidationError
from eric_memory.service import MemoryService
from tests.helpers import TempServiceTest


class ReviewFixTests(TempServiceTest):
    def test_backup_list_orders_by_created_at_not_filename(self) -> None:
        manager = BackupManager(self.data_dir)
        backup_dir = self.data_dir / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        older = backup_dir / "pre-migration-20260905-aaaa.sqlite3"
        newer = backup_dir / "manual-20260907-bbbb.sqlite3"
        older.write_bytes(b"old")
        newer.write_bytes(b"new")
        (backup_dir / f"{older.name}.json").write_text(
            json.dumps(
                {
                    "filename": older.name,
                    "created_at": "2026-09-05T00:00:00Z",
                    "kind": "pre-migration",
                    "schema_version": 1,
                }
            ),
            encoding="utf-8",
        )
        (backup_dir / f"{newer.name}.json").write_text(
            json.dumps(
                {
                    "filename": newer.name,
                    "created_at": "2026-09-07T16:22:41Z",
                    "kind": "manual",
                    "schema_version": 2,
                }
            ),
            encoding="utf-8",
        )
        listed = manager.list()
        self.assertEqual([item["filename"] for item in listed], [newer.name, older.name])
        self.assertEqual(listed[0]["schema_version"], 2)

    def test_redact_wipes_secret_and_keeps_later_content_immutable(self) -> None:
        secret = "sk-" + ("a" * 40)
        fact, _ = self.service.store.add_fact_with_result(secret)
        other, _ = self.service.store.add_fact_with_result("sk-" + ("b" * 40))
        wiped = self.service.redact(fact.fact_id, reason="review cleanup")
        second = self.service.redact(other.fact_id)
        self.assertNotIn("sk-", wiped["fact"]["content"])
        self.assertIn(f"#{fact.fact_id}", wiped["fact"]["content"])
        self.assertNotEqual(wiped["fact"]["content"], second["fact"]["content"])
        hits = self.service.search(secret[:12], include_deprecated=True)
        self.assertFalse(any(secret in item["content"] for item in hits["facts"]))
        vault = Path(self.service.config.vault_dir)
        self.assertNotIn(secret, (vault / "现行.md").read_text(encoding="utf-8"))
        with self.assertRaises(sqlite3.IntegrityError):
            self.service.store.connection.execute(
                "UPDATE facts SET content = 'changed-after-redact' WHERE fact_id = ?",
                (fact.fact_id,),
            )
        self.service.deprecate(fact.fact_id, reason="credential removed")
        deprecated = (vault / "已过期.md").read_text(encoding="utf-8")
        self.assertNotIn(secret, deprecated)
        self.assertIn("投影不重复已过期正文", deprecated)
        ordinary = self.service.add("An ordinary pointer has no secret.")["fact"]
        with self.assertRaises(ValidationError):
            self.service.redact(ordinary["fact_id"])

    def test_candidate_and_source_writes_refresh_homepage_counts(self) -> None:
        home = Path(self.service.config.vault_dir) / "记忆首页.md"
        before = home.read_text(encoding="utf-8")
        self.assertIn("已点头的资料夹：0", before)
        self.assertIn("待审候选：0", before)
        folder = self.data_dir / "docs"
        folder.mkdir()
        (folder / "brief.md").write_text("body stays out of facts", encoding="utf-8")
        self.service.source_approve(str(folder))
        self.service.index_files(str(folder))
        candidate = self.service.candidate_add(
            content="A short candidate should bump the homepage pending count.",
            submission_uid="review-fix-candidate",
            manual_input=True,
        )
        after_add = home.read_text(encoding="utf-8")
        self.assertIn("已点头的资料夹：1", after_add)
        self.assertIn("待审候选：1", after_add)
        self.assertIn("现行索引文件：1", after_add)
        self.assertIn(str(folder.resolve()), after_add)
        self.service.review_reject(candidate["candidate"]["candidate_uid"], reason="synthetic reject")
        after_reject = home.read_text(encoding="utf-8")
        self.assertIn("待审候选：0", after_reject)

    def test_counts_separate_current_files_from_stale_rows(self) -> None:
        folder = self.data_dir / "docs"
        folder.mkdir()
        sample = folder / "brief.md"
        sample.write_text("index me", encoding="utf-8")
        self.service.source_approve(str(folder))
        self.service.index_files(str(folder))
        self.service.store.connection.execute("UPDATE files SET status = 'stale'")
        self.service.store.connection.commit()
        counts = self.service.store.counts()
        self.assertEqual(counts["files"], 1)
        self.assertEqual(counts["files_current"], 0)
        home = Path(self.service.config.vault_dir) / "记忆首页.md"
        self.service.sync()
        self.assertIn("现行索引文件：0", home.read_text(encoding="utf-8"))

    def test_imported_source_ref_counts_as_a_search_source(self) -> None:
        fact, _ = self.service.store.add_fact_with_result(
            "Imported pointer should expose its holograph locator.",
            source_kind="import",
            source_ref="holograph:153",
        )
        loaded = self.service.store.get_fact(fact.fact_id)
        assert loaded is not None
        self.assertEqual(loaded.source_count, 1)
        hits = self.service.search("holograph locator")
        self.assertEqual(hits["facts"][0]["source_count"], 1)

    def test_harvest_abandon_does_not_advance_cursor_or_ack_files(self) -> None:
        folder = self.data_dir / "sessions"
        folder.mkdir()
        (folder / "session.jsonl").write_text("Synthetic session.\n", encoding="utf-8")
        source_uid = self.service.source_approve(str(folder))["source"]["source_uid"]
        first = self.service.harvest_begin(source_uid)
        self.assertEqual(first["status"], "open")
        abandoned = self.service.harvest_abandon(first["run_uid"])
        self.assertEqual(abandoned["run"]["status"], "error")
        self.assertEqual(abandoned["run"]["error_code"], "HARVEST_ABANDONED")
        self.assertEqual(self.service.store.get_source(source_uid)["last_cursor"], "")
        retry = self.service.harvest_begin(source_uid)
        self.assertEqual(retry["changed_files"], first["changed_files"])
        with self.assertRaises(ConflictError):
            self.service.harvest_abandon(first["run_uid"])
        report = doctor_report(self.data_dir, self.service.store, self.service.config)
        latest = next(item for item in report["index"]["scans"] if item["source_uid"] == source_uid)
        self.assertEqual(latest["run_uid"], retry["run_uid"])

    def test_ack_scope_issues_clears_doctor_warning(self) -> None:
        fact = self.service.add("Imported pointer in user scope.")["fact"]
        self.service.store.connection.execute(
            "INSERT INTO migration_issues(issue_code, object_type, object_id, detail, created_at) "
            "VALUES ('AMBIGUOUS_SCOPE_TAG', 'fact', ?, '[]', '2026-09-08T00:00:00Z')",
            (str(fact["fact_id"]),),
        )
        self.service.store.connection.commit()
        before = doctor_report(self.data_dir, self.service.store, self.service.config)
        self.assertIn("ambiguous_scope_tags", before["warnings"])
        acked = self.service.ack_scope_issues()
        self.assertEqual(acked["acked"], 1)
        after = doctor_report(self.data_dir, self.service.store, self.service.config)
        self.assertNotIn("ambiguous_scope_tags", after["warnings"])
        self.assertEqual(after["migration_scope_issues"], 0)

    def test_resolve_scope_confirms_current_scope_only(self) -> None:
        fact = self.service.add("A user-scoped import leftover.")["fact"]
        self.service.store.connection.execute(
            "INSERT INTO migration_issues(issue_code, object_type, object_id, detail, created_at) "
            "VALUES ('AMBIGUOUS_SCOPE_TAG', 'fact', ?, '[]', '2026-09-08T00:00:00Z')",
            (str(fact["fact_id"]),),
        )
        self.service.store.connection.commit()
        resolved = self.service.resolve_scope_issue(fact["fact_id"], scope="user")
        self.assertEqual(resolved["issues_cleared"], 1)
        with self.assertRaises(ValidationError):
            self.service.resolve_scope_issue(fact["fact_id"], scope="project", project="青云")

    def test_entity_cleanup_dry_run_lists_import_noise_only(self) -> None:
        self.service.add("Pointer about a PDF and Documents folder.", entities=["PDF", "Documents", "With."])
        plan = self.service.entity_cleanup_plan()
        names = {item["name"] for item in plan["proposals"]}
        self.assertTrue(plan["dry_run"])
        self.assertEqual(plan["applied"], 0)
        self.assertTrue({"PDF", "Documents"} <= names)
        self.assertNotIn("With.", names)

    def test_same_second_backup_order_uses_subsecond_creation_stamp(self) -> None:
        with patch("eric_memory.backup.utc_now", return_value="2026-09-08T00:00:00Z"):
            older = self.service.backup_create(kind="z-old")["backup"]
            newer = self.service.backup_create(kind="a-new")["backup"]
        items = self.service.backup_list()["backups"]
        self.assertEqual(items[0]["path"], newer["path"])
        self.assertNotEqual(items[0]["path"], older["path"])

    def test_redact_refreshes_legacy_v2_fts_and_remains_backup_verifiable(self) -> None:
        # The released v2 trigger only fires when tags is part of the UPDATE.
        conn = self.service.store.connection
        trigger = conn.execute("SELECT sql FROM sqlite_master WHERE name = 'facts_au'").fetchone()[0]
        conn.execute("DROP TRIGGER facts_au")
        conn.execute(trigger.replace("UPDATE OF content, tags", "UPDATE OF tags"))
        secret = "sk-" + ("d" * 40)
        fact, _ = self.service.store.add_fact_with_result(secret)
        self.service.redact(fact.fact_id)
        old_hits = conn.execute("SELECT rowid FROM facts_fts WHERE facts_fts MATCH ?", ('"' + secret + '"',)).fetchall()
        self.assertEqual(old_hits, [])
        new_hits = conn.execute("SELECT rowid FROM facts_fts WHERE facts_fts MATCH 'Redacted'").fetchall()
        self.assertIn(fact.fact_id, [row[0] for row in new_hits])
        conn.execute("INSERT INTO facts_fts(facts_fts, rank) VALUES('integrity-check', 1)")
        backup = self.service.backup_create()["backup"]
        self.assertTrue(self.service.backups.verify(backup["path"])["ok"])

    def test_redact_rollback_restores_content_and_immutable_trigger(self) -> None:
        secret = "sk-" + ("e" * 40)
        fact, _ = self.service.store.add_fact_with_result(secret)
        with (
            patch.object(self.service.store, "_audit", side_effect=RuntimeError("synthetic failure")),
            self.assertRaises(RuntimeError),
        ):
            self.service.redact(fact.fact_id)
        restored = self.service.store.get_fact(fact.fact_id)
        assert restored is not None
        self.assertEqual(restored.content, secret)
        with self.assertRaises(sqlite3.IntegrityError):
            self.service.store.connection.execute(
                "UPDATE facts SET content = 'changed' WHERE fact_id = ?", (fact.fact_id,)
            )
        self.service.store.connection.execute("INSERT INTO facts_fts(facts_fts, rank) VALUES('integrity-check', 1)")

    def test_harness_cannot_abandon_another_source_or_redact(self) -> None:
        folder = self.data_dir / "owned"
        folder.mkdir()
        (folder / "one.md").write_text("Synthetic source.", encoding="utf-8")
        self.service.harness_add("owner")
        self.service.harness_add("other")
        source = self.service.source_approve(str(folder), harness_key="owner")["source"]
        owner = MemoryService(self.data_dir, principal="owner")
        other = MemoryService(self.data_dir, principal="other")
        self.addCleanup(owner.close)
        self.addCleanup(other.close)
        run = owner.harvest_begin(source["source_uid"])
        with self.assertRaises(PermissionRequiredError):
            other.harvest_abandon(run["run_uid"])
        self.assertEqual(self.service.store.scan_run(run["run_uid"])["status"], "open")
        self.assertEqual(owner.harvest_abandon(run["run_uid"])["run"]["status"], "error")
        fact, _ = self.service.store.add_fact_with_result("sk-" + ("f" * 40))
        with self.assertRaises(PermissionRequiredError):
            owner.redact(fact.fact_id)

    def test_scope_ack_preserves_unrelated_and_unconfirmed_issues(self) -> None:
        user = self.service.add("User-scoped import.")["fact"]
        project = self.service.add("Project-scoped import.", scope="project", project="demo")["fact"]
        conn = self.service.store.connection
        conn.executemany(
            "INSERT INTO migration_issues(issue_code, object_type, object_id, detail, created_at) "
            "VALUES (?, 'fact', ?, '[]', '2026-09-08T00:00:00Z')",
            [
                ("AMBIGUOUS_SCOPE_TAG", str(user["fact_id"])),
                ("OTHER_ISSUE", str(user["fact_id"])),
                ("AMBIGUOUS_SCOPE_TAG", str(project["fact_id"])),
                ("AMBIGUOUS_SCOPE_TAG", "999999"),
            ],
        )
        self.assertEqual(self.service.ack_scope_issues()["acked"], 1)
        self.assertEqual(self.service.resolve_scope_issue(user["fact_id"])["issues_cleared"], 0)
        remaining = {tuple(row) for row in conn.execute("SELECT issue_code, object_id FROM migration_issues")}
        self.assertEqual(
            remaining,
            {
                ("OTHER_ISSUE", str(user["fact_id"])),
                ("AMBIGUOUS_SCOPE_TAG", str(project["fact_id"])),
                ("AMBIGUOUS_SCOPE_TAG", "999999"),
            },
        )

    def test_doctor_distinguishes_scope_warnings_from_other_migration_issues(self) -> None:
        conn = self.service.store.connection
        conn.execute(
            "INSERT INTO migration_issues(issue_code, object_type, object_id, detail, created_at) "
            "VALUES ('OTHER_ISSUE', 'fact', '9', '[]', '2026-09-08T00:00:00Z')"
        )
        report = doctor_report(self.data_dir, self.service.store, self.service.config)
        self.assertEqual(report["migration_scope_issues"], 0)
        self.assertNotIn("ambiguous_scope_tags", report["warnings"])
        self.assertEqual(report["migration_issues"][0]["issue_code"], "OTHER_ISSUE")
        self.assertEqual(self.service.ack_scope_issues()["acked"], 0)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM migration_issues").fetchone()[0], 1)

    def test_source_revoke_preserves_cleanup_and_projection_failures(self) -> None:
        folder = self.data_dir / "revoke-source"
        folder.mkdir()
        source = self.service.source_approve(str(folder))["source"]
        with (
            patch.object(ContextStore, "forget_source", side_effect=OSError("synthetic cleanup failure")),
            patch("eric_memory.service.write_vault", side_effect=OSError("synthetic projection failure")),
        ):
            result = self.service.source_revoke(source["source_uid"])
        self.assertTrue(result["committed"])
        self.assertEqual(result["source"]["status"], "revoked")
        self.assertEqual(result["context_cleanup"], "pending")
        self.assertEqual(result["warning"]["code"], "CONTEXT_CLEANUP_PENDING")
        self.assertEqual(result["projection"], "dirty")
        self.assertEqual(result["projection_warning"]["code"], "PROJECTION_DIRTY")
