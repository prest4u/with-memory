[English](windows-linux.md) · [中文](../windows-linux.md)

# Windows and Linux

GA requires native CI/build/smoke evidence on Windows x86_64 and Linux x86_64; macOS success does not substitute for it. All platforms use the same schema and CLI/MCP contract.

## Windows

Install per user into a virtual environment or verified native onedir package. Data/config/database files receive a protected ACL with only the current user. `doctor` verifies the current-user SID grant and reports BitLocker status when available.

```powershell
py -3 -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade "pip==26.2.1"
.venv\Scripts\python.exe -m pip install .
.venv\Scripts\eric-memory.exe --data-dir "$env:USERPROFILE\eric-memory-data" init --no-obsidian
.venv\Scripts\eric-memory.exe --data-dir "$env:USERPROFILE\eric-memory-data" doctor
```

MCP uses the same executable with `mcp --principal KEY`. Do not run as Administrator.

## Linux

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade "pip==26.2.1"
.venv/bin/python -m pip install .
.venv/bin/eric-memory --data-dir "$HOME/eric-memory-data" init --no-obsidian
.venv/bin/eric-memory --data-dir "$HOME/eric-memory-data" doctor
```

Directories and files are hardened to `0700`/`0600`; enable LUKS or equivalent disk encryption. Do not install as root.

Both native artifacts must pass init, migration planning, candidate/review, search, backup/restore, stdio MCP, support-bundle, startup, and uninstall tests in a clean OS user before GA.
