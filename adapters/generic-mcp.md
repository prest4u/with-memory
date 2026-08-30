# 通用 MCP 挂载

所有 harness 共用同一条 stdio 服务器：

```text
python3 /ABS/REPO/mcp/server.py --data-dir /ABS/DATA
```

Windows：

```text
py -3 C:\ABS\REPO\mcp\server.py --data-dir C:\ABS\DATA
```

服务器使用 `Content-Length` 帧，工具名以 `memory_` 开头，与 CLI 动词对齐。

不知道某工具的会话路径时：

1. 仍登记该工具（无 `--harvest`）
2. 把这段 MCP 配置贴进该工具的设置
3. 把 `skills/严格技能.md` 交给它

不要为了「找路径」扫描整个用户目录。
