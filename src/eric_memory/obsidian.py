"""Project the store into a human-readable Obsidian vault. Vault is never the source of truth."""

from __future__ import annotations

import os
from pathlib import Path

from .models import Fact
from .paths import atomic_write_text, require_absolute
from .store import MemoryStore

HOME_NAME = "记忆首页.md"
ACTIVE_NAME = "现行.md"
DEPRECATED_NAME = "已过期.md"
FILES_NAME = "资料夹.md"
HARNESS_NAME = "已接工具.md"
MANAGED_PAGES = (HOME_NAME, ACTIVE_NAME, DEPRECATED_NAME, FILES_NAME, HARNESS_NAME)


def _md_escape(text: str) -> str:
    value = text.replace("\r\n", "\n").replace("\n", " ").strip()
    value = value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    for marker in ("\\", "`", "*", "_", "[", "]", "|", "#"):
        value = value.replace(marker, f"\\{marker}")
    return value


def _code_escape(text: str) -> str:
    return text.replace("`", "ˋ").replace("\r", " ").replace("\n", " ")


def _fact_line(fact: Fact) -> str:
    entities = "、".join(fact.entities[:8])
    extra = f" · {entities}" if entities else ""
    successor = f" → 被 {fact.superseded_by} 取代" if fact.superseded_by else ""
    return (
        f"- **#{fact.fact_id}** `{fact.category}` {fact.as_of}{extra}{successor}\n  {_md_escape(fact.content)[:400]}\n"
    )


def render_home(store: MemoryStore) -> str:
    counts = store.counts()
    harnesses = store.list_harnesses()
    folders = store.list_folders()
    lines = [
        "# 记忆首页",
        "",
        "这是给人看的窗口。机器真值在本机 SQLite，不在这些笔记里。",
        "改事实请让手头的 AI 走官方 CLI / MCP，不要在这里直接改当作现行。",
        "",
        "## 一眼",
        "",
        f"- 现行事实：{counts['active']}",
        f"- 已过期（仍保留）：{counts['deprecated']}",
        f"- 实体：{counts['entities']}",
        f"- 已索引文件：{counts['files']}",
        f"- 已接工具：{counts['harnesses']}",
        f"- 已点头的资料夹：{counts['folders']}",
        f"- 待审候选：{counts.get('candidates_pending', 0)}",
        "",
        "## 打开",
        "",
        f"- [[{ACTIVE_NAME[:-3]}|现行事实]]",
        f"- [[{DEPRECATED_NAME[:-3]}|已过期（历史）]]",
        f"- [[{FILES_NAME[:-3]}|资料夹]]",
        f"- [[{HARNESS_NAME[:-3]}|已接工具]]",
        "",
        "## 今天怎么用",
        "",
        "1. 打开任何一个已接上的 AI 工具，开始干活前它应先搜索记忆。",
        "2. 每天（或你设的自动化）跑一次「同步记忆」任务。",
        "3. 想核对现行说法，看本页和「现行」。过期条目不会被删。",
        "",
        "## 已接工具",
        "",
    ]
    if not harnesses:
        lines.append("还没有登记工具。请跑「添加 AI 工具」任务。")
    else:
        for harness in harnesses:
            harvest = "可收割已点头目录" if harness.harvest_ok and harness.session_root else "只走 CLI/MCP"
            lines.append(f"- **{harness.display_name}**（`{harness.key}`）· {harvest}")
    lines.extend(["", "## 资料夹", ""])
    if not folders:
        lines.append("还没有指定资料夹。安装或添加任务里填绝对路径。")
    else:
        for folder in folders:
            label = folder["label"] or folder["path"]
            lines.append(f"- {_md_escape(label)}：`{_code_escape(folder['path'])}`")
    lines.append("")
    return "\n".join(lines)


def render_facts(store: MemoryStore, *, status: str, title: str, intro: str, limit: int = 80) -> str:
    with store._lock:
        rows = store.connection.execute(
            """
            SELECT * FROM facts
            WHERE status = ?
            ORDER BY updated_at DESC, fact_id DESC
            LIMIT ?
            """,
            (status, limit),
        ).fetchall()
    facts = [store._row_to_fact(row) for row in rows]
    lines = [f"# {title}", "", intro, ""]
    if not facts:
        lines.append("（目前没有条目）")
        lines.append("")
        return "\n".join(lines)
    for fact in facts:
        lines.append(_fact_line(fact))
    if len(facts) >= limit:
        lines.append(f"只列出最近 {limit} 条。更多请用 `eric-memory search`。")
        lines.append("")
    return "\n".join(lines)


