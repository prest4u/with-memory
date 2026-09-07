"""Known harness catalog. Not a closed world — users can add `other`."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class HarnessPreset:
    key: str
    display_name: str
    typical_session_root: str
    notes: str
    mainland_note: str = ""


CATALOG: list[HarnessPreset] = [
    HarnessPreset(
        "kimi",
        "Kimi Code",
        "~/.kimi/sessions",
        "会话常在 wire.jsonl / context.jsonl。安装时把 ~ 展开成绝对路径后再登记。",
        "C 端新购曾暂停，以用户本机是否已装为准。",
    ),
    HarnessPreset(
        "qwen",
        "Qwen Code",
        "~/.qwen",
        "阿里系 CLI。有 chats/projects 目录才登记 session_root。",
        "大陆常用。",
    ),
    HarnessPreset(
        "lingma",
        "通义灵码 / Qoder",
        "",
        "IDE 插件路径因版本而异。未知则只挂 CLI/MCP，不收割会话。",
        "大陆常用。",
    ),
    HarnessPreset(
        "workbuddy",
        "WorkBuddy",
        "",
        "按用户指出的 projects 目录登记。没有路径就不收割。",
        "大陆常用。",
    ),
    HarnessPreset(
        "codebuddy",
        "CodeBuddy",
        "",
        "与 WorkBuddy 类似，按用户给出的项目根登记。",
        "大陆常用。",
    ),
    HarnessPreset(
        "zcode",
        "ZCode",
        "",
        "GLM 常常只是别的工具里的模型。若用户实际用的是 ZCode，勾这一项。",
        "GLM 不等于独立 harness。",
    ),
    HarnessPreset(
        "minimax",
        "MiniMax Code",
        "",
        "有本机会话目录再填；否则只走 CLI/MCP。",
        "视工作流而定。",
    ),
    HarnessPreset(
        "trae",
        "Trae / TraeCode",
        "",
        "未知路径时不要全盘扫描。",
        "",
    ),
    HarnessPreset(
        "comate",
        "Comate / 文心快码",
        "",
        "未知路径时只挂 CLI。",
        "大陆可能遇到。",
    ),
    HarnessPreset(
        "openclaw",
        "OpenClaw",
        "",
        "按用户给出的工作区登记。",
        "",
    ),
    HarnessPreset(
        "cursor",
        "Cursor",
        "",
        "本仓库 CLI / MCP 即可。不要把 Cursor 工程目录当会话源，除非用户点头。",
        "大陆网络可能受限。",
    ),
    HarnessPreset(
        "claude",
        "Claude Code",
        "~/.claude",
        "可把已点头的项目会话目录登记为 session_root。",
        "",
    ),
    HarnessPreset(
        "codex",
        "Codex / OpenAI",
        "~/.codex",
        "已暂停的大 cron 不要自动恢复。只收用户点头的目录。",
        "大陆可能受限。",
    ),
    HarnessPreset(
        "hermes",
        "Hermes Agent",
        "~/.hermes",
        "完整档可把 profiles/<name> 登为 session_root。旧 memory_store.db 只作导入源。",
        "",
    ),
    HarnessPreset(
        "grok",
        "Grok / xAI",
        "",
        "按用户场景勾选。无本机会话目录则只走 CLI/MCP。",
        "大陆可能受限。",
    ),
    HarnessPreset(
        "other",
        "其他",
        "",
        "安装问卷的开放项。用户自己填写显示名和绝对路径。",
        "不要假设未列出的工具不存在。",
    ),
]


def catalog_by_key() -> dict[str, HarnessPreset]:
    return {item.key: item for item in CATALOG}


def catalog_dicts() -> list[dict[str, str]]:
    return [
        {
            "key": item.key,
            "display_name": item.display_name,
            "typical_session_root": item.typical_session_root,
            "notes": item.notes,
            "mainland_note": item.mainland_note,
        }
        for item in CATALOG
    ]
