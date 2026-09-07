[English](en/cli-mcp.md) · [中文](CLI与MCP.md)

# CLI 与 stdio MCP

两个入口调用同一套请求校验和 service。可选投影失败时，数据库仍是权威真值。

CLI 的标准输出与错误输出统一使用 UTF-8，脚本应按 UTF-8 解码，包括 Windows 管道。

## CLI 通用形式

```text
eric-memory [--data-dir 绝对路径] [--json] COMMAND ...
```

持久化路径必须是绝对路径。`--version` 不打开数据库；只读命令不会创建缺失数据库。

## 命令表

| 区域 | 命令 |
| --- | --- |
| Library | `init`, `status`, `doctor`, `verify` |
| Fact | `add`, `search`, `deprecate`, `history`, `export` |
| 候选与审阅 | `candidate add/list/show`, `review` |
| 来源 | `source approve/list/revoke/scan`, `sync`, 兼容 `index-files` |
| 身份 | `harness add/list/catalog/grant/revoke` |
| 恢复 | `migrate --plan/--apply/--rollback`, `backup create/list/verify/prune/remove/remove`, `restore --from`, `purge` |
| 运维 | `support-bundle`, `update check/apply`, `mcp --principal KEY` |
| 导入 | 兼容 `import holograph --source 绝对路径` |

精确参数及限制请运行 `eric-memory COMMAND --help`。

## 检索与 scope

```bash
eric-memory --data-dir /绝对路径/data search "查询" --scope user --limit 10
eric-memory --data-dir /绝对路径/data search "查询" --scope project --project "With"
eric-memory --data-dir /绝对路径/data search "查询" --scope workspace --workspace "/绝对路径/workspace"
```

query 为 1–256 字符，limit 为 1–100；`%` / `_` 按普通字符处理。`scope=user` 返回该身份获准的全部 scope；project/workspace 必须给准确 key。结果保留旧字段并新增 `fact_uid`、`score`、`matched_by`、结构化 `scope`、`source_count`。历史需要 `history:read`。

## Candidate 与本地审阅

本地手工 candidate 可以不带来源：

```bash
eric-memory --data-dir /绝对路径/data candidate add \
  --content "一到三句、最多 1,200 个 Unicode 字符。" \
  --entities "实体A,实体B" --category project --confidence 0.8 --scope user
eric-memory --data-dir /绝对路径/data review
```

审阅会展示来源、scope、相关 active 事实与冲突；每批最多 50 条。accept 可在同一事务里显式 supersede 旧整数 fact ID；reject 会立即清除 candidate 正文。

`add` / `deprecate` 继续保留为本地管理员或特殊 direct-writer 兼容面。普通 harness 默认只交 candidate。

## Principal 与来源配置

```bash
eric-memory --data-dir /绝对路径/data harness add --key cursor --name Cursor
eric-memory --data-dir /绝对路径/data source approve /绝对路径/approved-root --harness cursor \
  --include "*.md" --exclude ".git" --type md --max-file-bytes 5242880
```

批准来源只能在本地交互式执行。OS 根目录被拒绝；整个主目录需要输入完整路径确认。撤销会让未完成 run 失效并清除索引/游标。

## MCP 启动

已安装可执行文件：

```text
eric-memory --data-dir /绝对路径/data mcp --principal cursor
```

源码 checkout 兼容 shim：

```text
python3 /绝对路径/with-memory/mcp/server.py --data-dir /绝对路径/data --principal cursor
```

v1 只启用 stdio，不配置 HTTP URL。锁定的官方 MCP Python SDK 使用换行分隔 JSON-RPC，并处理现代/旧协议协商；旧 `mcp/server.py` 路径在整个 v1 保留兼容。

不带 `--principal` 时进入 `legacy`，可见工具严格只有 `memory_status` 与 `memory_search`。普通已登记 harness 只会看到其 capability 对应工具。

## MCP 工具与能力

| 工具 | Capability | 说明 |
| --- | --- | --- |
| `memory_status` | `status:read` | 脱敏健康状态 |
| `memory_search` | `fact:search` | 获准 active scope；历史还需 `history:read` |
| `memory_candidate_add` | `candidate:submit` | 必须给正文、稳定 `submission_uid`、获准 `source_uid` 与 `source_locator` |
| `memory_candidate_list` | `candidate:read-own` | 不能读别的 principal |
| `memory_source_list` | `source:scan` | 只列分配且获准来源 |
| `memory_harvest_begin` | `source:scan` | 返回 run UID 与变化文件元数据，上限 100,000 |
| `memory_harvest_complete` | `source:scan` | 提交成功游标 |
| `memory_history` | `history:read` | 取代链与脱敏 audit |
| `memory_add`, `memory_deprecate` | `fact:write` | 特殊授权兼容入口 |
| `memory_sync` | `sync:run` | 扫描获准来源并修复投影 |
| import/index/harness 工具 | `manage` | 普通 harness 不可见 |

所有 input/output 都有严格 JSON Schema、参数限制、稳定错误码与 `additionalProperties:false`。

## Harvest 协议

1. 调用 `memory_harvest_begin(source_uid)`。
2. 只读取返回的变化路径，且必须仍在已批准 canonical root 内。
3. 用稳定 submission UID 和 source locator 提交 candidate。
4. 全部处理成功后才调用 `memory_harvest_complete(run_uid, cursor)`。

数据库保存文件元数据、摘要、游标和 candidate 指针，不保存来源正文。

## 稳定错误

MCP 失败返回 `isError=true` 和结构化 `{code,message,details}`。重点包括 `PERMISSION_REQUIRED`、验证、冲突/不存在、内容策略拒绝、来源撤销/truncated 与完整性错误。不要通过放宽 scope 或参数重试越权请求；应修正请求或由本地管理员授权。
