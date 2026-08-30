# WorkBuddy / CodeBuddy

没有统一的全局会话根。问用户他们的项目目录，只登记用户指出的绝对路径。

```bash
python3 "$REPO/bin/eric-memory" --data-dir "$DATA" harness add \
  --key workbuddy --name "WorkBuddy" \
  --session-root /ABS/PROJECT \
  --harvest
```

CodeBuddy 用 `--key codebuddy`。两个都要用就登记两次。路径不明时不要扫盘。
