[English](CONTRIBUTING.md) · [中文](CONTRIBUTING.zh-CN.md)

# 怎么改这个仓库

内核合同不能破：harness 只交 candidate、本地 review 才激活、演进用取代而非改写、只经 CLI/MCP 写、Obsidian 不是真值、只扫获准 source。

产品叫 With. 命令仍是 `eric-memory`。

## 开发

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"
.venv/bin/ruff check .
.venv/bin/mypy
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python scripts/repo_check.py
```

普通验证只用合成临时库。禁止把 acceptance、迁移、restore 或 `mac_gate` 指向 owner 的真实库；不要提交 `memory.db`、本机 `mcp.json`、发行私钥或含真实路径或正文的验证记录。

## 写入

每次行为变化都补能证伪的测试。安全、迁移、权限、发行和内容策略在发布前必须由独立 reviewer 审阅冻结候选。整个 v1 保留整数 ID、命令/MCP 名称与旧 JSON 字段。

## 文档

README 第一屏是品牌，不是功能清单。公开页顶统一 `[English] · [中文]`。文档里不要写维护者本机主目录。
