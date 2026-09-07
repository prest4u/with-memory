#!/usr/bin/env python3
"""Portable repo contract check. Safe on CI; does not open a live memory.db."""

from __future__ import annotations

import json
from pathlib import Path

from eric_memory.catalog import catalog_dicts
from eric_memory.mcp_server import TOOLS

ROOT = Path(__file__).resolve().parents[1]
MAINTAINER_HOME_MARKER = "/Users" + "/eric"

PLAN_KEYS = {
    "kimi",
    "qwen",
    "lingma",
    "workbuddy",
    "codebuddy",
    "zcode",
    "minimax",
    "trae",
    "comate",
    "openclaw",
    "cursor",
    "claude",
    "codex",
    "hermes",
    "grok",
    "other",
}
NEED_MCP = {
    "memory_status",
    "memory_add",
    "memory_search",
    "memory_deprecate",
    "memory_sync",
    "memory_import",
    "memory_index_files",
    "memory_harness_add",
    "memory_harness_list",
    "memory_candidate_add",
    "memory_candidate_list",
    "memory_source_list",
    "memory_harvest_begin",
    "memory_harvest_complete",
    "memory_history",
}
NEED_FILES = (
    ROOT / "LICENSE",
    ROOT / "AGENTS.md",
    ROOT / "README.md",
    ROOT / "README.zh-CN.md",
    ROOT / "CONTRIBUTING.md",
    ROOT / "CONTRIBUTING.zh-CN.md",
    ROOT / "SECURITY.md",
    ROOT / "SECURITY.zh-CN.md",
    ROOT / "CHANGELOG.md",
    ROOT / "pyproject.toml",
    ROOT / "with.spec",
    ROOT / ".github" / "workflows" / "ci.yml",
    ROOT / ".github" / "workflows" / "release.yml",
    ROOT / "skills" / "严格技能.md",
    ROOT / ".cursor" / "mcp.json.example",
    ROOT / "bin" / "eric-memory",
    ROOT / "mcp" / "server.py",
    ROOT / "quests" / "安装任务.md",
    ROOT / "quests" / "每日同步任务.md",
    ROOT / "quests" / "添加AI工具.md",
    ROOT / "quests" / "en" / "install.md",
    ROOT / "quests" / "en" / "daily-sync.md",
    ROOT / "quests" / "en" / "add-harness.md",
    ROOT / "docs" / "windows-linux.md",
    ROOT / "docs" / "en" / "introduction.md",
    ROOT / "docs" / "en" / "architecture.md",
    ROOT / "docs" / "en" / "simple.md",
    ROOT / "docs" / "en" / "full.md",
    ROOT / "docs" / "en" / "cli-mcp.md",
    ROOT / "docs" / "en" / "windows-linux.md",
    ROOT / "docs" / "en" / "install-upgrade.md",
    ROOT / "docs" / "en" / "migration-backup-restore.md",
    ROOT / "docs" / "en" / "privacy-threat-model.md",
    ROOT / "docs" / "en" / "purge.md",
    ROOT / "docs" / "en" / "troubleshooting-support.md",
    ROOT / "docs" / "en" / "release-process.md",
    ROOT / "docs" / "安装与升级.md",
    ROOT / "docs" / "迁移备份与恢复.md",
    ROOT / "docs" / "隐私与威胁模型.md",
    ROOT / "docs" / "紧急清除.md",
    ROOT / "docs" / "故障排查与支持.md",
    ROOT / "docs" / "发布流程.md",
    ROOT / "release" / "evidence.example.json",
    ROOT / "scripts" / "benchmark.py",
    ROOT / "scripts" / "evaluate_search.py",
    ROOT / "scripts" / "release_smoke.py",
    ROOT / "scripts" / "verify_release_evidence.py",
    ROOT / "vault-template" / "记忆首页.md",
)

PUBLIC_SCAN = (
    ROOT / "README.md",
    ROOT / "README.zh-CN.md",
    ROOT / "AGENTS.md",
    ROOT / "CONTRIBUTING.md",
    ROOT / "CONTRIBUTING.zh-CN.md",
    ROOT / "SECURITY.md",
    ROOT / "SECURITY.zh-CN.md",
    ROOT / "docs",
    ROOT / "quests",
    ROOT / "adapters",
    ROOT / "skills",
)


def collect_gaps() -> list[str]:
    gaps: list[str] = []
    for path in NEED_FILES:
        if not path.is_file():
            gaps.append(f"missing {path.relative_to(ROOT)}")
    example = ROOT / ".cursor" / "mcp.json.example"
    if example.is_file() and "eric-memory" not in example.read_text(encoding="utf-8"):
        gaps.append("mcp.json.example missing eric-memory")
    if example.is_file() and "--principal" not in example.read_text(encoding="utf-8"):
        gaps.append("mcp.json.example missing principal identity")
    keys = {item["key"] for item in catalog_dicts()}
    if PLAN_KEYS - keys:
        gaps.append(f"catalog missing {sorted(PLAN_KEYS - keys)}")
    mcp_names = {tool["name"] for tool in TOOLS}
    if NEED_MCP - mcp_names:
        gaps.append(f"mcp missing {sorted(NEED_MCP - mcp_names)}")
    license_text = (ROOT / "LICENSE").read_text(encoding="utf-8") if (ROOT / "LICENSE").is_file() else ""
    if license_text and "MIT" not in license_text:
        gaps.append("LICENSE is not MIT")
    readme = (ROOT / "README.md").read_text(encoding="utf-8") if (ROOT / "README.md").is_file() else ""
    if readme and "With is a persistent memory layer for your harness." not in readme:
        gaps.append("README.md missing With. lead sentence")
    for adapter in (ROOT / "adapters").glob("*.md"):
        adapter_text = adapter.read_text(encoding="utf-8")
        if "mcp/server.py" in adapter_text and "--principal" not in adapter_text:
            gaps.append(f"{adapter.relative_to(ROOT)} mounts MCP without a principal")
    init_text = (ROOT / "src" / "eric_memory" / "__init__.py").read_text(encoding="utf-8")
    version_line = next((line for line in init_text.splitlines() if line.startswith("__version__ = ")), "")
    public_key = ROOT / "src" / "eric_memory" / "resources" / "release-public-key.pem"
    if '"1.' in version_line and not public_key.is_file():
        gaps.append("v1 version declared without an embedded release trust root")
    for item in PUBLIC_SCAN:
        paths = [item] if item.is_file() else list(item.rglob("*.md")) if item.is_dir() else []
        for path in paths:
            text = path.read_text(encoding="utf-8")
            if MAINTAINER_HOME_MARKER in text:
                gaps.append(f"{path.relative_to(ROOT)} contains a maintainer home path")
    for path in (ROOT / "scripts").glob("*.py"):
        if MAINTAINER_HOME_MARKER in path.read_text(encoding="utf-8"):
            gaps.append(f"{path.relative_to(ROOT)} contains a maintainer home path")
    return gaps


def main() -> int:
    gaps = collect_gaps()
    payload = {"ok": not gaps, "gaps": gaps, "portable": True}
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
