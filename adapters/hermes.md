# Hermes Agent

## 不要做的

- 不要把本系统装进旧的 Holograph 插件树
- 不要改 `~/.hermes/profiles/*/memory_store.db`
- 导入成功前，旧 Holograph 继续可用；成功后新写入走本仓库

## CLI

```bash
python3 /Users/eric/Documents/eric-memory/bin/eric-memory --data-dir /Users/eric/eric-memory-data search "……"
```

## 会话目录

仅当用户点头时登记，例如：

```bash
python3 /Users/eric/Documents/eric-memory/bin/eric-memory --data-dir /Users/eric/eric-memory-data \
  harness add --key hermes --name "Hermes Agent" \
  --session-root /Users/eric/.hermes/profiles/eric \
  --harvest
```

收割只索引文件路径，不会把 `memory_store.db` 再写回旧格式。

## MCP

在 profile 的 `config.yaml` 增加（不要改 `memory.provider: holographic`，旧库继续作归档）：

```yaml
mcp_servers:
  eric-memory:
    command: python3
    args:
      - /Users/eric/Documents/eric-memory/mcp/server.py
      - --data-dir
      - /Users/eric/eric-memory-data
    timeout: 120
```

重启 Hermes 后工具名一般是 `mcp_eric-memory_memory_search` 这类前缀。新写入走这些工具或 CLI，不要再 `INSERT` 进 `memory_store.db`。