def render_files(store: MemoryStore) -> str:
    files = store.list_files(limit=200)
    folders = store.list_folders()
    lines = [
        "# 资料夹",
        "",
        "这里只放路径指针。文件正文不会被写成事实。",
        "",
        "## 已点头的根目录",
        "",
    ]
    if not folders:
        lines.append("（无）")
    else:
        for folder in folders:
            lines.append(f"- `{_code_escape(folder['path'])}`")
    lines.extend(["", "## 最近索引的文件", ""])
    if not files:
        lines.append("还没有索引。跑 `eric-memory index-files` 或每日同步。")
    else:
        for item in files[:80]:
            lines.append(f"- `{_code_escape(item['name'])}` — `{_code_escape(item['path'])}`")
    lines.append("")
    return "\n".join(lines)


def render_harnesses(store: MemoryStore) -> str:
    harnesses = store.list_harnesses()
    lines = [
        "# 已接工具",
        "",
        "每日同步只处理这里登记过、并且用户点头可收割的目录。",
        "",
    ]
    if not harnesses:
        lines.append("还没有工具。请跑 `quests/添加AI工具.md`。")
        lines.append("")
        return "\n".join(lines)
    for harness in harnesses:
        root = harness.session_root or "（无会话目录，只走 CLI/MCP）"
        lines.append(f"## {harness.display_name}")
        lines.append("")
        lines.append(f"- 键：`{harness.key}`")
        lines.append(f"- 会话根：`{_code_escape(root)}`")
        lines.append(f"- MCP 已挂：{'是' if harness.mcp_mounted else '否'}")
        lines.append(f"- 允许收割：{'是' if harness.harvest_ok else '否'}")
        if harness.notes:
            lines.append(f"- 备注：{_md_escape(harness.notes)}")
        lines.append("")
    return "\n".join(lines)


def write_vault(store: MemoryStore, vault_dir: str | Path) -> dict[str, str]:
    root = require_absolute(vault_dir, name="vault dir")
    root.mkdir(parents=True, exist_ok=True)
    (root / ".obsidian").mkdir(exist_ok=True)
    pages = {
        HOME_NAME: render_home(store),
        ACTIVE_NAME: render_facts(
            store,
            status="active",
            title="现行",
            intro="默认检索只看这些。过期条目在「已过期」，不会出现在这里。",
        ),
        DEPRECATED_NAME: render_facts(
            store,
            status="deprecated",
            title="已过期",
            intro="作废不删。需要历史时用 `eric-memory search --include-deprecated`。",
        ),
        FILES_NAME: render_files(store),
        HARNESS_NAME: render_harnesses(store),
    }
    written: dict[str, str] = {}
    for name, body in pages.items():
        path = root / name
        atomic_write_text(path.resolve(), body, mode=0o600)
        written[name] = str(path)
    return written


def remove_managed_projection(vault_dir: str | Path) -> list[str]:
    """Remove With-owned pages when purge cannot safely regenerate them."""
    root = require_absolute(vault_dir, name="vault dir")
    removed: list[str] = []
    for name in MANAGED_PAGES:
        path = root / name
        if path.exists():
            path.unlink()
            removed.append(str(path))
    if os.name != "nt" and root.is_dir():
        directory_fd = os.open(root, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    return removed


def copy_template(template_dir: Path, vault_dir: Path) -> None:
    """Copy starter notes only when the vault is empty of our home page."""
    dest = require_absolute(vault_dir, name="vault dir")
    dest.mkdir(parents=True, exist_ok=True)
    home = dest / HOME_NAME
    if home.exists():
        return
    if not template_dir.is_dir():
        return
    for path in template_dir.glob("*.md"):
        target = dest / path.name
        if not target.exists():
            atomic_write_text(target.resolve(), path.read_text(encoding="utf-8"), mode=0o600)
