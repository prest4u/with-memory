[English](windows-linux.md) · [中文](../windows-linux.md)

# Windows / Linux

v1 is accepted on macOS. These hosts follow the same contract. You do not have to prove them yourself today.

## Shared rules

- Python 3.10+
- Data directory must be absolute after init
- No Administrator / root
- No login daemon
- On Windows the interpreter is `py -3`

## Windows

```bat
py -3 --version
py -3 C:\ABS\with-memory\bin\eric-memory --data-dir %USERPROFILE%\eric-memory-data init --tier simple
py -3 C:\ABS\with-memory\bin\eric-memory --data-dir %USERPROFILE%\eric-memory-data status
py -3 C:\ABS\with-memory\mcp\server.py --data-dir %USERPROFILE%\eric-memory-data
```

Open `%USERPROFILE%\eric-memory-data\vault` in Obsidian, or the vault you passed at init.

## Linux

```bash
python3 --version
python3 /ABS/with-memory/bin/eric-memory --data-dir "$HOME/eric-memory-data" init --tier simple
python3 /ABS/with-memory/bin/eric-memory --data-dir "$HOME/eric-memory-data" status
python3 /ABS/with-memory/mcp/server.py --data-dir "$HOME/eric-memory-data"
```

If the distro only has `python` and it is 3.10+, use that name. Do not switch to root to get a newer interpreter.

## Quests

Install / daily sync / add-harness still use the files in `quests/` (Chinese) or `quests/en/` (English). Swap `python3` for `py -3` on Windows.
