# Eric Memory

客户装在自己电脑上的本机记忆系统。机器真值是自有 SQLite；人看的窗口是 Obsidian；各家 AI 工具通过同一条 CLI / MCP 读写。

**作废不删。** 现行是 `active`，历史是 `deprecated` + `superseded_by`。默认搜索不会把过期条目当成现行。

第一版只在客户本机。不做 App、不做云、不装开机守护、不要求管理员权限。

## 两档

| 档 | 给谁 | 怎么用 |
| --- | --- | --- |
| 简易 | 大陆客户 | 把 `quests/安装任务.md` 贴给手头的 AI，按问答题完装。之后只开 Obsidian「记忆首页」，每天让 AI 跑一次同步任务。 |
| 完整 | 你（维护者） | 同上，外加从旧 Holograph 导入、跑测试、补 adapter。 |

说明：[docs/简易.md](docs/简易.md) · [docs/完整.md](docs/完整.md) · [docs/windows-linux.md](docs/windows-linux.md)

## 需要什么

- Python 3.10+（macOS / Windows / Linux 皆可）
- Obsidian（只给人看，不当真值）
- 任意能跑终端命令或挂 MCP 的 AI 工具

不需要 Neo4j、Mem0、云账号、全局 PATH、管理员。

## 最快自检（完整档）

在仓库根目录：

```bash
python3 -m unittest discover -s tests -v
python3 bin/eric-memory --data-dir "$HOME/eric-memory-data" init --tier full
python3 bin/eric-memory --data-dir "$HOME/eric-memory-data" status
python3 scripts/acceptance_check.py
```

Windows 把 `python3` 换成 `py -3`，把 `$HOME/eric-memory-data` 换成 `%USERPROFILE%\eric-memory-data`。

## 官方入口

| 动作 | CLI | MCP |
| --- | --- | --- |
| 初始化 | `init` | （安装任务执行一次） |
| 状态 | `status` | `memory_status` |
| 写入 | `add` | `memory_add` |
| 检索 | `search` | `memory_search` |
| 作废 | `deprecate` | `memory_deprecate` |
| 同步 | `sync` | `memory_sync` |
| 导入 | `import holograph --source …` | `memory_import` |
| 索引资料夹 | `index-files` | `memory_index_files` |
| 登记工具 | `harness add` | `memory_harness_add` |

禁止对数据库私自 `INSERT`。技能合同见 [skills/严格技能.md](skills/严格技能.md)。

## 明确不做（第一版）

- 不把 Mem0 / Graphiti / Cognee / Supermemory 当内核
- 不把 Obsidian 当真值
- 不静默扫描全盘
- 不把厂商基准分当验收
- 未打磨完成前不建 GitHub、不给客户 zip
