[English](CONTRIBUTING.md) · [中文](CONTRIBUTING.zh-CN.md)

# 怎么改这个仓库

内核合同不能破：作废不删、只经 CLI/MCP 写、Obsidian 不是真值、不默认全盘扫、不换 Mem0/Graphiti/Cognee/Supermemory。

产品叫 With. 命令仍是 `eric-memory`。

## 开发

```bash
python3 -m unittest discover -s tests -v
python3 scripts/repo_check.py
python3 bin/eric-memory --version
```

有本机数据目录时再跑 `python3 scripts/acceptance_check.py`。不要把 `memory.db` 或本机 `mcp.json` 提交上来。

## 写入

补测试再改行为。回归放 `tests/test_review_regressions.py`。对外文档英文为主。大陆客户粘贴的任务全文留在 `quests/`。

## 文档

README 第一屏是品牌，不是功能清单。公开页顶统一 `[English] · [中文]`。文档里不要写维护者本机主目录。
