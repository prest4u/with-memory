"""Official With. CLI. All state changes delegate to MemoryService."""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path
from typing import Any

from . import __version__
from .errors import ConfirmationRequiredError, MemoryError, ValidationError, public_error
from .paths import atomic_write_text, expand_once, resolve_data_dir
from .service import MemoryService


def _print(payload: Any, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    if isinstance(payload, dict) and "facts" in payload:
        print(f"layer={payload.get('layer')} results={len(payload['facts'])}")
        for fact in payload["facts"]:
            mark = "现行" if fact["status"] == "active" else "过期"
            print(f"[{fact['fact_id']}] {mark} {fact['category']} {fact['as_of']} score={fact.get('score', 0):.4f}")
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
        for old in payload.get("deprecated", []) if isinstance(payload.get("deprecated"), list) else []:
            print(f"deprecated #{old['fact_id']} -> #{fact['fact_id']}")
        if isinstance(payload.get("deprecated"), dict):
            print(f"deprecated #{payload['deprecated']['fact_id']} -> #{fact['fact_id']}")
        if payload.get("projection") == "dirty":
            print("warning: database committed; Obsidian projection is dirty", file=sys.stderr)
        return
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _split_entities(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [part.strip() for part in raw.replace("，", ",").split(",") if part.strip()]


def _split_ints(raw: str | None) -> list[int]:
    if not raw:
        return []
    try:
        return [int(item.strip()) for item in raw.replace("，", ",").split(",") if item.strip()]
    except ValueError as exc:
        raise ValueError("fact IDs must be comma-separated integers") from exc


def _peel_globals(argv: list[str] | None) -> tuple[list[str], str | None, bool, bool]:
    raw = list(sys.argv[1:] if argv is None else argv)
    data_dir: str | None = None
    as_json = False
    as_version = False
    kept: list[str] = []
    index = 0
    while index < len(raw):
        argument = raw[index]
        if argument in {"--version", "-V"}:
            as_version = True
            index += 1
            continue
        if argument == "--json":
            as_json = True
            index += 1
            continue
        if argument == "--data-dir" and index + 1 < len(raw):
            data_dir = raw[index + 1]
            index += 2
            continue
        if argument.startswith("--data-dir="):
            data_dir = argument.split("=", 1)[1]
            index += 1
            continue
        kept.append(argument)
        index += 1
    return kept, data_dir, as_json, as_version


def _add_scope_arguments(
    parser: argparse.ArgumentParser,
    *,
    default: str | None = "user",
) -> None:
    parser.add_argument("--scope", choices=("user", "workspace", "project"), default=default)
    parser.add_argument("--project")
    parser.add_argument("--workspace")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="eric-memory",
        description="With.：本地优先、候选先审、作废不删的个人记忆系统。",
    )
    parser.add_argument("--data-dir", help="绝对数据目录；仅命令行输入允许 ~")
    parser.add_argument("--json", action="store_true", help="机器可读输出")
    parser.add_argument("--version", "-V", action="store_true", help="打印版本后退出")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="创建 schema-v2 数据库和可选 Obsidian 投影")
    init.add_argument("--vault")
    init.add_argument("--tier", choices=("simple", "full"), default="simple")
    init.add_argument("--folder", action="append", default=[])
    init.add_argument("--no-obsidian", action="store_true")

    sub.add_parser("status", help="只读状态")
    sub.add_parser("doctor", help="只读完整健康检查")

    context = sub.add_parser("context", help="项目临时资料与限量检索；需单独启用原文存储")
    context_sub = context.add_subparsers(dest="context_cmd", required=True)
    context_enable = context_sub.add_parser("enable", help="本地管理员允许一个来源保存临时原文")
    context_enable.add_argument("--project", required=True)
    context_enable.add_argument("--source-uid", required=True)
    context_enable.add_argument("--ttl-days", type=int, default=7)
    context_enable.add_argument("--allow-content-storage", action="store_true")
    context_disable = context_sub.add_parser("disable", help="关闭项目临时存储并删除对应缓存")
    context_disable.add_argument("--project", required=True)
    context_disable.add_argument("--source-uid")
    context_disable.add_argument("--confirm", action="store_true")
    context_sub.add_parser("clean", help="删除已经过期的临时资料")
    context_reset = context_sub.add_parser("reset", help="移除所有项目的临时库及启用记录，用于损坏恢复")
    context_reset.add_argument("--confirm", action="store_true")
    for name in ("status", "index", "recall", "read"):
        item = context_sub.add_parser(name)
        item.add_argument("--project", required=True)
        item.add_argument("--principal", default="legacy", help="使用已登记并获准的客户端身份")
        if name == "index":
            item.add_argument("--source-uid", required=True)
            item.add_argument("--source-locator", required=True)
            item.add_argument("--label", default="")
            item.add_argument("--expected-sha256")
        if name == "recall":
            item.add_argument("query")
            item.add_argument("--limit", type=int, default=6)
        if name == "read":
            item.add_argument("artifact_uid")
            item.add_argument("--start-line", type=int, default=1)
            item.add_argument("--start-column", type=int, default=1)
            item.add_argument("--line-count", type=int, default=40)
        if name in {"read", "recall"}:
            item.add_argument("--max-bytes", type=int, default=8192)

    add = sub.add_parser("add", help="本地管理员直接写入现行事实")
    add.add_argument("--content", required=True)
    add.add_argument("--category", default="general")
    add.add_argument("--tags", default="")
    add.add_argument("--entities", default="")
    add.add_argument("--as-of")
    add.add_argument("--trust", type=float, default=0.5)
    add.add_argument("--supersedes", type=int)
    _add_scope_arguments(add, default=None)

    search = sub.add_parser("search", help="检索获准 scope 的现行事实")
    search.add_argument("query")
    search.add_argument("--limit", type=int, default=10)
    search.add_argument("--include-deprecated", action="store_true")
    search.add_argument("--no-files", action="store_true")
    _add_scope_arguments(search)

    deprecate = sub.add_parser("deprecate", help="作废事实，不删除")
    deprecate.add_argument("fact_id", type=int)
    deprecate.add_argument("--superseded-by", type=int)
    deprecate.add_argument("--reason", default="")

    history = sub.add_parser("history", help="读取事实取代链和审计元数据")
    history.add_argument("fact_id", type=int)

    sync = sub.add_parser("sync", help="增量扫描来源并修复可选投影")
    sync.add_argument("--projection-only", action="store_true", help="仅修复 Obsidian 投影，不推进来源收割进度")

    import_parser = sub.add_parser("import", help="从只读 Holograph 库导入")
    import_parser.add_argument("source_kind", choices=("holograph",))
    import_parser.add_argument("--source", required=True)

    index = sub.add_parser("index-files", help="兼容命令：扫描已批准资料夹")
    index.add_argument("--folder")

    harness = sub.add_parser("harness", help="harness 身份和授权")
    harness_sub = harness.add_subparsers(dest="harness_cmd", required=True)
    harness_add = harness_sub.add_parser("add")
    harness_add.add_argument("--key", required=True)
    harness_add.add_argument("--name")
    harness_add.add_argument("--session-root")
    harness_add.add_argument("--mcp-mounted", action=argparse.BooleanOptionalAction, default=None)
    harness_add.add_argument("--harvest", action=argparse.BooleanOptionalAction, default=None)
    harness_add.add_argument("--notes")
    harness_sub.add_parser("list")
    harness_sub.add_parser("catalog")
    grant = harness_sub.add_parser("grant")
    grant.add_argument("--key", required=True)
    grant.add_argument("--capability", required=True)
    grant.add_argument("--scope-kind", choices=("user", "project", "workspace"))
    grant.add_argument("--scope-key")
    revoke_grant = harness_sub.add_parser("revoke")
    revoke_grant.add_argument("--key", required=True)
    revoke_grant.add_argument("--capability", required=True)
    revoke_grant.add_argument("--scope-kind", choices=("user", "project", "workspace"))
    revoke_grant.add_argument("--scope-key")

    candidate = sub.add_parser("candidate", help="候选提交与查看")
    candidate_sub = candidate.add_subparsers(dest="candidate_cmd", required=True)
    candidate_add = candidate_sub.add_parser("add")
    candidate_add.add_argument("--content", required=True)
    candidate_add.add_argument("--submission-uid")
    candidate_add.add_argument("--category", default="general")
    candidate_add.add_argument("--entities", default="")
    candidate_add.add_argument("--as-of")
    candidate_add.add_argument("--confidence", type=float, default=0.5)
    candidate_add.add_argument("--source-uid")
    candidate_add.add_argument("--source-locator", default="")
    _add_scope_arguments(candidate_add)
    candidate_list = candidate_sub.add_parser("list")
    candidate_list.add_argument("--status", default="pending")
    candidate_list.add_argument("--limit", type=int, default=100)
    candidate_show = candidate_sub.add_parser("show")
    candidate_show.add_argument("candidate_uid")

    review = sub.add_parser("review", help="本地交互式候选审阅（单批最多 50）")
    review.add_argument("--candidate")
    action = review.add_mutually_exclusive_group()
    action.add_argument("--accept", action="store_true")
    action.add_argument("--reject", action="store_true")
    review.add_argument("--supersedes")
    review.add_argument("--reason", default="")
    review.add_argument("--limit", type=int, default=50)

    source = sub.add_parser("source", help="来源同意、撤销和扫描")
    source_sub = source.add_subparsers(dest="source_cmd", required=True)
    source_approve = source_sub.add_parser("approve")
    source_approve.add_argument("root")
    source_approve.add_argument("--harness", default="")
    source_approve.add_argument("--include", action="append", default=[])
    source_approve.add_argument("--exclude", action="append", default=[])
    source_approve.add_argument("--type", action="append", default=[])
    source_approve.add_argument("--max-file-bytes", type=int, default=50 * 1024 * 1024)
    source_list = source_sub.add_parser("list")
    source_list.add_argument("--include-revoked", action="store_true")
    source_revoke = source_sub.add_parser("revoke")
    source_revoke.add_argument("source_uid")
    source_scan = source_sub.add_parser("scan")
    source_scan.add_argument("source_uid")
    source_scan.add_argument("--max-files", type=int, default=100_000)

    harvest = sub.add_parser("harvest", help="分两步收割；候选全部提交成功后才确认进度")
    harvest_sub = harvest.add_subparsers(dest="harvest_cmd", required=True)
    harvest_begin = harvest_sub.add_parser("begin")
    harvest_begin.add_argument("source_uid")
    harvest_begin.add_argument("--max-files", type=int, default=100_000)
    harvest_complete = harvest_sub.add_parser("complete")
    harvest_complete.add_argument("run_uid")
    harvest_complete.add_argument("--cursor", required=True)

    migrate = sub.add_parser("migrate", help="旁路迁移或精确回滚")
    migrate_action = migrate.add_mutually_exclusive_group(required=True)
    migrate_action.add_argument("--plan", action="store_true")
    migrate_action.add_argument("--apply", action="store_true")
    migrate_action.add_argument("--rollback", action="store_true")
    migrate.add_argument("--incremental-export")

    backup = sub.add_parser("backup", help="SQLite 备份管理")
    backup_sub = backup.add_subparsers(dest="backup_cmd", required=True)
    backup_sub.add_parser("create")
    backup_sub.add_parser("list")
    verify_backup = backup_sub.add_parser("verify")
    verify_backup.add_argument("path")
    backup_sub.add_parser("prune")
    remove_backup = backup_sub.add_parser("remove", help="删除明确指定的托管备份")
    remove_backup.add_argument("paths", nargs="+")

    restore = sub.add_parser("restore", help="校验、快照后原子恢复")
    restore.add_argument("--from", dest="from_path", required=True)

    purge = sub.add_parser("purge", help="交互式紧急清除")
    purge.add_argument("fact_id", type=int)

    export = sub.add_parser("export", help="导出事实")
    export.add_argument("--format", choices=("jsonl", "markdown"), default="jsonl")
    export.add_argument("--active-only", action="store_true")
    export.add_argument("--output")

    support = sub.add_parser("support-bundle", help="导出默认无事实、无路径的支持包")
    support.add_argument("--output", required=True)

    verify = sub.add_parser("verify", help="验证 deprecated/scope 泄漏")
    verify.add_argument("--expect-query", action="append", default=[])

    mcp = sub.add_parser("mcp", help="启动官方 stdio MCP 服务")
    mcp.add_argument("--principal")

    update = sub.add_parser("update", help="仅在显式调用时访问 GitHub")
    update_sub = update.add_subparsers(dest="update_cmd", required=True)
    update_sub.add_parser("check")
    update_sub.add_parser("apply")
    return parser


