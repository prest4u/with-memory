# Qwen Code

典型根（存在才登记）：

```text
/Users/<name>/.qwen
```

有 `chats` 或 `projects` 子目录就登那一层。没有就不加 `--harvest`，只靠 CLI / MCP。

```bash
python3 "$REPO/bin/eric-memory" --data-dir "$DATA" harness add \
  --key qwen --name "Qwen Code"
```
