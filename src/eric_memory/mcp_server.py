"""Stdio MCP server. Tools map 1:1 onto MemoryService methods."""

from __future__ import annotations

import json
import sys
from typing import Any, Callable

from .service import MemoryService

_stdio_framing = "content-length"

PROTOCOL_VERSION = "2024-11-05"

TOOLS = [
    {
        "name": "memory_status",
        "description": "Show memory store counts, registered harnesses, and folders.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "memory_add",
        "description": "Add an active fact (1-3 sentences). Optionally supersede an old fact_id.",
        "inputSchema": {
            "type": "object",
            "required": ["content"],
            "properties": {
                "content": {"type": "string"},
                "category": {"type": "string"},
                "tags": {"type": "string"},
                "entities": {"type": "array", "items": {"type": "string"}},
                "as_of": {"type": "string"},
                "supersedes": {"type": "integer"},
            },
        },
    },
    {
        "name": "memory_search",
        "description": "Search active facts. Deprecated facts are hidden unless include_deprecated is true.",
        "inputSchema": {
            "type": "object",
            "required": ["query"],
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer"},
                "include_deprecated": {"type": "boolean"},
                "scope": {"type": "string", "enum": ["user", "workspace", "project"]},
                "project": {"type": "string"},
                "workspace": {"type": "string"},
            },
        },
    },
    {
        "name": "memory_deprecate",
        "description": "Invalidate a fact without deleting it.",
        "inputSchema": {
            "type": "object",
            "required": ["fact_id"],
            "properties": {
                "fact_id": {"type": "integer"},
                "superseded_by": {"type": "integer"},
                "reason": {"type": "string"},
            },
        },
    },
    {
        "name": "memory_sync",
        "description": "Rebuild the Obsidian home and reindex approved folders/session roots.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "memory_import",
        "description": "Import a read-only Holograph memory_store.db.",
        "inputSchema": {
            "type": "object",
            "required": ["source"],
            "properties": {"source": {"type": "string"}},
        },
    },
    {
        "name": "memory_index_files",
        "description": "Index an approved folder. Returns paths, not file bodies as facts.",
        "inputSchema": {
            "type": "object",
            "properties": {"folder": {"type": "string"}},
        },
    },
    {
        "name": "memory_harness_add",
        "description": "Register an AI tool. Harvest requires an approved absolute session_root.",
        "inputSchema": {
            "type": "object",
            "required": ["key"],
            "properties": {
                "key": {"type": "string"},
                "display_name": {"type": "string"},
                "session_root": {"type": "string"},
                "mcp_mounted": {"type": "boolean"},
                "harvest_ok": {"type": "boolean"},
                "notes": {"type": "string"},
            },
        },
    },
    {
        "name": "memory_harness_list",
        "description": "List registered harnesses and the known catalog.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
]


def _ok(payload: Any) -> dict:
    return {
        "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False, indent=2)}],
        "structuredContent": payload,
    }