def _mode_for(args: argparse.Namespace) -> str:
    if args.command == "init":
        return "create"
    if args.command == "context":
        # Context writes target only working.sqlite3; the durable DB stays read-only.
        return "ro"
    if args.command in {"status", "doctor", "search", "history", "export", "verify"}:
        return "ro"
    if args.command == "source" and args.source_cmd == "list":
        return "ro"
    if args.command == "harness" and args.harness_cmd in {"list", "catalog"}:
        return "ro"
    if args.command == "backup" and args.backup_cmd in {"list", "verify"}:
        return "ro"
    if args.command == "migrate" and args.plan:
        return "ro"
    return "rw-existing"


def _interactive_review(service: MemoryService, args: argparse.Namespace, as_json: bool) -> int:
    if args.limit < 1 or args.limit > 50:
        raise ValidationError("review limit must be between 1 and 50")
    if args.candidate:
        candidates = [service.candidate_show(args.candidate)["candidate"]]
    else:
        candidates = service.candidate_list(status="pending", limit=args.limit)["candidates"]
    results: list[dict[str, Any]] = []
    for candidate in candidates:
        uid = candidate["candidate_uid"]
        context = service.review_context(uid)
        if as_json and (args.accept or args.reject):
            pass
        else:
            print(json.dumps(context, ensure_ascii=False, indent=2))
        accept = args.accept
        reject = args.reject
        if not accept and not reject:
            answer = input("[a]ccept / [r]eject / [s]kip / [q]uit: ").strip().lower()
            if answer.startswith("q"):
                break
            if answer.startswith("s") or not answer:
                continue
            accept = answer.startswith("a")
            reject = answer.startswith("r")
        if accept:
            supersedes = _split_ints(args.supersedes)
            if not args.supersedes and not as_json:
                supersedes = _split_ints(input("supersede fact IDs (comma-separated, blank for none): "))
            results.append(service.review_accept(uid, supersedes=supersedes))
        elif reject:
            reason = args.reason or (input("rejection reason: ") if not as_json else "")
            results.append(service.review_reject(uid, reason=reason))
    _print({"reviewed": len(results), "results": results}, as_json)
    return 0


