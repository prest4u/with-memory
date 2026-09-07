[English](cli-mcp.md) · [中文](../CLI与MCP.md)

# CLI and stdio MCP

Both surfaces call the same request validation and service layer. The database remains authoritative if an optional projection fails.

CLI stdout and stderr use UTF-8, including Windows pipes. Scripts should decode both streams as UTF-8.

## Global CLI form

```text
eric-memory [--data-dir ABS_PATH] [--json] COMMAND ...
```

Persisted paths must be absolute. `--version` does not open a database. Read-only commands never create a missing database.

## Command map

| Area | Commands |
| --- | --- |
| Library | `init`, `status`, `doctor`, `verify` |
| Facts | `add`, `search`, `deprecate`, `history`, `export` |
| Trust loop | `candidate add/list/show`, `review` |
| Sources | `source approve/list/revoke/scan`, `sync`, compatibility `index-files` |
| Identities | `harness add/list/catalog/grant/revoke` |
| Recovery | `migrate --plan/--apply/--rollback`, `backup create/list/verify/prune/remove/remove`, `restore --from`, `purge` |
| Operations | `support-bundle`, `update check/apply`, `mcp --principal KEY` |
| Import | compatibility `import holograph --source ABS_PATH` |

Run `eric-memory COMMAND --help` for exact arguments and bounds.

## Search and scope

```bash
eric-memory --data-dir /ABS/data search "query" --scope user --limit 10
eric-memory --data-dir /ABS/data search "query" --scope project --project "With"
eric-memory --data-dir /ABS/data search "query" --scope workspace --workspace "/ABS/workspace"
```

Queries are 1–256 characters and limits are 1–100. `%` and `_` are literal. `scope=user` returns all scopes authorized for that identity; project/workspace require an exact key. Results preserve legacy fields and add `fact_uid`, `score`, `matched_by`, structured `scope`, and `source_count`. Deprecated history requires `history:read`.

## Candidate and local review

A local manual candidate may omit a source:

```bash
eric-memory --data-dir /ABS/data candidate add \
  --content "One to three sentences, no more than 1,200 Unicode characters." \
  --entities "entity-a,entity-b" --category project --confidence 0.8 --scope user
eric-memory --data-dir /ABS/data review
```

Review shows source, scope, related active facts, and conflicts. A batch is limited to 50. Accept can explicitly supersede old integer fact IDs in the same transaction; reject immediately clears candidate text.

`add` and `deprecate` remain compatible local-admin/direct-writer surfaces. Normal harnesses submit candidates.

## Principal and source setup

```bash
eric-memory --data-dir /ABS/data harness add --key cursor --name Cursor
eric-memory --data-dir /ABS/data source approve /ABS/approved/root --harness cursor \
  --include "*.md" --exclude ".git" --type md --max-file-bytes 5242880
```

Approving a source is local and interactive. OS roots are rejected and a complete home root requires exact path confirmation. Revocation invalidates open runs and clears its index/cursor.

## MCP launch

Installed executable:

```text
eric-memory --data-dir /ABS/data mcp --principal cursor
```

Source checkout compatibility shim:

```text
python3 /ABS/with-memory/mcp/server.py --data-dir /ABS/data --principal cursor
```

v1 enables stdio only. Do not configure an HTTP URL. The official MCP Python SDK handles newline-delimited modern and legacy protocol negotiation. The old `mcp/server.py` path remains a shim for the v1 lifecycle.

Omitting `--principal` selects `legacy`, whose visible tool set is exactly `memory_status` and `memory_search`. A registered normal harness sees tools only for its capabilities.

## MCP tools and required capability

| Tool | Capability | Notes |
| --- | --- | --- |
| `memory_status` | `status:read` | Redacted health/status |
| `memory_search` | `fact:search` | Active authorized scopes; history also needs `history:read` |
| `memory_candidate_add` | `candidate:submit` | Requires content, stable `submission_uid`, approved `source_uid`, and `source_locator` |
| `memory_candidate_list` | `candidate:read-own` | Cannot read another principal's candidates |
| `memory_source_list` | `source:scan` | Only assigned approved sources |
| `memory_harvest_begin` | `source:scan` | Returns a run UID and changed-file metadata, maximum 100,000 |
| `memory_harvest_complete` | `source:scan` | Commits a successful cursor |
| `memory_history` | `history:read` | Supersession chain and redacted audit metadata |
| `memory_add`, `memory_deprecate` | `fact:write` | Compatibility for specially granted principals |
| `memory_sync` | `sync:run` | Scan approved sources and repair projection |
| import/index/harness tools | `manage` | Not visible to normal harnesses |

Every tool input and output has a strict JSON Schema, bounded fields, stable error codes, and `additionalProperties:false`.

## Harvest protocol

1. Call `memory_harvest_begin(source_uid)`.
2. Read only the changed paths returned inside the already-approved canonical root.
3. Submit one or more candidates with stable submission UIDs and source locators.
4. Call `memory_harvest_complete(run_uid, cursor)` only after successful processing.

The database stores file metadata, hashes, cursor, and candidate pointers—not source bodies.

## Stable errors

MCP tool failures return `isError=true` with structured `{code,message,details}`. Important codes include `PERMISSION_REQUIRED`, validation errors, conflict/not-found errors, content-policy rejection, source revoked/truncated, and integrity failures. Do not retry a validation or permission failure with broader arguments; fix the request or obtain a local grant.
