[English](SECURITY.md) · [中文](SECURITY.zh-CN.md)

# 安全策略

## 支持版本

GA 前只有最新冻结 RC 接受安全修复。`1.0.0` 后只支持最新 v1 minor/patch；更早的开发快照不受支持。只有原生 archive 能通过附件中的 Ed25519 签名清单与 GitHub provenance 校验时，才算真实发行。

## 私下报告

优先使用本仓库 GitHub Private Vulnerability Reporting / Security Advisory。若该通道不可用，请通过维护者 GitHub profile 已公开的私密联系方式先索取安全报告通道，第一条消息不要包含漏洞细节。

请提供受影响版本/commit、OS/架构、影响、纯合成数据的最小复现和脱敏 operation UID。不要发送真实 `memory.db`、WAL、原始会话/文件正文、完整路径清单、凭据、Vault、发行私钥或未检查的支持包。

在协商披露日期前不要创建公开 issue。完整报告应在七日内得到确认，随后完成严重度/影响版本判断、私下修复与回归测试；若签名材料受影响则轮换，并通过安全公告发布修复版本。

## 安全不变量

- SQLite 是唯一真值，所有写入必须经过 transaction/service 入口。
- 普通 MCP principal 不能批准 candidate、purge、restore、migrate、批准 source 或管理 grant。
- ACL、scope、status 先于排名；deprecated 与跨 scope 泄漏都会阻断发布。
- 禁止内容必须做到原文零持久化；日志和 audit 不复制事实正文。
- 除 `init` 外不得创建缺失数据库；读命令使用只读 URI。
- 只有显式 update 才联网；信任根、签名、大小、哈希、redirect 或压缩路径任一异常都会安全失败。
- 来源扫描不跟随符号链接，也不遍历未批准根。

系统保护依赖 OS 账户；同账户任意 shell 等价于本地管理员。请启用 FileVault、BitLocker 或 LUKS。完整内容见[隐私与威胁模型](docs/隐私与威胁模型.md)和[紧急清除说明](docs/紧急清除.md)。