def _dispatch(service: MemoryService, args: argparse.Namespace) -> int:
    as_json = bool(args.json)
    if args.command == "context":
        context = service.context
        if args.context_cmd == "enable":
            payload = context.enable(
                args.project, args.source_uid, ttl_days=args.ttl_days, allow_content_storage=args.allow_content_storage
            )
        elif args.context_cmd == "disable":
            if not args.confirm:
                raise ConfirmationRequiredError("context disable deletes captured documents; pass --confirm")
            payload = context.disable(args.project, args.source_uid)
        elif args.context_cmd == "clean":
            payload = context.clean()
        elif args.context_cmd == "reset":
            if not args.confirm:
                raise ConfirmationRequiredError("context reset removes all temporary projects; pass --confirm")
            payload = context.reset()
        elif args.context_cmd == "status":
            payload = context.status(args.project)
        elif args.context_cmd == "index":
            payload = context.index(
                args.project,
                args.source_uid,
                args.source_locator,
                label=args.label,
                expected_sha256=args.expected_sha256,
            )
        elif args.context_cmd == "recall":
            payload = context.recall(args.query, args.project, limit=args.limit, max_bytes=args.max_bytes)
        else:
            payload = context.read(
                args.project,
                args.artifact_uid,
                start_line=args.start_line,
                start_column=args.start_column,
                line_count=args.line_count,
                max_bytes=args.max_bytes,
            )
        print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
        return 0
    if args.command == "init":
        payload = service.init(
            data_dir=args.data_dir,
            vault_dir=args.vault,
            tier=args.tier,
            folders=args.folder,
            obsidian_enabled=not args.no_obsidian,
        )
        _print(payload, as_json)
        return 0
    if args.command == "status":
        _print(service.status(), True)
        return 0
    if args.command == "doctor":
        payload = service.doctor()
        _print(payload, True)
        return 0 if payload["ok"] else 1
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
                scope=args.scope,
                project=args.project,
                workspace=args.workspace,
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
                include_files=not args.no_files,
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
    if args.command == "history":
        _print(service.history(args.fact_id), True)
        return 0
    if args.command == "sync":
        _print(service.sync(projection_only=args.projection_only), as_json)
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
        elif args.harness_cmd == "list":
            _print(service.harness_list(), True)
        elif args.harness_cmd == "catalog":
            _print(service.catalog(), True)
        elif args.harness_cmd == "grant":
            _print(
                service.harness_grant(
                    args.key,
                    args.capability,
                    scope=args.scope_kind,
                    scope_key=args.scope_key,
                ),
                True,
            )
        else:
            _print(
                service.harness_revoke_grant(
                    args.key,
                    args.capability,
                    scope=args.scope_kind,
                    scope_key=args.scope_key,
                ),
                True,
            )
        return 0
    if args.command == "candidate":
        if args.candidate_cmd == "add":
            _print(
                service.candidate_add(
                    content=args.content,
                    submission_uid=args.submission_uid or str(uuid.uuid4()),
                    category=args.category,
                    entities=_split_entities(args.entities),
                    as_of=args.as_of,
                    confidence=args.confidence,
                    scope=args.scope,
                    project=args.project,
                    workspace=args.workspace,
                    source_uid=args.source_uid,
                    source_locator=args.source_locator,
                    manual_input=args.source_uid is None,
                ),
                as_json,
            )
        elif args.candidate_cmd == "list":
            status = None if args.status == "all" else args.status
            _print(service.candidate_list(status=status, limit=args.limit), True)
        else:
            _print(service.candidate_show(args.candidate_uid), True)
        return 0
    if args.command == "review":
        return _interactive_review(service, args, as_json)
    if args.command == "source":
        if args.source_cmd == "approve":
            requested = expand_once(args.root, name="source root", resolve=False)
            root = requested.resolve(strict=True)
            confirmation = None
            if root == Path.home().resolve():
                print(f"Approving the complete home directory is broad: {root}", file=sys.stderr)
                confirmation = input("Type the complete canonical path to confirm: ").strip()
            _print(
                service.source_approve(
                    args.root,
                    harness_key=args.harness,
                    include=args.include,
                    exclude=args.exclude,
                    file_types=args.type,
                    max_file_bytes=args.max_file_bytes,
                    home_confirmation=confirmation,
                ),
                True,
            )
        elif args.source_cmd == "list":
            _print(service.source_list(include_revoked=args.include_revoked), True)
        elif args.source_cmd == "revoke":
            _print(service.source_revoke(args.source_uid), True)
        else:
            _print(service.source_scan(args.source_uid, max_files=args.max_files), True)
        return 0
    if args.command == "harvest":
        if args.harvest_cmd == "begin":
            _print(service.harvest_begin(args.source_uid, max_files=args.max_files), True)
        else:
            _print(service.harvest_complete(args.run_uid, cursor=args.cursor), True)
        return 0
    if args.command == "migrate":
        if args.plan:
            _print(service.migrate_plan(), True)
        elif args.apply:
            _print(service.migrate_apply(), True)
        else:
            plan = service.rollback_plan()
            confirm_loss = False
            export_path = args.incremental_export
            if not plan["lossless"]:
                _print(plan, True)
                answer = input("Type ROLLBACK to export v2 changes and restore the v1 backup: ")
                if answer != "ROLLBACK":
                    raise ConfirmationRequiredError("rollback cancelled")
                confirm_loss = True
                if not export_path:
                    export_path = str(resolve_data_dir(args.data_dir) / "exports" / "pre-v1-rollback-increment.jsonl")
            _print(
                service.migrate_rollback(confirm_loss=confirm_loss, incremental_export=export_path),
                True,
            )
        return 0
    if args.command == "backup":
        if args.backup_cmd == "create":
            _print(service.backup_create(), True)
        elif args.backup_cmd == "list":
            _print(service.backup_list(), True)
        elif args.backup_cmd == "verify":
            payload = service.backup_verify(args.path)
            _print(payload, True)
            return 0 if payload["ok"] else 1
        elif args.backup_cmd == "remove":
            print(json.dumps({"managed_backups_to_remove": args.paths}), file=sys.stderr)
            if input("Type REMOVE BACKUPS to continue: ") != "REMOVE BACKUPS":
                raise ConfirmationRequiredError("backup removal cancelled")
            _print(service.backup_remove(args.paths), True)
        else:
            _print(service.backup_prune(), True)
        return 0
    if args.command == "restore":
        print(f"Restore database from {args.from_path}", file=sys.stderr)
        if input("Type RESTORE to continue: ") != "RESTORE":
            raise ConfirmationRequiredError("restore cancelled")
        _print(service.restore(args.from_path), True)
        return 0
    if args.command == "purge":
        plan = service.purge_plan(args.fact_id)
        _print(plan, True)
        confirmation = input("Type the exact fact UID to permanently purge it: ").strip()
        _print(service.purge(args.fact_id, confirmation=confirmation), True)
        return 0
    if args.command == "export":
        payload = service.export(format=args.format, include_deprecated=not args.active_only)
        if args.output:
            path = expand_once(args.output, name="export output")
            atomic_write_text(path, payload.pop("content"), mode=0o600)
            payload["path"] = str(path)
            _print(payload, True)
        elif as_json:
            _print(payload, True)
        else:
            print(payload["content"], end="")
        return 0
    if args.command == "support-bundle":
        _print(service.support_bundle(args.output), True)
        return 0
    if args.command == "verify":
        queries = args.expect_query or ["example-name", "青云"]
        payload = service.verify(queries)
        _print(payload, True)
        return 0 if payload["ok"] else 1
    raise SystemExit(f"unknown command: {args.command}")


