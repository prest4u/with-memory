# Windows / Linux 步骤

第一版的验收机是 macOS。这两套系统按同样合同安装，不要求你现在实测。

## 共同规则

- Python 3.10+
- 数据目录必须是绝对路径
- 不要管理员 / root
- 不要开机守护
- `python3` 在 Windows 上写成 `py -3`

## Windows

```bat
py -3 --version
py -3 C:\ABS\eric-memory\bin\eric-memory --data-dir %USERPROFILE%\eric-memory-data init --tier simple
py -3 C:\ABS\eric-memory\bin\eric-memory --data-dir %USERPROFILE%\eric-memory-data status
py -3 C:\ABS\eric-memory\mcp\server.py --data-dir %USERPROFILE%\eric-memory-data
```

Obsidian 打开 `%USERPROFILE%\eric-memory-data\vault`（或安装时指定的库）。

## Linux

```bash
python3 --version
python3 /ABS/eric-memory/bin/eric-memory --data-dir "$HOME/eric-memory-data" init --tier simple
python3 /ABS/eric-memory/bin/eric-memory --data-dir "$HOME/eric-memory-data" status
python3 /ABS/eric-memory/mcp/server.py --data-dir "$HOME/eric-memory-data"
```

若发行版只有 `python` 且版本 ≥ 3.10，可以替换命令名，不要因此改用 root。

## 任务全文

安装 / 每日同步 / 添加工具仍用 `quests/` 里的中文全文。把其中的 `python3` 按上面替换即可。