def _as_entities(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [part.strip() for part in value.replace("，", ",").split(",") if part.strip()]
    if isinstance(value, (list, tuple)):
        names: list[str] = []
        for item in value:
            names.extend(_as_entities(item) if isinstance(item, str) else [str(item).strip()])
        return [name for name in names if name]
    return []


def _parse_data_dir(args: list[str]) -> str | None:
    index = 0
    while index < len(args):
        token = args[index]
        if token == "--data-dir" and index + 1 < len(args):
            return args[index + 1]
        if token.startswith("--data-dir="):
            return token.split("=", 1)[1] or None
        index += 1
    return None


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return default


def _err(message: str) -> dict:
    return {
        "content": [{"type": "text", "text": message}],
        "isError": True,
    }


def dispatch_tool(service: MemoryService, name: str, arguments: dict[str, Any] | None) -> dict:
    args = arguments or {}
    handlers: dict[str, Callable[[], dict]] = {
        "memory_status": lambda: _ok(service.status()),
        "memory_add": lambda: _ok(
            service.add(
                str(args["content"]),
                category=str(args.get("category") or "general"),
                tags=str(args.get("tags") or ""),
                entities=_as_entities(args.get("entities")),
                as_of=args.get("as_of"),
                supersedes=args.get("supersedes"),
                actor="mcp",
            )
        ),
        "memory_search": lambda: _ok(
            service.search(
                str(args["query"]),
                limit=int(args.get("limit") or 10),
                include_deprecated=_as_bool(args.get("include_deprecated")),
                scope=str(args.get("scope") or "user"),
                project=args.get("project"),
                workspace=args.get("workspace"),
            )
        ),
        "memory_deprecate": lambda: _ok(
            service.deprecate(
                int(args["fact_id"]),
                superseded_by=args.get("superseded_by"),
                reason=str(args.get("reason") or ""),
                actor="mcp",
            )
        ),
        "memory_sync": lambda: _ok(service.sync(actor="mcp")),
        "memory_import": lambda: _ok(service.import_holograph(str(args["source"]), actor="mcp")),
        "memory_index_files": lambda: _ok(service.index_files(args.get("folder"))),
        "memory_harness_add": lambda: _ok(
            service.harness_add(
                str(args["key"]),
                display_name=args.get("display_name"),
                session_root=str(args.get("session_root") or ""),
                mcp_mounted=_as_bool(args.get("mcp_mounted")),
                harvest_ok=_as_bool(args.get("harvest_ok")),
                notes=str(args.get("notes") or ""),
                actor="mcp",
            )
        ),
        "memory_harness_list": lambda: _ok(service.harness_list()),
    }
    handler = handlers.get(name)
    if handler is None:
        return _err(f"unknown tool: {name}")
    try:
        return handler()
    except Exception as exc:  # noqa: BLE001 — surface to the harness
        return _err(f"{type(exc).__name__}: {exc}")


def _read_message(buffer) -> dict | None:
    headers: dict[str, str] = {}
    while True:
        line = buffer.readline()
        if not line:
            return None
        # Cursor stdio sends one JSON object per line; other hosts use Content-Length.
        if line.lstrip().startswith(b"{"):
            global _stdio_framing
            _stdio_framing = "ndjson"
            return json.loads(line.decode("utf-8"))
        if line in (b"\r\n", b"\n"):
            break
        key, value = line.decode("utf-8").split(":", 1)
        headers[key.strip().lower()] = value.strip()
    length = int(headers["content-length"])
    body = buffer.read(length)
    if not body:
        return None
    return json.loads(body.decode("utf-8"))


def _write_message(payload: dict) -> None:
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    if _stdio_framing == "ndjson":
        sys.stdout.buffer.write(raw + b"\n")
    else:
        sys.stdout.buffer.write(f"Content-Length: {len(raw)}\r\n\r\n".encode("ascii") + raw)
    sys.stdout.buffer.flush()


def handle_rpc(service: MemoryService, message: dict) -> dict | None:
    method = message.get("method")
    msg_id = message.get("id")
    if method == "notifications/initialized" or method == "notifications/cancelled":
        return None
    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}, "resources": {}, "prompts": {}},
                "serverInfo": {"name": "eric-memory", "version": "0.1.0"},
            },
        }
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {"tools": TOOLS}}
    if method == "resources/list":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {"resources": []}}
    if method == "resources/templates/list":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {"resourceTemplates": []}}
    if method == "prompts/list":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {"prompts": []}}
    if method == "tools/call":
        params = message.get("params") or {}
        result = dispatch_tool(service, params.get("name", ""), params.get("arguments") or {})
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}
    if method == "ping":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {}}
    if msg_id is None:
        return None
    return {
        "jsonrpc": "2.0",
        "id": msg_id,
        "error": {"code": -32601, "message": f"method not found: {method}"},
    }


def run(data_dir: str | None = None) -> None:
    global _stdio_framing
    _stdio_framing = "content-length"
    service = MemoryService(data_dir)
    try:
        while True:
            message = _read_message(sys.stdin.buffer)
            if message is None:
                break
            reply = handle_rpc(service, message)
            if reply is not None:
                _write_message(reply)
    finally:
        service.close()


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    data_dir = _parse_data_dir(list(args))
    run(data_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
