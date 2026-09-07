#!/usr/bin/env python3
"""Destructive acceptance gate restricted to a temporary database or an explicit copy."""

from __future__ import annotations

import argparse
import json
import tempfile
import uuid
from pathlib import Path
from typing import Any

from eric_memory.mcp_server import dispatch_tool
from eric_memory.paths import require_absolute
from eric_memory.service import MemoryService

ROOT = Path(__file__).resolve().parents[1]
INDEX_SAMPLE = ROOT / "vault-template"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-dir",
        help="absolute path to a disposable data directory; omitted means a new temporary directory",
    )
    parser.add_argument(
        "--allow-existing-copy",
        action="store_true",
        help="confirm that an existing memory.db is a disposable copy, never the live database",
    )
    parser.add_argument(
        "--holograph-copy",
        help="optional absolute path to a Holograph database copy imported read-only",
    )
    return parser


def _open_gate_service(data_dir: Path, *, allow_existing_copy: bool) -> MemoryService:
    database = data_dir / "memory.db"
    if database.exists():
        if not allow_existing_copy:
            raise SystemExit("existing memory.db refused; pass --allow-existing-copy only for a disposable copy")
        return MemoryService(data_dir)
    service = MemoryService.for_init(data_dir)
    service.init(data_dir=data_dir, tier="full", write_repo_pointer=False)
    return service


def _run(data_dir: Path, args: argparse.Namespace) -> dict[str, Any]:
    service = _open_gate_service(data_dir, allow_existing_copy=args.allow_existing_copy)
    harness: MemoryService | None = None
    try:
        imported: dict[str, Any] | None = None
        if args.holograph_copy:
            source = require_absolute(args.holograph_copy, name="holograph copy")
            imported = service.import_holograph(str(source), actor="mac-gate")

        service.harness_add(
            "gate-harness",
            display_name="Disposable acceptance harness",
            session_root=str(INDEX_SAMPLE.resolve()),
            mcp_mounted=True,
            notes="Synthetic acceptance only",
        )
        service.source_approve(str(INDEX_SAMPLE.resolve()), harness_key="gate-harness")
        service.harness_add("gate-harness", harvest_ok=True)
        source = next(item for item in service.source_list()["sources"] if item["harness_key"] == "gate-harness")
        harness = MemoryService(data_dir, principal="gate-harness")
        submitted = dispatch_tool(
            harness,
            "memory_candidate_add",
            {
                "content": "Acceptance confirms candidate review and scoped retrieval work together.",
                "submission_uid": str(uuid.uuid4()),
                "entities": ["With acceptance"],
                "source_uid": source["source_uid"],
                "source_locator": "记忆首页.md",
            },
        )
        if submitted.get("isError"):
            raise SystemExit(submitted["structuredContent"]["error"])
        candidate_uid = submitted["structuredContent"]["candidate"]["candidate_uid"]
        accepted = service.review_accept(candidate_uid)
        searched = dispatch_tool(harness, "memory_search", {"query": "acceptance"})
        denied = dispatch_tool(
            harness,
            "memory_add",
            {"content": "This direct active write must never be accepted."},
        )
        backup = service.backup_create(kind="acceptance")
        verified = service.backup_verify(backup["backup"]["path"])
        doctor = service.doctor()
        service.sync(actor="mac-gate")
        found_ids = [item["fact_id"] for item in searched["structuredContent"]["facts"]]
        if accepted["fact"]["fact_id"] not in found_ids:
            raise SystemExit("accepted candidate was not retrieved")
        if not denied.get("isError") or denied["structuredContent"]["error"]["code"] != "PERMISSION_REQUIRED":
            raise SystemExit("ordinary harness unexpectedly wrote an active fact")
        if not verified["ok"]:
            raise SystemExit("acceptance backup failed verification")
        return {
            "ok": True,
            "synthetic": True,
            "data_dir": str(data_dir),
            "import": imported,
            "accepted_fact_id": accepted["fact"]["fact_id"],
            "search_ids": found_ids,
            "direct_write_error": denied["structuredContent"]["error"]["code"],
            "backup_verified": True,
            "doctor_ok": doctor["ok"],
        }
    finally:
        if harness is not None:
            harness.close()
        service.close()


def main() -> int:
    args = _parser().parse_args()
    if args.data_dir:
        data_dir = require_absolute(args.data_dir, name="gate data dir")
        result = _run(data_dir, args)
    else:
        with tempfile.TemporaryDirectory(prefix="with-mac-gate-") as temporary:
            result = _run(Path(temporary).resolve() / "data", args)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
