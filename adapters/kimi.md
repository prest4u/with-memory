# Kimi Code

本机常见会话根（先确认存在，再请用户点头）：

```text
/Users/<name>/.kimi/sessions
```

会话文件常见为 `wire.jsonl`、`context.jsonl`。每日任务最多把该目录当资料夹索引，禁止把 jsonl 全文写成事实。

```bash
python3 "$REPO/bin/eric-memory" --data-dir "$DATA" harness add \
  --key kimi --name "Kimi Code" \
  --session-root /Users/<name>/.kimi/sessions \
  --harvest
```

大陆提示：C 端新购曾暂停。以用户本机是否已装为准，不要引导去未授权渠道。
