from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from eric_memory.context_service import read_source
from eric_memory.context_store import ContextStore, chunk_text, encoded_size
from eric_memory.errors import (
    ConflictError,
    ContentRejectedError,
    IntegrityCheckError,
    NotFoundError,
    PermissionRequiredError,
    ValidationError,
)
from eric_memory.locking import exclusive_file_lock
from eric_memory.mcp_server import dispatch_tool, visible_tools
from eric_memory.permissions import CONTEXT_READ, CONTEXT_WRITE, FACT_SEARCH
from eric_memory.scopes import ScopeSpec
from eric_memory.service import MemoryService
from tests.helpers import ROOT, TempServiceTest


class ContextTests(TempServiceTest):
    def setUp(self) -> None:
        super().setUp()
        self.root = (self.data_dir.parent / "Project with spaces 中文").resolve()
        self.root.mkdir()
        self.project = "demo"
        self.service.harness_add("codex", display_name="Codex")
        self.source = self.service.source_approve(str(self.root), harness_key="codex")["source"]
        self.service.context.enable(self.project, self.source["source_uid"], allow_content_storage=True)
        for cap in (CONTEXT_READ, CONTEXT_WRITE, FACT_SEARCH):
            self.service.store.grant("codex", cap, scope=ScopeSpec("project", self.project))
        self.client = MemoryService(self.data_dir, principal="codex", mode="ro")

    def tearDown(self) -> None:
        self.client.close()
        super().tearDown()

    def capture(self, content: str, name: str = "output.md") -> dict:
        (self.root / name).write_bytes(content.encode("utf-8"))
        return self.client.context.index(self.project, self.source["source_uid"], name)

    def test_exact_read_preserves_lf_crlf_and_cr_source_bytes(self) -> None:
        for index, newline in enumerate(("\n", "\r\n", "\r")):
            content = newline.join(("# 中文", "Orchid", ""))
            with self.subTest(newline=repr(newline)):
                receipt = self.capture(content, f"newlines-{index}.md")
                result = self.client.context.read(self.project, receipt["artifact_uid"])
                self.assertEqual("".join(item["content"] for item in result["results"]), content)
                self.assertEqual(receipt["sha256"], hashlib.sha256(content.encode("utf-8")).hexdigest())

    def test_exact_english_chinese_and_fact_recall_with_byte_budget(self) -> None:
        self.service.add("Launch region is Oregon.", scope="project", project=self.project)
        content = "# Launch\nUse region Oregon for launch.\n\n# 中文资料\n学生版不包含答案。\n"
        receipt = self.capture(content)
        self.assertEqual(receipt["sha256"], hashlib.sha256(content.encode()).hexdigest())
        result = self.client.context.recall("Launch", self.project, max_bytes=2048)
        self.assertEqual({r["kind"] for r in result["results"]}, {"fact", "context"})
        self.assertEqual(result["returned_bytes"], encoded_size(result))
        self.assertLessEqual(encoded_size(result), 2048)
        chinese = self.client.context.recall("学生版 答案", self.project)
        self.assertIn("学生版不包含答案", json.dumps(chinese, ensure_ascii=False))
        exact = self.client.context.read(self.project, receipt["artifact_uid"])
        self.assertEqual("".join(r["content"] for r in exact["results"]), content)
        self.assertIsNone(exact["next_line"])

    def test_capture_uses_retention_setting_current_at_commit(self) -> None:
        def shorten_retention(source, locator):
            content = read_source(source, locator)
            self.service.context.enable(self.project, self.source["source_uid"], ttl_days=1, allow_content_storage=True)
            return content

        with patch("eric_memory.context_service.read_source", side_effect=shorten_retention):
            receipt = self.capture("# Orchid\nRetention changed during the file read.\n")
        with self.client.context.store.connect() as db:
            row = db.execute(
                "SELECT created_at, expires_at FROM artifacts WHERE artifact_uid=?", (receipt["artifact_uid"],)
            ).fetchone()
        self.assertEqual(row[1] - row[0], 86400)

    def test_existing_memory_api_is_unchanged_and_context_does_not_create_facts(self) -> None:
        before = self.service.store.counts()["facts"]
        receipt = self.capture("# Orchid\nORCHID launch uses Oregon.\n")
        self.assertEqual(self.service.store.counts()["facts"], before)
        old = dispatch_tool(self.client, "memory_search", {"query": "ORCHID"})
        self.assertFalse(old["isError"])
        self.assertEqual(old["structuredContent"]["facts"], [])
        with self.service.context.store.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0], 1)
        self.assertNotIn("ORCHID", json.dumps(receipt))

    def test_default_legacy_and_ungranted_principals_cannot_discover_or_call(self) -> None:
        self.service.harness_add("other", display_name="Other")
        for principal in ("legacy", "other"):
            with self.subTest(principal=principal):
                client = MemoryService(self.data_dir, principal=principal, mode="ro")
                try:
                    names = {t["name"] for t in visible_tools(client)}
                    self.assertNotIn("memory_context_index", names)
                    self.assertNotIn("memory_recall", names)
                    result = dispatch_tool(client, "memory_recall", {"project": self.project, "query": "launch"})
                    self.assertTrue(result["isError"])
                    self.assertEqual(result["structuredContent"]["error"]["code"], "PERMISSION_REQUIRED")
                finally:
                    client.close()

    def test_project_isolation_and_explicit_cross_harness_read(self) -> None:
        receipt = self.capture("# Orchid\nOrchid uses Oregon.\n")
        self.service.harness_add("cursor", display_name="Cursor")
        cursor = MemoryService(self.data_dir, principal="cursor", mode="ro")
        try:
            with self.assertRaises(PermissionRequiredError):
                cursor.context.read(self.project, receipt["artifact_uid"])
            self.service.store.grant("cursor", CONTEXT_READ, scope=ScopeSpec("project", self.project))
            self.assertTrue(cursor.context.read(self.project, receipt["artifact_uid"])["results"])
            shared = cursor.context.recall("Orchid", self.project)
            self.assertTrue(shared["results"])
            self.assertFalse(shared["facts_access"])
            with self.assertRaises(PermissionRequiredError):
                cursor.context.read("elsewhere", receipt["artifact_uid"])
            self.service.store.grant("cursor", CONTEXT_WRITE, scope=ScopeSpec("project", self.project))
            with self.assertRaises(PermissionRequiredError):
                cursor.context.index(self.project, self.source["source_uid"], "output.md")
            self.service.store.revoke_grant("cursor", CONTEXT_READ, scope=ScopeSpec("project", self.project))
            with self.assertRaises(PermissionRequiredError):
                cursor.context.read(self.project, receipt["artifact_uid"])
        finally:
            cursor.close()

    def test_project_casefold_matches_existing_scope_semantics(self) -> None:
        receipt = self.capture("# Launch\nLaunch successful.\n")
        self.assertEqual(self.client.context.read(" DEMO ", receipt["artifact_uid"])["project"], "demo")

    def test_source_revocation_and_new_consent_never_resurrect_old_documents(self) -> None:
        receipt = self.capture("# Orchid\nOld launch value.\n")
        self.service.source_revoke(self.source["source_uid"])
        with self.assertRaises(NotFoundError):
            self.client.context.read(self.project, receipt["artifact_uid"])
        self.service.source_approve(str(self.root), harness_key="codex")
        with self.assertRaises(PermissionRequiredError):
            self.client.context.index(self.project, self.source["source_uid"], "output.md")
        self.assertFalse(self.client.context.recall("Orchid", self.project)["results"])

    def test_reapproval_without_revoke_invalidates_snapshot(self) -> None:
        receipt = self.capture("# Orchid\nOld launch value.\n")
        self.service.source_approve(str(self.root), harness_key="codex", exclude=["output.md"])
        with self.assertRaises(NotFoundError):
            self.client.context.read(self.project, receipt["artifact_uid"])
        self.assertFalse(self.client.context.recall("Orchid", self.project)["results"])

    def test_restore_cannot_revive_old_source_consent_or_cached_body(self) -> None:
        content = "# Orchid\nRESTORE-CONTEXT-CANARY old launch value.\n"
        receipt = self.capture(content)
        backup = self.service.backup_create()["backup"]
        self.service.source_approve(str(self.root), harness_key="codex")
        with self.assertRaises(NotFoundError):
            self.client.context.read(self.project, receipt["artifact_uid"])
        self.client.close()
        restored = self.service.restore(backup["path"])
        self.assertEqual(restored["context_reset"]["removed_files"], 1)
        self.client = MemoryService(self.data_dir, principal="codex", mode="ro")
        with self.assertRaises(NotFoundError):
            self.client.context.read(self.project, receipt["artifact_uid"])
        self.assertFalse(self.client.context.status(self.project)["enabled"])
        self.assertFalse(self.service.context.store.path.exists())
        with self.assertRaises(PermissionRequiredError):
            self.client.context.index(self.project, self.source["source_uid"], "output.md")

    def test_failed_context_reset_prevents_main_database_restore(self) -> None:
        backup = self.service.backup_create()["backup"]
        fact = self.service.add("New fact stays when restore cannot clear the cache.")["fact"]
        with patch.object(ContextStore, "reset", side_effect=ConflictError("busy")), self.assertRaises(ConflictError):
            self.service.restore(backup["path"])
        self.assertIsNotNone(self.service.store.get_fact(fact["fact_id"]))

    def test_unsupported_restore_preserves_enabled_context_and_body(self) -> None:
        content = "# Orchid\nContext remains available after rejected restore.\n"
        receipt = self.capture(content)
        source = self.data_dir / "unsupported.sqlite3"
        connection = sqlite3.connect(source)
        self.service.store.connection.backup(connection)
        connection.execute("UPDATE schema_meta SET value = '999' WHERE key = 'schema_version'")
        connection.commit()
        connection.close()
        with self.assertRaises(ValidationError):
            self.service.restore(str(source))
        result = self.client.context.read(self.project, receipt["artifact_uid"])
        self.assertEqual("".join(row["content"] for row in result["results"]), content)

    def test_restore_source_changed_after_verification_preserves_context(self) -> None:
        content = "# Orchid\nCaptured context survives a changed restore source.\n"
        receipt = self.capture(content)
        source = Path(self.service.backup_create()["backup"]["path"])
        original_create = self.service.backups.create

        def change_source(*args, **kwargs):
            result = original_create(*args, **kwargs)
            if kwargs.get("kind") == "pre-restore":
                with closing(sqlite3.connect(source)) as connection, connection:
                    connection.execute("DROP TRIGGER facts_ai")
            return result

        with (
            patch.object(self.service.backups, "create", side_effect=change_source),
            self.assertRaises(ValidationError),
        ):
            self.service.restore(str(source))
        result = self.client.context.read(self.project, receipt["artifact_uid"])
        self.assertEqual("".join(row["content"] for row in result["results"]), content)
        self.assertTrue(self.service.store.integrity()["ok"])

    def test_revocation_reports_committed_state_when_cleanup_fails(self) -> None:
        receipt = self.capture("# Orchid\nSafe revocation test.\n")
        with patch.object(ContextStore, "forget_source", side_effect=IntegrityCheckError("damaged")):
            result = self.service.source_revoke(self.source["source_uid"])
        self.assertTrue(result["committed"])
        self.assertEqual(result["source"]["status"], "revoked")
        self.assertEqual(result["context_cleanup"], "pending")
        self.assertEqual(result["warning"]["code"], "CONTEXT_CLEANUP_PENDING")
        with self.assertRaises(NotFoundError):
            self.client.context.read(self.project, receipt["artifact_uid"])
        self.assertEqual(self.service.source_revoke(self.source["source_uid"])["context_cleanup"], "complete")

    def test_expiry_survives_restart_and_read_does_not_extend_it(self) -> None:
        receipt = self.capture("# Orchid\nTemporary launch value.\n")
        self.client.close()
        self.client = MemoryService(self.data_dir, principal="codex", mode="ro")
        self.assertEqual(
            self.client.context.read(self.project, receipt["artifact_uid"])["expires_at"], receipt["expires_at"]
        )
        with patch("eric_memory.context_store.time.time", return_value=receipt["expires_at"] + 1):
            self.assertFalse(self.client.context.recall("Orchid", self.project)["results"])
            with self.assertRaises(NotFoundError):
                self.client.context.read(self.project, receipt["artifact_uid"])
            self.assertEqual(self.service.context.clean()["removed"], 1)

    def test_reindex_invalidates_old_id_and_rolls_back_on_quota_failure(self) -> None:
        first = self.capture("# Orchid\nValue one.\n")
        second = self.capture("# Orchid\nValue two.\n")
        with self.assertRaises(NotFoundError):
            self.client.context.read(self.project, first["artifact_uid"])
        with patch("eric_memory.context_store.MAX_PROJECT_BYTES", 1), self.assertRaises(ValidationError):
            self.capture("# Orchid\nValue three.\n")
        self.assertIn("Value two", json.dumps(self.client.context.read(self.project, second["artifact_uid"])))

    def test_secret_minor_and_transcript_rejection_does_not_persist_originals(self) -> None:
        examples = [
            "password=" + "prohibitedvalue",
            "Student Alex exam score is 91.",
            '{"role":"user","content":"Full transcript should not be captured"}',
            "User: Secret conversation one.\nAssistant: Conversation answer.\n" * 20,
            "Human: First turn.\nAI: Second turn.\n" * 20,
            "用户：第一句话。\n助手：第二句话。\n" * 20,
            "### User\nA question.\n### Assistant\nAn answer.\n",
            "**User:** Question.\n**Assistant:** Answer.\n",
            '<message role="user">Question.</message>\n<message role="assistant">Answer.</message>',
            "<user>Question.</user><assistant>Answer.</assistant>",
            "- role: user\n  content: Question.\n- role: assistant\n  content: Answer.\n",
            json.dumps({"export": json.dumps({"role": "user", "content": "nested conversation"})}),
        ]
        for content in examples:
            with self.subTest(content=content), self.assertRaises(ContentRejectedError):
                self.capture(content)
        with self.service.context.store.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0], 0)
        for content in examples:
            self.assertNotIn(content.encode(), self.service.context.store.path.read_bytes())

    def test_empty_source_selector_cannot_disable_an_entire_project(self) -> None:
        receipt = self.capture("# Orchid\nSafe project content.\n")
        for selector in ("", " ", "not-a-uuid"):
            with self.assertRaises(ValidationError):
                self.service.context.disable(self.project, selector)
        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "bin/eric-memory"),
                "--data-dir",
                str(self.data_dir),
                "context",
                "disable",
                "--project",
                self.project,
                "--source-uid",
                "",
                "--confirm",
            ],
            capture_output=True,
            encoding="utf-8",
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(self.client.context.read(self.project, receipt["artifact_uid"])["results"])

    def test_local_reset_recovers_corruption_and_wrong_library_without_touching_facts(self) -> None:
        fact = self.service.add("Durable content remains after cache reset.")["fact"]
        path = self.service.context.store.path
        path.write_bytes(b"Broken cache contains only synthetic temporary prose.")
        with self.assertRaises(IntegrityCheckError):
            self.client.context.status(self.project)
        with self.assertRaises(PermissionRequiredError):
            self.client.context.reset()
        self.assertEqual(self.service.context.reset()["removed_files"], 1)
        foreign = ContextStore(self.data_dir, "another-library")
        foreign.enable(self.project, self.source["source_uid"], self.source["consent_uid"], 7)
        with self.assertRaises(IntegrityCheckError):
            self.service.context.enable(self.project, self.source["source_uid"], allow_content_storage=True)
        for suffix in ("-journal", "-wal", "-shm"):
            path.with_name(path.name + suffix).write_text("Synthetic sidecar")
        self.assertEqual(self.service.context.reset()["removed_files"], 4)
        self.service.context.enable(self.project, self.source["source_uid"], allow_content_storage=True)
        self.assertTrue(self.capture("# Recovered\nFresh temporary document.\n"))
        self.assertIsNotNone(self.service.store.get_fact(fact["fact_id"]))

    def test_reset_refuses_linked_storage_and_waits_for_an_open_reader(self) -> None:
        self.capture("# Orchid\nConcurrent reset sample.\n")
        store = self.service.context.store
        with ThreadPoolExecutor(max_workers=1) as executor:
            with store.connect():
                future = executor.submit(store.reset)
                time.sleep(0.12)
                self.assertFalse(future.done())
            self.assertEqual(future.result()["removed_files"], 1)
        outside = self.root / "untouched.txt"
        outside.write_text("Outside file stays untouched.")
        try:
            store.path.symlink_to(outside)
        except OSError:
            return
        with self.assertRaises(ValidationError):
            self.service.context.reset()
        self.assertEqual(outside.read_text(), "Outside file stays untouched.")

    def test_windows_lock_failure_does_not_unlock_an_unowned_lock_or_grow_its_file(self) -> None:
        lock_path = self.data_dir / "windows-lock-test"
        calls: list[int] = []

        def locking(fd: int, operation: int, length: int) -> None:
            calls.append(operation)
            if operation == 1 and len(calls) == 1:
                raise OSError("synthetic contention")

        api = SimpleNamespace(locking=locking, LK_NBLCK=1, LK_UNLCK=2)
        with patch("eric_memory.locking.os.name", "nt"), patch.dict(sys.modules, {"msvcrt": api}):
            with self.assertRaises(ConflictError), exclusive_file_lock(lock_path):
                self.fail("lock was not acquired")
            self.assertEqual(calls, [1])
            for _ in range(2):
                with exclusive_file_lock(lock_path):
                    pass
        self.assertEqual(calls, [1, 1, 2, 1, 2])
        self.assertEqual(lock_path.read_bytes(), b"0")

    def test_traversal_hidden_files_source_filters_and_hash_mismatch(self) -> None:
        self.capture("# Orchid\nLaunch.\n")
        for locator in ("../output.md", "/etc/passwd", "C:/Windows/file", ".secret", "folder/../output.md", "a\\b"):
            with self.subTest(locator=locator), self.assertRaises((ValidationError, ContentRejectedError)):
                self.client.context.index(self.project, self.source["source_uid"], locator)
        with self.assertRaises(ValidationError):
            self.client.context.index(self.project, self.source["source_uid"], "output.md", expected_sha256="0" * 64)
        source = self.service.source_approve(str(self.root), harness_key="codex", file_types=["log"])["source"]
        self.service.context.enable(self.project, source["source_uid"], allow_content_storage=True)
        with self.assertRaises(PermissionRequiredError):
            self.client.context.index(self.project, source["source_uid"], "output.md")

    def test_symlink_hardlink_directory_and_binary_inputs(self) -> None:
        file = self.root / "safe.txt"
        file.write_text("Safe content")
        try:
            (self.root / "link.txt").symlink_to(file)
        except OSError:
            pass
        else:
            with self.assertRaises(ValidationError):
                self.client.context.index(self.project, self.source["source_uid"], "link.txt")
        try:
            os.link(file, self.root / "hard.txt")
        except OSError:
            pass
        else:
            with self.assertRaises(ValidationError):
                self.client.context.index(self.project, self.source["source_uid"], "hard.txt")
        (self.root / "dir").mkdir()
        (self.root / "binary").write_bytes(b"\x00\xff")
        for locator in ("dir", "binary"):
            with self.assertRaises(ValidationError):
                self.client.context.index(self.project, self.source["source_uid"], locator)

    def test_changed_file_is_not_captured_and_size_limit_is_enforced(self) -> None:
        path = self.root / "changing.txt"
        path.write_text("Before capture")
        real_read = os.read
        changed = False

        def changing_read(fd: int, size: int) -> bytes:
            nonlocal changed
            value = real_read(fd, size)
            if not changed:
                path.write_text("After capture with a different size")
                changed = True
            return value

        with (
            patch("eric_memory.context_service.os.read", side_effect=changing_read),
            self.assertRaises(ValidationError),
        ):
            self.client.context.index(self.project, self.source["source_uid"], "changing.txt")
        with patch("eric_memory.context_service.MAX_DOCUMENT_BYTES", 2), self.assertRaises(ValidationError):
            self.client.context.index(self.project, self.source["source_uid"], "changing.txt")

    def test_long_multibyte_line_can_be_reconstructed_under_small_budget(self) -> None:
        original = "中文内容" * 2500 + "\nEnd.\n"
        receipt = self.capture(original)
        line, column = 1, 1
        collected = []
        for _ in range(100):
            result = self.client.context.read(
                self.project, receipt["artifact_uid"], start_line=line, start_column=column, max_bytes=2048
            )
            self.assertLessEqual(encoded_size(result), 2048)
            self.assertEqual(encoded_size(result), result["returned_bytes"])
            self.assertTrue(result["results"])
            collected.extend(item["content"] for item in result["results"])
            line, column = result["next_line"], result["next_column"]
            if line is None:
                break
        self.assertEqual("".join(collected), original)

    def test_missing_read_does_not_create_cache_and_wrong_library_is_rejected(self) -> None:
        store = ContextStore(self.data_dir / "absent", self.service.store.library_uid)
        self.assertFalse(store.sources(self.project))
        self.assertFalse(store.directory.exists())
        other = ContextStore(self.data_dir, "wrong-library")
        with self.assertRaises(Exception):
            other.sources(self.project)

    def test_context_capture_never_changes_durable_database_or_managed_backups(self) -> None:
        before = self.service.store.connection.total_changes
        rows = self.service.store.connection.execute("SELECT COUNT(*) FROM audit").fetchone()[0]
        self.capture("# Orchid\nSafe temporary output.\n")
        self.assertEqual(self.service.store.connection.total_changes, before)
        self.assertEqual(self.service.store.connection.execute("SELECT COUNT(*) FROM audit").fetchone()[0], rows)
        self.assertFalse(any("working" in p.name for p in (self.data_dir / "backups").rglob("*")))

    def test_two_process_style_connections_can_write_concurrently(self) -> None:
        for i in range(6):
            (self.root / f"parallel-{i}.md").write_text(f"# Item {i}\nIndependent content.\n")

        def capture(i: int) -> str:
            client = MemoryService(self.data_dir, mode="ro", principal="codex")
            try:
                return client.context.index(self.project, self.source["source_uid"], f"parallel-{i}.md")["artifact_uid"]
            finally:
                client.close()

        with ThreadPoolExecutor(max_workers=3) as executor:
            ids = list(executor.map(capture, range(6)))
        self.assertEqual(len(set(ids)), 6)

    def test_cli_context_runs_from_an_unrelated_directory_with_explicit_principal(self) -> None:
        receipt = self.capture("# Orchid\nLocal CLI can retrieve this.\n")
        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "bin/eric-memory"),
                "--data-dir",
                str(self.data_dir),
                "context",
                "read",
                receipt["artifact_uid"],
                "--project",
                self.project,
                "--principal",
                "codex",
            ],
            cwd=self.root,
            capture_output=True,
            encoding="utf-8",
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["artifact_uid"], receipt["artifact_uid"])

    def test_ordinary_fenced_code_is_exact_in_one_chunk(self) -> None:
        text = "# Example\n```python\n# inner comment\nprint('hello')\n```\n"
        self.assertEqual(chunk_text(text), [(1, 5, text)])
