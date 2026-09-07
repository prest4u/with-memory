[English](add-harness.md) · [中文](../添加AI工具.md)

# Quest: add one harness

Reuse the existing data directory. Ask for a stable key, stdio MCP support, and an explicitly consented absolute source path plus rules. Unknown means no harvest.

Register locally:

```bash
eric-memory --data-dir "$DATA" harness add --key "$KEY" --name "$NAME"
```

Configure the host with `eric-memory --data-dir /ABS/data mcp --principal KEY`. Add `--mcp-mounted` only after changing the real host config. Approve any source separately with local `source approve PATH --harness KEY`; compatibility flags never grant consent.

Give the harness `skills/严格技能.md`. Smoke `memory_status`, search, assigned-source begin/complete, and candidate submission. Confirm direct write is hidden or returns `PERMISSION_REQUIRED`. The owner decides the test candidate in local `review`.
