[English](en/add-harness.md) · [中文](添加AI工具.md)

# 任务：添加一个 harness

不新建第二套数据库，不扫描全盘，不启用 HTTP MCP。先确认现有 data directory 与 `doctor` 正常。

## 1. 问用户

- 工具名与稳定 key。
- 是否支持 stdio MCP；不支持则只给 CLI 指南。
- 是否有明确同意的 source 绝对路径；没有就不收割。
- source 的 include/exclude、类型和单文件上限。

## 2. 本地登记

```bash
eric-memory --data-dir "$DATA" harness add --key "$KEY" --name "$NAME"
```

若实际写入了 host MCP 配置，再加 `--mcp-mounted`。不要只改旗标。

MCP 参数：

```text
eric-memory --data-dir /ABS/data mcp --principal KEY
```

新连接的 `tools/list` 应只出现默认最小权限工具，不应出现 direct write 或管理工具。

## 3. 来源同意

只有用户明确给出路径时，本地执行：

```bash
eric-memory --data-dir "$DATA" source approve "$SOURCE" --harness "$KEY"
```

不要让 `harness add --harvest` 替代同意。后续撤销用 `source revoke SOURCE_UID`。

## 4. 交付契约与验收

把 `skills/严格技能.md` 交给该 harness。新工具内执行：

1. `memory_status`
2. `memory_search`
3. 对获准 source 执行 begin/complete
4. 提交一条无敏感内容的 candidate
5. 确认 direct `memory_add` 不可见或返回 `PERMISSION_REQUIRED`

用户在本地 `review` 决定是否接受测试 candidate。最后报告 principal、grants、source UID、MCP 配置位置和验收结果。
