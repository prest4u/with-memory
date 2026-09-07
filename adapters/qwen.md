# Qwen Code

典型根（存在才登记）：

```text
$HOME/.qwen
```

有 `chats` 或 `projects` 子目录且用户明确同意，才批准那一层。没有就只靠 CLI / MCP。

```bash
python3 "$REPO/bin/eric-memory" --data-dir "$DATA" harness add \
  --key qwen --name "Qwen Code"
```

用户确认具体子目录后再批准，不要批准整个 `$HOME/.qwen`：

```bash
python3 "$REPO/bin/eric-memory" --data-dir "$DATA" source approve \
  "$HOME/.qwen/chats" --harness qwen
```

MCP 启动参数必须包含 `--principal qwen`；默认写入口是 `memory_candidate_add`。
