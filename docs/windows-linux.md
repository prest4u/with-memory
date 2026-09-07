[English](en/windows-linux.md) · [中文](windows-linux.md)

# Windows 与 Linux

GA 必须在 Windows x86_64 与 Linux x86_64 上分别完成原生 CI、构建和冒烟；macOS 通过不能替代。三类系统使用同一 schema 与 CLI/MCP 契约。

## Windows

安装到当前用户虚拟环境或已验证 onedir 包。data/config/database 会设置受保护 ACL，只保留当前用户 SID。`doctor` 校验权限并在可用时报告 BitLocker。

```powershell
py -3 -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade "pip==26.2.1"
.venv\Scripts\python.exe -m pip install .
.venv\Scripts\eric-memory.exe --data-dir "$env:USERPROFILE\eric-memory-data" init --no-obsidian
.venv\Scripts\eric-memory.exe --data-dir "$env:USERPROFILE\eric-memory-data" doctor
```

MCP 使用同一可执行文件并加 `mcp --principal KEY`。禁止以管理员运行。

## Linux

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade "pip==26.2.1"
.venv/bin/python -m pip install .
.venv/bin/eric-memory --data-dir "$HOME/eric-memory-data" init --no-obsidian
.venv/bin/eric-memory --data-dir "$HOME/eric-memory-data" doctor
```

目录/文件权限收紧为 `0700`/`0600`；应启用 LUKS 或等价磁盘加密。禁止 root 安装。

两个原生产物都必须在干净 OS 用户下通过 init、迁移 plan、candidate/review、search、backup/restore、stdio MCP、support-bundle、启动和卸载验收，才允许 GA。
