# WorkBuddy / CodeBuddy

没有统一的全局会话根。问用户他们的项目目录，只登记用户指出的绝对路径。

```bash
python3 "$REPO/bin/eric-memory" --data-dir "$DATA" harness add \
  --key workbuddy --name "WorkBuddy"
python3 "$REPO/bin/eric-memory" --data-dir "$DATA" source approve \
  /ABS/PROJECT --harness workbuddy
```

CodeBuddy 用 `--key codebuddy` 和 MCP 参数 `--principal codebuddy`。两个都要用就分别登记、分别批准来源。路径不明时不要扫盘；普通 harness 只提交 candidate。