def _dispatch_update(args: argparse.Namespace) -> int:
    """The only CLI path allowed to access the network; it never opens SQLite."""
    from .updates import apply_update, check_update, prepare_update

    if args.update_cmd == "check":
        _print(check_update(), True)
        return 0
    plan = prepare_update(getattr(args, "data_dir", None))
    _print(plan, True)
    version = str(plan["release"]["version"])
    answer = input(f"Type APPLY {version} to verify and install this update: ").strip()
    if answer != f"APPLY {version}":
        raise ConfirmationRequiredError("update cancelled")
    _print(
        apply_update(plan, confirmation=answer, data_dir=getattr(args, "data_dir", None)),
        True,
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    # CLI pipes have a stable UTF-8 contract even under a Windows legacy code page.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", newline="\n")
    parser = build_parser()
    kept, data_dir, as_json, as_version = _peel_globals(argv)
    if as_version:
        print(f"eric-memory {__version__}")
        return 0
    args = parser.parse_args(kept)
    if data_dir:
        args.data_dir = data_dir
    if as_json:
        args.json = True
    if args.command == "mcp":
        from .mcp_server import run

        run(getattr(args, "data_dir", None), principal=args.principal or "legacy")
        return 0
    if args.command == "update":
        try:
            return _dispatch_update(args)
        except (
            MemoryError,
            ValueError,
            KeyError,
            FileNotFoundError,
            PermissionError,
            OSError,
        ) as exc:
            payload = {"error": public_error(exc)}
            print(json.dumps(payload, ensure_ascii=False), file=sys.stderr)
            return 1
    service: MemoryService | None = None
    try:
        service = MemoryService(
            getattr(args, "data_dir", None),
            mode=_mode_for(args),  # type: ignore[arg-type]
            principal=getattr(args, "principal", "local"),
        )
        return _dispatch(service, args)
    except (MemoryError, ValueError, KeyError, FileNotFoundError, PermissionError, OSError) as exc:
        payload = {"error": public_error(exc)}
        if bool(getattr(args, "json", False)):
            print(json.dumps(payload, ensure_ascii=False), file=sys.stderr)
        else:
            print(f"error[{payload['error']['code']}]: {payload['error']['message']}", file=sys.stderr)
        return 1
    finally:
        if service is not None:
            service.close()


if __name__ == "__main__":
    raise SystemExit(main())
