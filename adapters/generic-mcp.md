# 通用 MCP 挂载

所有 harness 共用同一条 stdio 服务器：

```text
python3 /ABS/with-memory/mcp/server.py --data-dir /ABS/eric-memory-data --principal HARNESS_KEY
```

Windows：

```text
py -3 C:\ABS\with-memory\mcp\server.py --data-dir C:\ABS\eric-memory-data --principal HARNESS_KEY
```

服务器由锁定的官方 MCP Python SDK v2 提供 stdio 换行 JSON-RPC；不监听 HTTP。工具名以 `memory_` 开头，`tools/list` 会按 principal 权限过滤。不带 `--principal` 时只有 legacy 的 status/search。

不知道某工具的会话路径时：

1. 先用本地 CLI `harness add --key HARNESS_KEY` 登记该工具（无 `--harvest`）
2. 把这段 MCP 配置贴进该工具的设置
3. 把 `skills/严格技能.md` 交给它

不要为了「找路径」扫描整个用户目录。

有用户明确批准的目录时，必须另走本地同意入口：

```text
eric-memory --data-dir /ABS/eric-memory-data source approve /ABS/SOURCE --harness HARNESS_KEY
```

harness 默认提交 `memory_candidate_add`，不能直接写 active。
