#!/usr/bin/env python3
"""Synthetic reference benchmark for 100k facts/files and release performance gates."""

from __future__ import annotations

import argparse
import json
import math
import os
import platform
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import patch

from eric_memory.scopes import ScopeSpec
from eric_memory.service import MemoryService
from eric_memory.store import normalized_content_hash, search_terms, utc_now


def _p95(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, math.ceil(len(ordered) * 0.95) - 1)]


def _seed_facts(service: MemoryService, count: int) -> list[str]:
    connection = service.store.connection
    scope = ScopeSpec("user", "")
    scope_id = int(connection.execute("SELECT scope_id FROM scopes WHERE fingerprint='user:'").fetchone()[0])
    namespace = uuid.UUID(service.store.library_uid)
    now = utc_now()
    queries: list[str] = []
    with service.store.write_transaction():
        for start in range(0, count, 1000):
            stop = min(count, start + 1000)
            fact_rows: list[tuple[Any, ...]] = []
            scope_rows: list[tuple[int, int]] = []
            term_rows: list[tuple[int, str, str]] = []
            event_rows: list[tuple[str, str, str, str, str, str, str, str, str]] = []
            for index in range(start, stop):
                fact_id = index + 1
                content = (
                    f"Memory benchmark record {index:06d} topic {index % 1000:04d} retains a stable local pointer."
                )
                fact_uid = str(uuid.uuid5(namespace, f"benchmark-fact:{index}"))
                fact_rows.append(
                    (
                        fact_id,
                        fact_uid,
                        content,
                        normalized_content_hash(content),
                        "benchmark",
                        "",
                        0.5,
                        "active",
                        "2026-01-01",
                        None,
                        "synthetic",
                        "",
                        scope.fingerprint,
                        now,
                        now,
                    )
                )
                scope_rows.append((fact_id, scope_id))
                term_rows.extend((fact_id, term, kind) for term, kind in search_terms(content))
                event_rows.append(
                    (
                        str(uuid.uuid4()),
                        service.store.library_uid,
                        service.store.device_uid,
                        str(uuid.uuid4()),
                        "fact",
                        fact_uid,
                        "created",
                        "{}",
                        now,
                    )
                )
                if index in {round((count - 1) * part / 19) for part in range(20)}:
                    queries.append(f"record {index:06d} topic {index % 1000:04d}")
            connection.executemany(
                """
                INSERT INTO facts(
                    fact_id, fact_uid, content, normalized_content_hash, category, tags,
                    trust, status, as_of, superseded_by, source_kind, source_ref,
                    scope_fingerprint, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                fact_rows,
            )
            connection.executemany("INSERT INTO fact_scopes(fact_id, scope_id) VALUES (?, ?)", scope_rows)
            connection.executemany(
                "INSERT OR IGNORE INTO fact_search_terms(fact_id, term, kind) VALUES (?, ?, ?)",
                term_rows,
            )
            connection.executemany(
                """
                INSERT INTO change_events(
                    event_uid, library_uid, device_uid, operation_uid, object_type,
                    object_uid, action, metadata_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                event_rows,
            )
    return queries


def _search_metrics(data_dir: Path, service: MemoryService, queries: list[str]) -> dict[str, float]:
    for query in queries[:3]:
        service.search(query, limit=5, include_files=False)
    warm: list[float] = []
    for query in queries:
        started = time.perf_counter()
        result = service.search(query, limit=5, include_files=False)
        warm.append(time.perf_counter() - started)
        if not result["facts"]:
            raise RuntimeError(f"benchmark query returned no fact: {query}")
    cold: list[float] = []
    for query in queries[:10]:
        started = time.perf_counter()
        reader = MemoryService(data_dir, mode="ro", principal="local")
        try:
            reader.search(query, limit=5, include_files=False)
        finally:
            reader.close()
        cold.append(time.perf_counter() - started)
    worker = subprocess.run(  # noqa: S603 - current interpreter and fixed local script
        [
            sys.executable,
            str(Path(__file__).resolve()),
            "--search-worker",
            str(data_dir),
            queries[-1],
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if worker.returncode != 0:
        raise RuntimeError(f"search memory worker failed: {worker.stderr[-500:]}")
    worker_metrics = json.loads(worker.stdout)
    return {
        "warm_p95_seconds": _p95(warm),
        "cold_p95_seconds": _p95(cold),
        "process_peak_mb": float(worker_metrics["process_peak_mb"]),
    }


def _create_files(root: Path, count: int) -> None:
    for index in range(count):
        folder = root / f"batch-{index // 1000:04d}"
        folder.mkdir(exist_ok=True)
        descriptor = os.open(folder / f"record-{index:06d}.txt", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(descriptor)


def _scan_metrics(root: Path, count: int) -> dict[str, float | int]:
    data_dir = root.parent / "file-data"
    service = MemoryService.for_init(data_dir)
    service.init(data_dir=data_dir, tier="full", write_repo_pointer=False)
    try:
        source = service.source_approve(str(root))["source"]
        initial = service.source_scan(source["source_uid"], max_files=count)
        if initial["status"] != "complete":
            raise RuntimeError(f"initial benchmark scan failed: {initial}")
        started = time.perf_counter()
        with patch(
            "eric_memory.files._hash_regular_file_no_follow",
            side_effect=AssertionError("unchanged scan reread a file body"),
        ):
            unchanged = service.source_scan(source["source_uid"], max_files=count)
        elapsed = time.perf_counter() - started
        if unchanged["status"] != "complete":
            raise RuntimeError(f"unchanged benchmark scan failed: {unchanged}")
        stats = unchanged["stats"]
        return {
            "unchanged_seconds": elapsed,
            "unchanged_files": int(stats["unchanged"]),
            "changed_files": int(stats["changed"]),
        }
    finally:
        service.close()


def _mcp_initialize(data_dir: Path) -> float:
    command = [
        sys.executable,
        "-m",
        "eric_memory",
        "--data-dir",
        str(data_dir),
        "mcp",
        "--principal",
        "legacy",
    ]
    started = time.perf_counter()
    process = subprocess.Popen(  # noqa: S603 - current interpreter and fixed module/arguments
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert process.stdin is not None and process.stdout is not None
        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "with-benchmark", "version": "1"},
            },
        }
        process.stdin.write(json.dumps(request) + "\n")
        process.stdin.flush()
        response = json.loads(process.stdout.readline())
        if response.get("id") != 1 or "result" not in response:
            raise RuntimeError(f"MCP initialization failed: {response}")
        return time.perf_counter() - started
    finally:
        if process.stdin:
            process.stdin.close()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.terminate()
            process.wait(timeout=5)


def run_benchmark(*, facts: int, files: int, protocol: bool = True) -> dict[str, Any]:
    if facts < 1 or files < 1:
        raise ValueError("facts and files must be positive")
    with tempfile.TemporaryDirectory(prefix="with-benchmark-") as temporary:
        root = Path(temporary).resolve()
        data_dir = root / "fact-data"
        service = MemoryService.for_init(data_dir)
        service.init(data_dir=data_dir, tier="full", write_repo_pointer=False)
        try:
            seed_started = time.perf_counter()
            queries = _seed_facts(service, facts)
            seed_seconds = time.perf_counter() - seed_started
            search = _search_metrics(data_dir, service, queries)
            mcp_seconds = _mcp_initialize(data_dir) if protocol else None
        finally:
            service.close()
        file_root = root / "files"
        file_root.mkdir()
        _create_files(file_root, files)
        scan = _scan_metrics(file_root, files)
        reference = facts == 100_000 and files == 100_000
        gates = {
            "warm_search": search["warm_p95_seconds"] < 0.250,
            "cold_search": search["cold_p95_seconds"] < 0.750,
            "search_memory": search["process_peak_mb"] < 300,
            "unchanged_scan": scan["unchanged_seconds"] < (15.0 if reference else 10.0),
            "mcp_initialize": mcp_seconds is None or mcp_seconds < 2.0,
            "file_count": scan["unchanged_files"] == files and scan["changed_files"] == 0,
        }
        return {
            "ok": all(gates.values()),
            "reference_scale": reference,
            "environment": {
                "platform": platform.platform(),
                "python": platform.python_version(),
                "machine": platform.machine(),
            },
            "facts": facts,
            "files": files,
            "seed_seconds": seed_seconds,
            "search": search,
            "scan": scan,
            "mcp_initialize_seconds": mcp_seconds,
            "gates": gates,
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--search-worker", nargs=2, metavar=("DATA_DIR", "QUERY"), help=argparse.SUPPRESS)
    parser.add_argument("--facts", type=int, default=10_000)
    parser.add_argument("--files", type=int, default=5_000)
    parser.add_argument("--no-protocol", action="store_true")
    parser.add_argument("--report-only", action="store_true")
    args = parser.parse_args()
    if args.search_worker:
        data_dir = Path(args.search_worker[0]).resolve()
        reader = MemoryService(data_dir, mode="ro", principal="local")
        try:
            reader.search(args.search_worker[1], limit=5, include_files=False)
            if os.name == "nt":
                import ctypes

                class ProcessMemoryCounters(ctypes.Structure):
                    _fields_ = [
                        ("cb", ctypes.c_ulong),
                        ("page_fault_count", ctypes.c_ulong),
                        ("peak_working_set_size", ctypes.c_size_t),
                        ("working_set_size", ctypes.c_size_t),
                        ("quota_peak_paged_pool_usage", ctypes.c_size_t),
                        ("quota_paged_pool_usage", ctypes.c_size_t),
                        ("quota_peak_non_paged_pool_usage", ctypes.c_size_t),
                        ("quota_non_paged_pool_usage", ctypes.c_size_t),
                        ("pagefile_usage", ctypes.c_size_t),
                        ("peak_pagefile_usage", ctypes.c_size_t),
                    ]

                counters = ProcessMemoryCounters()
                counters.cb = ctypes.sizeof(counters)
                handle = ctypes.windll.kernel32.GetCurrentProcess()
                ctypes.windll.psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), ctypes.sizeof(counters))
                peak_mb = int(counters.peak_working_set_size) / (1024 * 1024)
            else:
                import resource

                peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
                peak_mb = peak / (1024 * 1024) if sys.platform == "darwin" else peak / 1024
        finally:
            reader.close()
        print(json.dumps({"process_peak_mb": peak_mb}))
        return 0
    report = run_benchmark(facts=args.facts, files=args.files, protocol=not args.no_protocol)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] or args.report_only else 1


if __name__ == "__main__":
    raise SystemExit(main())
