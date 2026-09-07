from __future__ import annotations

import json
import subprocess
import sys

from tests.helpers import ROOT, TempServiceTest

# isort: split

from eric_memory.mcp_server import dispatch_tool
from eric_memory.service import MemoryService


class DailyHarvestTests(TempServiceTest):
    def setUp(self) -> None:
        super().setUp()
        self.root = self.data_dir / "sessions"
        self.root.mkdir()
        self.sample = self.root / "session.jsonl"
        self.sample.write_text("Synthetic session.\n", encoding="utf-8")
        self.source_uid = self.service.source_approve(str(self.root))["source"]["source_uid"]

    def _cli(self, *args: str) -> dict:
        result = subprocess.run(
            [sys.executable, str(ROOT / "bin/eric-memory"), "--data-dir", str(self.data_dir), "--json", *args],
            check=True,
            capture_output=True,
            text=True,
        )
        return json.loads(result.stdout)

    def test_interrupted_harvest_redelivers_changes_after_restart(self) -> None:
        first = self.service.harvest_begin(self.source_uid)
        self.assertEqual(len(first["changed_files"]), 1)
        self.service.close()
        self.service = MemoryService(self.data_dir)
        retry = self.service.harvest_begin(self.source_uid)
        self.assertEqual(retry["changed_files"], first["changed_files"])
        self.service.harvest_complete(retry["run_uid"], cursor="processed")
        self.assertEqual(self.service.harvest_begin(self.source_uid)["changed_files"], [])

    def test_retry_does_not_redeliver_previously_acknowledged_unchanged_files(self) -> None:
        first = self.service.harvest_begin(self.source_uid)
        self.service.harvest_complete(first["run_uid"], cursor="first")
        other = self.root / "new.jsonl"
        other.write_text("New synthetic session.\n", encoding="utf-8")
        self.service.harvest_begin(self.source_uid)
        retry = self.service.harvest_begin(self.source_uid)
        self.assertEqual([item["path"] for item in retry["changed_files"]], [str(other.resolve())])
        self.assertEqual(retry["stats"]["unchanged"], 1)

    def test_truncated_run_does_not_acknowledge_partial_changes(self) -> None:
        (self.root / "other.jsonl").write_text("Other session.\n", encoding="utf-8")
        first = self.service.harvest_begin(self.source_uid, max_files=1)
        self.assertEqual(first["status"], "error")
        retry = self.service.harvest_begin(self.source_uid)
        self.assertEqual(len(retry["changed_files"]), 2)

    def test_completing_an_older_run_does_not_acknowledge_newer_content(self) -> None:
        first = self.service.harvest_begin(self.source_uid)
        self.sample.write_text("Changed synthetic session with new facts.\n", encoding="utf-8")
        newer = self.service.harvest_begin(self.source_uid)
        self.service.harvest_complete(first["run_uid"], cursor="old")
        retry = self.service.harvest_begin(self.source_uid)
        self.assertEqual(retry["changed_files"], newer["changed_files"])

    def test_cli_two_phase_harvest_and_projection_preserve_pending_files(self) -> None:
        first = self._cli("harvest", "begin", self.source_uid)
        projection = self._cli("sync", "--projection-only")
        self.assertEqual(projection["projection"], "clean")
        self.assertEqual(projection["scans"], [])
        retry = self._cli("harvest", "begin", self.source_uid)
        self.assertEqual(retry["changed_files"], first["changed_files"])
        completed = self._cli("harvest", "complete", retry["run_uid"], "--cursor", "cli-processed")
        self.assertTrue(completed["committed"])
        self.assertEqual(self._cli("harvest", "begin", self.source_uid)["changed_files"], [])

    def test_mcp_projection_only_does_not_consume_unseen_sessions(self) -> None:
        result = dispatch_tool(self.service, "memory_sync", {"projection_only": True})
        self.assertFalse(result["isError"])
        self.assertEqual(result["structuredContent"]["scans"], [])
        self.assertEqual(len(self.service.harvest_begin(self.source_uid)["changed_files"]), 1)
