# Working context

With. can keep selected working documents in an optional local cache and return
small, source-linked excerpts. This native Python/SQLite implementation adds no
runtime dependency and uses the existing CLI and stdio MCP server.

The feature is opt-in. Existing installations continue to expose their previous
tools until an administrator grants the new capabilities. The durable
`memory_search` API and schema-v2 facts are unchanged.

## Enable a software project

Use the installed `eric-memory` executable, or replace it with the absolute
virtual-environment Python and `bin/eric-memory` paths. Do not use a system Python
that lacks the package's locked MCP dependency. Replace the example paths with
existing absolute paths. Run the following administrator commands locally:

```sh
eric-memory --data-dir /ABS/data harness add --key codex --name Codex
eric-memory --data-dir /ABS/data source approve /ABS/project/captured --harness codex
```

Read `source_uid` from that command's result. Enabling temporary **source-body**
storage is separate from metadata/source approval:

```sh
eric-memory --data-dir /ABS/data context enable --project demo --source-uid SOURCE_UUID --allow-content-storage
eric-memory --data-dir /ABS/data harness grant --key codex --capability context:read --scope-kind project --scope-key demo
eric-memory --data-dir /ABS/data harness grant --key codex --capability context:write --scope-kind project --scope-key demo
```

To include durable facts from the same project, also grant its fact-search scope:

```sh
eric-memory --data-dir /ABS/data harness grant --key codex --capability fact:search --scope-kind project --scope-key demo
```

Restart the MCP client after changing tool grants. Use the existing server entry
with `--principal codex`; adding a second MCP server is unnecessary.

## Capture before the model reads the raw output

Use the host's approved command runner to save stdout/stderr into an approved
file, preserving the command's exit status separately. Then call:

```json
{"project":"demo","source_uid":"SOURCE_UUID","source_locator":"build.log","label":"Build output"}
```

Tool: `memory_context_index`. It reads the file locally and returns only a receipt
with a document ID, SHA-256, byte count and expiry. It executes no command and
performs no network request. Inputs are stable UTF-8 files with relative POSIX
locators, including on Windows. The source's include/exclude/type/size rules and
owning principal still apply. An optional `expected_sha256` rejects changed input.

For clients with a command runner but no MCP, the same operation is available as:

```sh
eric-memory --data-dir /ABS/data context index --principal codex --project demo --source-uid SOURCE_UUID --source-locator build.log
eric-memory --data-dir /ABS/data context recall --principal codex --project demo "compiler error" --max-bytes 8192
```

Running a raw-output tool first and indexing its response afterward does not
remove text already in the conversation. This release supplies explicit capture
and retrieval; it does not intercept every host tool or install automatic hooks.

## Retrieve and resume

`memory_recall(query, project, limit=6, max_bytes=8192)` interleaves relevant active
project facts and temporary document excerpts. Results identify their kind and
status. If the caller lacks project fact-search permission, `facts_access` is
false and only authorized context results are returned. User-wide preferences
remain accessible through the original `memory_search` tool.

`memory_context_read(project, artifact_uid, start_line=1, start_column=1,
line_count=40, max_bytes=8192)` returns exact captured text with line coordinates.
Follow `next_line` and `next_column` to continue, including across a long JSON
line or multibyte text. Columns count Unicode characters starting at one. A null
cursor means end of document. `max_bytes` bounds compact UTF-8 JSON payload size,
including metadata. MCP framing and its structured/text representations add
transport overhead. No tokenizer or billing saving is inferred from this value.

Excerpts can be truncated; use the read tool before edits or exact quotations.
Documents are dated snapshots. Re-index explicitly after source changes; no
silent refresh occurs. Re-indexing replaces the document ID. An expired or replaced
ID returns `NOT_FOUND`, so capture the current approved file again.

For continuity, a host can write a short task checkpoint to an approved project
file and index it before compaction. After resume, search that project for the
checkpoint and verify source files. This is explicit working-state retrieval;
automatic capture of full transcripts is excluded.

## Share across harnesses

All clients use the same kernel. Any registered principal key works; the feature
does not branch on a fixed list of client names. Grant `context:read` for the
project to a second principal, such as `cursor` or `kimi`, to let it retrieve that
project's temporary documents. Granting read access deliberately shares the
enabled source content within that project. A write grant alone does not allow
indexing a source assigned to another principal.

