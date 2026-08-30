# Eric Memory

本仓库是本机记忆系统。真值在 SQLite，Obsidian 只是投影。

开工前先读 [skills/严格技能.md](skills/严格技能.md)，再调用官方入口：

```text
python3 bin/eric-memory --data-dir /Users/eric/eric-memory-data search "$QUERY"
python3 bin/eric-memory --data-dir /Users/eric/eric-memory-data add --content "1–3句指针" --entities "实体"
python3 bin/eric-memory --data-dir /Users/eric/eric-memory-data deprecate ID --superseded-by NEW --reason "……"
python3 bin/eric-memory --data-dir /Users/eric/eric-memory-data sync
```

数据目录以本机 `eric-memory-data` 或安装时写下的绝对路径为准。不要私自改 `memory.db`，不要把过期条当成现行，不要扫未登记目录。
