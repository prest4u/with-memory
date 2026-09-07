#!/usr/bin/env python3
"""Exercise a native frozen candidate without touching an owner's data directory."""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any


def _run(
    executable: Path,
    data_dir: Path | None,
    *arguments: str,
    stdin: str | None = None,
    timeout: float = 30,
) -> tuple[dict[str, Any] | str, float]:
    command = [str(executable)]
    if data_dir is not None:
        command.extend(("--data-dir", str(data_dir)))
    command.extend(arguments)
    started = time.perf_counter()
    completed = subprocess.run(
        command,
        input=stdin,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    elapsed = time.perf_counter() - started
    if completed.returncode != 0:
        raise SystemExit(
            f"frozen smoke failed ({completed.returncode}): {' '.join(command)}\n"
            f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
        )
    text = completed.stdout.strip()
    try:
        return json.loads(text), elapsed
    except json.JSONDecodeError:
        object_start = text.find("{")
        if object_start >= 0:
            try:
                return json.loads(text[object_start:]), elapsed
            except json.JSONDecodeError:
                pass
        return text, elapsed


async def _mcp_smoke(executable: Path, data_dir: Path) -> dict[str, Any]:
    import anyio
    from mcp.client.stdio import stdio_client

    from mcp import ClientSession, StdioServerParameters

    parameters = StdioServerParameters(
        command=str(executable),
        args=["--data-dir", str(data_dir), "mcp", "--principal", "legacy"],
    )
    started = time.perf_counter()
    async with (
        stdio_client(parameters) as (read_stream, write_stream),
        ClientSession(read_stream, write_stream) as session,
    ):
        initialized = await session.initialize()
        initialize_seconds = time.perf_counter() - started
        listed = await session.list_tools()
        names = {tool.name for tool in listed.tools}
        if names != {"memory_status", "memory_search"}:
            raise AssertionError(f"legacy MCP surface is not least-privilege: {sorted(names)}")
        status = await session.call_tool("memory_status", {})
        if status.is_error or status.structured_content.get("schema_version") != 2:
            raise AssertionError("frozen MCP status call failed")
    await anyio.sleep(0)
    return {
        "initialize_seconds": initialize_seconds,
        "protocol_version": initialized.protocol_version,
        "visible_tools": sorted(names),
    }


def _percentile95(values: list[float]) -> float:
    if len(values) < 2:
        return values[0]
    return statistics.quantiles(values, n=100, method="inclusive")[94]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--executable", type=Path, required=True)
    parser.add_argument("--max-first-startup", type=float, default=15.0)
    parser.add_argument("--max-warm-startup", type=float, default=1.5)
    parser.add_argument("--max-mcp-initialize", type=float, default=2.0)
    args = parser.parse_args()
    executable = args.executable.resolve(strict=True)

    first, first_seconds = _run(executable, None, "--version")
    if "eric-memory " not in str(first):
        raise AssertionError("frozen executable did not report an eric-memory version")
    warm_samples = [_run(executable, None, "--version")[1] for _ in range(7)]
    warm_p95 = _percentile95(warm_samples)

    with tempfile.TemporaryDirectory(prefix="with-release-smoke-") as temporary:
        root = Path(temporary).resolve()
        data_dir = root / "data"
        initialized, _ = _run(executable, data_dir, "--json", "init", "--no-obsidian")
        if initialized.get("schema_version") != 2:
            raise AssertionError("frozen init did not create schema v2")
        candidate, _ = _run(
            executable,
            data_dir,
            "--json",
            "candidate",
            "add",
            "--content",
            "Release smoke candidates require local review before activation.",
            "--entities",
            "With",
            "--confidence",
            "0.95",
        )
        if candidate.get("candidate", {}).get("status") != "pending":
            raise AssertionError("frozen candidate submission did not remain pending")
        reviewed, _ = _run(executable, data_dir, "--json", "review", "--accept", "--limit", "1")
        if reviewed.get("reviewed") != 1:
            raise AssertionError("frozen local review did not accept one candidate")
        search, _ = _run(executable, data_dir, "--json", "search", "With", "--no-files")
        if len(search.get("facts", [])) != 1:
            raise AssertionError("frozen search did not find the accepted fact")
        backup, _ = _run(executable, data_dir, "backup", "create")
        backup_path = Path(backup["backup"]["path"])
        verified, _ = _run(executable, data_dir, "backup", "verify", str(backup_path))
        if not verified.get("ok"):
            raise AssertionError("frozen backup verification failed")
        restored, _ = _run(
            executable,
            data_dir,
            "restore",
            "--from",
            str(backup_path),
            stdin="RESTORE\n",
        )
        if not restored.get("restored"):
            raise AssertionError("frozen restore failed")
        doctor, _ = _run(executable, data_dir, "doctor")
        if not doctor.get("ok"):
            raise AssertionError("frozen doctor failed after restore")
        bundle_path = root / "support.json"
        support, _ = _run(executable, data_dir, "support-bundle", "--output", str(bundle_path))
        if support.get("facts_included") or support.get("paths_included") or not bundle_path.is_file():
            raise AssertionError("support bundle violated its redaction contract")
        mcp = asyncio.run(_mcp_smoke(executable, data_dir))

    gates = {
        "first_startup": first_seconds <= args.max_first_startup,
        "warm_startup_p95": warm_p95 <= args.max_warm_startup,
        "mcp_initialize": mcp["initialize_seconds"] <= args.max_mcp_initialize,
        "frozen_workflow": True,
    }
    result = {
        "executable": str(executable),
        "first_startup_seconds": first_seconds,
        "warm_startup_p95_seconds": warm_p95,
        "mcp": mcp,
        "thresholds": {
            "first_startup_seconds": args.max_first_startup,
            "warm_startup_p95_seconds": args.max_warm_startup,
            "mcp_initialize_seconds": args.max_mcp_initialize,
        },
        "gates": gates,
        "ok": all(gates.values()),
        "synthetic_data_only": True,
    }
    print(json.dumps(result, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