For independent writers, approve a separate capture directory for each principal,
enable those sources under the same project, and grant each writer read/write
access to that project. Do not reassign an existing source merely to share reads.
Reapproval changes its consent revision and invalidates the old captured content.

The client configuration remains the normal With. stdio command. Use the
[generic adapter](../../adapters/generic-mcp.md), or the existing Codex, Cursor,
Kimi, Claude-compatible, Hermes, Qwen and Grok setup documentation. A remote-only
client without local stdio or filesystem access cannot directly use this local
feature. No HTTP service is introduced.

## Data handling and recovery

- Captured bodies live in `<data-dir>/context/working.sqlite3`, bound to the
  durable library ID. They are excluded from With.'s managed backups, support
  bundles, fact exports and Obsidian projection. The optional cache has its own
  schema and requires no migration of `memory.db`.
- The default lifetime is seven days. Enable accepts `--ttl-days 1..30`. Expired
  documents become unreadable immediately; physical cleanup occurs on indexing,
  `context clean`, source revocation or explicit disable. No cleanup daemon runs.
- Limits are 5 MiB per document, 50 MiB of original UTF-8 bodies and 1,000
  documents per project, and 100 enabled sources per project. SQLite/FTS indexes
  add disk space beyond the original-body quota. Oversized or changing inputs are
  rejected without replacing the previous valid snapshot.
- Existing secret and minor-individual-information controls also apply to
  temporary content. Long ordinary documents are allowed only here. Recognizable
  raw conversation exports are rejected. These checks do not certify arbitrary
    files as free of sensitive information; choose capture sources deliberately.
- Every read checks current project grants, source status and consent revision.
  Revocation takes effect for future reads. Previously returned text in a host's
  conversation cannot be recalled. Captured text is untrusted data and must not
  be treated as tool instructions or proof that a fact has been accepted.
- No content is automatically promoted. Submit a short, source-linked candidate
  through `memory_candidate_add` using the owning principal's approved source;
  the existing local review process determines activation.

Inspect setup with `context status --principal KEY --project demo`. Remove only
expired cache entries with the local administrator's `context clean`. To disable
and remove a project's temporary documents explicitly:

```sh
eric-memory --data-dir /ABS/data context disable --project demo --confirm
```

Revoke the project's `context:read`/`context:write` grants and restart the client
to remove its optional tool exposure. Durable facts remain available. SQLite
secure deletion and DELETE journaling reduce retained managed copies; disk
snapshots, external backups and the original input files are outside this cleanup.

Use `context disable --project demo --source-uid SOURCE_UUID --confirm` to remove
one source only. An empty or invalid source UUID is rejected. If the temporary
database is damaged or belongs to a different library, local administrators can
run `context reset --confirm`. Reset removes **all projects'** temporary documents,
enabled-source settings and SQLite sidecar files, including an unreadable cache.
It leaves durable facts intact. Enable the selected sources again before indexing.
With. processes coordinate cache connections and reset with a local file lock;
clients must use the same supported version. Stop older clients before upgrading.

Restoring a main-database backup or performing an emergency purge clears the
whole temporary cache before the main database changes. If that cleanup fails,
the restore or purge does not proceed. This prevents restored consent revisions
from reviving old documents and prevents purge from leaving a managed body copy.
The purge preview reports this impact. A failed cache cleanup after source
revocation returns `committed: true`, `context_cleanup: pending` and a warning:
source access has already been revoked. Retry revocation or use the local reset.

## Verification status

The added tests cover the actual With. server over the official stdio MCP SDK
with Codex, Cursor, Kimi, Claude, Hermes, Qwen, Grok and an arbitrary custom
principal. This is protocol testing with different identities, not a claim that
all eight client applications ran. Tests also cover CLI operation from another
directory, Unicode/spaced paths, exact paging, source changes, expiry,
concurrency, denied inputs and authorization changes.

On 2026-09-08, Cursor CLI on macOS also loaded the installed wheel through its
normal project MCP configuration and discovered all three context tools. A
separate installed-wheel smoke passed CLI and stdio index/recall/exact-read and
restart tests from an unrelated directory. Cursor's model-driven task routing
and the other client applications still require end-to-end acceptance.

The existing CI matrix runs the same tests on Python 3.10/3.12 on Linux, Windows,
macOS arm64 and macOS x86_64. A configured matrix is not a completed run. Actual
client application tests, clean-machine installs and signed release requirements
remain governed by the [release process](release-process.md).
