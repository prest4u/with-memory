---
name: eric-memory
description: Local memory candidate gate. Search first. Read and submit only through eric-memory CLI/MCP. Harnesses submit 1–3 sentence candidates; only local interactive review activates facts.
---

# 严格技能：Eric Memory

本机记忆的唯一写入口是这个仓库的 CLI 或 MCP。Obsidian 只是投影。旧 Holograph 库只归档，不再写入。

## 必须

1. **只调用本仓库 CLI / MCP。** 禁止私自 `INSERT` / `UPDATE` / 直接改 `memory.db` 或旧 `memory_store.db`。
2. **开工先 search。** 作用域用 `user` / `workspace` / `project`，与当前任务一致。默认结果里的 `deprecated` 不是现行。
3. **默认只提交 candidate。** 写入 1–3 句、最多 1,200 字符的指针。只有本地交互式管理员审阅后才能成为 `active`。
4. **矛盾在审阅时显式取代。** 普通 harness 不直接 `deprecate`；管理员在 `review` 中选择被取代事实。
5. **已批准 source 才收割。** 只处理仍为 `approved` 且分配给本 principal 的来源，禁止默认全盘扫。

## 禁止

- 凭据、密钥、cookie、token
- 未成年人到课记录或个体表现
- 整段会话、整篇文档原文
- 把文件正文写成事实（事实里只放路径）
- 把过期条当成现行建议
- 调用或模拟 `purge`（仅本地交互式管理员可用）
- 为每个新 AI 工具重写一套内核
- 再走 `query_holographic.py add`、`memory-append.py`、或 Hermes `memory.provider: holographic`

## 检索

```text
eric-memory search "$QUERY" --scope user
eric-memory search "$QUERY" --scope project --project 青云
eric-memory search "$QUERY" --include-deprecated
```

检索融合实体、别名、CJK/Latin term、FTS5 与字面 LIKE；普通 harness 看不到历史。本机数据目录以安装时写下的绝对路径为准。

## 候选提交

```text
eric-memory candidate add --content "……" --entities "实体1,实体2" --scope user
```

## MCP 对照

普通 principal 默认可见：`memory_status` `memory_search` `memory_candidate_add` `memory_candidate_list` `memory_source_list` `memory_harvest_begin` `memory_harvest_complete`。MCP 配置必须带 `--principal KEY`；未带时进入只读 `legacy` 身份。

参数与 CLI 相同。不要发明第二套工具名。
