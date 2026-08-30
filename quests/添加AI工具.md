# 任务：添加 AI 工具

内核已经装好。这里只登记一个新的 harness，不重装、不换数据库、不迁就某一家的专有插件。

## 硬限制

- 不要新建第二套数据目录。
- 不要全盘搜索会话文件。路径必须用户给，或用户确认清单里的「典型路径」展开后确实存在。
- 没有会话目录就只挂 CLI / MCP。
- 未展开的 `~` 不准写入。
- GLM 若只是别的工具里的模型，不要单独立一个「GLM harness」，除非用户真的在用 ZCode 或其它独立客户端。

## 1. 问用户

1. 工具名字（可先对照 `eric-memory harness catalog`）。
2. 是否已能在这个工具里运行终端命令。
3. 是否要挂 MCP（stdio：本仓库 `mcp/server.py`）。
4. 有没有允许同步时索引的会话目录（绝对路径）。没有就收割关闭。
5. 大陆限制是否影响它（Kimi 新购、Cursor/OpenAI/Grok 网络）。只记录，不绕过。

## 2. 登记

```bash
python3 "$REPO/bin/eric-memory" --data-dir "$DATA" harness catalog
python3 "$REPO/bin/eric-memory" --data-dir "$DATA" harness add \
  --key "$KEY" \
  --name "$DISPLAY_NAME"
```

用户批准会话目录后：

```bash
python3 "$REPO/bin/eric-memory" --data-dir "$DATA" harness add \
  --key "$KEY" \
  --name "$DISPLAY_NAME" \
  --session-root "$ABS_SESSION" \
  --harvest
```

已挂 MCP 时加 `--mcp-mounted`。参考 `adapters/` 里对应文档；没有专文就用 `adapters/generic-mcp.md`。

## 3. 交给这个工具一份技能

把 `skills/严格技能.md` 放到该工具能读到的位置（项目技能、用户技能、或下次对话置顶）。要点不能改：

- 开工必 search
- 只经 CLI/MCP 写
- 1–3 句指针
- 矛盾则作废旧条
- 已登记源才收割

## 4. 投影

```bash
python3 "$REPO/bin/eric-memory" --data-dir "$DATA" sync
```

请用户打开 Obsidian「已接工具」确认新名字在。

## 5. 小验收

在新工具里搜一条刚写过的现行事实；再写一条测试指针并作废它。过了才告诉用户「这个工具接上了」。
