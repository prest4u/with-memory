# Cursor

## CLI

Cursor Agent 直接调用：

```bash
python3 /Users/eric/Documents/eric-memory/bin/eric-memory --data-dir /Users/eric/eric-memory-data search "……"
```

## MCP

在 Cursor 的 MCP 设置里增加 stdio 服务器（路径按本机改）：

```json
{
  "mcpServers": {
    "eric-memory": {
      "command": "python3",
      "args": [
        "/Users/eric/Documents/eric-memory/mcp/server.py",
        "--data-dir",
        "/Users/eric/eric-memory-data"
      ]
    }
  }
}
```

写入仓库 `.cursor/mcp.json` 或本机 `~/.cursor/mcp.json`（空文件就直接写成上面这段）。新开一轮 Cursor 对话后应能看到 `memory_search`。

把 `skills/严格技能.md` 放进项目或用户规则。不要把整个 Cursor 工程目录登记为可收割会话源，除非用户明确点头。
