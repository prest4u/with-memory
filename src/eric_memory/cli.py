"""Official CLI. Same actions as MCP. No raw SQL."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from . import __version__
from .service import MemoryService


def _print(payload: Any, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    if isinstance(payload, dict) and "facts" in payload:
        print(f"layer={payload.get('layer')} results={len(payload['facts'])}")
        for fact in payload["facts"]:
            mark = "现行" if fact["status"] == "active" else "过期"
            print(f"[{fact['fact_id']}] {mark} {fact['category']} {fact['as_of']}")
            print(f"  {fact['content'][:220]}")
        files = payload.get("files") or []
        if files:
            print(f"files={len(files)}")
            for item in files:
                print(f"  {item['path']}")
        return
    if isinstance(payload, dict) and "fact" in payload:
        fact = payload["fact"]
        print(f"#{fact['fact_id']} {fact['status']} {fact['content'][:220]}")
        if "deprecated" in payload:
            old = payload["deprecated"]
            print(f"deprecated #{old['fact_id']} -> #{fact['fact_id']}")
        return
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _split_entities(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [part.strip() for part in raw.replace("，", ",").split(",") if part.strip()]


def _peel_globals(argv: list[str] | None) -> tuple[list[str], str | None, bool, bool]:
    raw = list(sys.argv[1:] if argv is None else argv)
    data_dir: str | None = None
    as_json = False
    as_version = False
    kept: list[str] = []
    index = 0
    while index < len(raw):
        token = raw[index]
        if token in {"--version", "-V"}:
            as_version = True
            index += 1
            continue
        if token == "--json":
            as_json = True
            index += 1
            continue
        if token == "--data-dir" and index + 1 < len(raw):
            data_dir = raw[index + 1]
            index += 2
            continue
        if token.startswith("--data-dir="):
            data_dir = token.split("=", 1)[1]
            index += 1
            continue
        kept.append(token)
        index += 1
    return kept, data_dir, as_json, as_version


def _service(args: argparse.Namespace) -> MemoryService:
    return MemoryService(getattr(args, "data_dir", None))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="eric-memory",
        description="本机记忆系统：作废不删，CLI 与 MCP 同一套动作。",
    )
    parser.add_argument("--data-dir", help="绝对数据目录，或仅在边界使用 ~")
    parser.add_argument("--json", action="store_true", help="机器可读输出")
    parser.add_argument("--version", "-V", action="store_true", help="打印版本后退出")
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="创建数据目录、库和 Obsidian 首页")
    p_init.add_argument("--vault", help="Obsidian 库绝对路径")
    p_init.add_argument("--tier", choices=("simple", "full"), default="simple")
    p_init.add_argument("--folder", action="append", default=[], help="可重复，指定资料夹")

    sub.add_parser("status", help="查看库、工具、资料夹")

    p_add = sub.add_parser("add", help="写入一条现行事实")
    p_add.add_argument("--content", required=True)
    p_add.add_argument("--category", default="general")
    p_add.add_argument("--tags", default="")
    p_add.add_argument("--entities", default="")
    p_add.add_argument("--as-of")
    p_add.add_argument("--trust", type=float, default=0.5)
    p_add.add_argument("--supersedes", type=int)

    p_search = sub.add_parser("search", help="检索现行事实；过期默认不可见")
    p_search.add_argument("query")
    p_search.add_argument("--limit", type=int, default=10)
    p_search.add_argument("--include-deprecated", action="store_true")
    p_search.add_argument("--scope", default="user", choices=("user", "workspace", "project"))
    p_search.add_argument("--project")
    p_search.add_argument("--workspace")

    p_dep = sub.add_parser("deprecate", help="作废一条事实，不删除")
    p_dep.add_argument("fact_id", type=int)
    p_dep.add_argument("--superseded-by", type=int)
    p_dep.add_argument("--reason", default="")

    p_sync = sub.add_parser("sync", help="重建 Obsidian 首页并重索引已点头目录")
    p_sync.add_argument("--actor", default="cli")

    p_import = sub.add_parser("import", help="从只读 Holograph 库导入")
    p_import.add_argument("source_kind", choices=("holograph",))
    p_import.add_argument("--source", required=True)

    p_index = sub.add_parser("index-files", help="索引指定或已登记资料夹")
    p_index.add_argument("--folder")

    p_hadd = sub.add_parser("harness", help="工具登记")
    hsub = p_hadd.add_subparsers(dest="harness_cmd", required=True)
    h_add = hsub.add_parser("add", help="登记或更新一个 AI 工具")
    h_add.add_argument("--key", required=True)
    h_add.add_argument("--name")
    h_add.add_argument("--session-root", default="")
    h_add.add_argument("--mcp-mounted", action="store_true")
    h_add.add_argument("--harvest", action="store_true")
    h_add.add_argument("--notes", default="")
    hsub.add_parser("list", help="已登记工具 + 已知清单")
    hsub.add_parser("catalog", help="已知清单（不是封闭世界）")

    p_verify = sub.add_parser("verify", help="验收：默认检索不把过期当现行")
    p_verify.add_argument("--expect-query", action="append", default=[])

    kept, data_dir, as_json, as_version = _peel_globals(argv)
    if as_version:
        print(f"eric-memory {__version__}")
        return 0
    args = parser.parse_args(kept)
    if data_dir:
        args.data_dir = data_dir
    if as_json:
        args.json = True
    service = _service(args)
    try:
        return _dispatch(service, args)
    except (ValueError, KeyError, FileNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        service.close()


def _dispatch(service: MemoryService, args: argparse.Namespace) -> int:
    as_json = bool(args.json)
    if args.command == "init":
        _print(
            service.init(data_dir=args.data_dir, vault_dir=args.vault, tier=args.tier, folders=args.folder),
            as_json,
        )
        return 0
    if args.command == "status":
        _print(service.status(), True)
        return 0
    if args.command == "add":
        _print(
            service.add(
                args.content,
                category=args.category,
                tags=args.tags,
                entities=_split_entities(args.entities),
                as_of=args.as_of,
                trust=args.trust,
                supersedes=args.supersedes,
            ),
            as_json,
        )
        return 0
    if args.command == "search":
        _print(
            service.search(
                args.query,
                limit=args.limit,
                include_deprecated=args.include_deprecated,
                scope=args.scope,
                project=args.project,
                workspace=args.workspace,
            ),
            as_json,
        )
        return 0
    if args.command == "deprecate":
        _print(
            service.deprecate(args.fact_id, superseded_by=args.superseded_by, reason=args.reason),
            as_json,
        )
        return 0
    if args.command == "sync":
        _print(service.sync(actor=args.actor), as_json)
        return 0
    if args.command == "import":
        _print(service.import_holograph(args.source), as_json)
        return 0
    if args.command == "index-files":
        _print(service.index_files(args.folder), as_json)
        return 0
    if args.command == "harness":
        if args.harness_cmd == "add":
            _print(
                service.harness_add(
                    args.key,
                    display_name=args.name,
                    session_root=args.session_root,
                    mcp_mounted=args.mcp_mounted,
                    harvest_ok=args.harvest,
                    notes=args.notes,
                ),
                as_json,
            )
            return 0
        if args.harness_cmd == "list":
            _print(service.harness_list(), True)
            return 0
        if args.harness_cmd == "catalog":
            _print(service.catalog(), True)
            return 0
    if args.command == "verify":
        queries = args.expect_query or ["刘昱铄", "青云"]
        payload = service.verify(queries)
        _print(payload, True)
        return 0 if payload["ok"] else 1
    raise SystemExit(f"unknown command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
