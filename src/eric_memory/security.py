"""Cross-platform private filesystem permissions for the OS-account trust boundary."""

from __future__ import annotations

import base64
import json
import os
import shutil
import stat
import subprocess
from pathlib import Path
from typing import Any

WINDOWS_SET_ACL = r"""
$ErrorActionPreference = 'Stop'
$target = $withArguments[0]
$isDirectory = $withArguments[1] -eq 'directory'
$identity = [System.Security.Principal.WindowsIdentity]::GetCurrent()
if ($isDirectory) {
  $acl = New-Object System.Security.AccessControl.DirectorySecurity
  $inheritance = [System.Security.AccessControl.InheritanceFlags]'ContainerInherit, ObjectInherit'
} else {
  $acl = New-Object System.Security.AccessControl.FileSecurity
  $inheritance = [System.Security.AccessControl.InheritanceFlags]::None
}
$acl.SetOwner($identity.User)
$acl.SetAccessRuleProtection($true, $false)
$rule = New-Object System.Security.AccessControl.FileSystemAccessRule(
  $identity.User,
  [System.Security.AccessControl.FileSystemRights]::FullControl,
  $inheritance,
  [System.Security.AccessControl.PropagationFlags]::None,
  [System.Security.AccessControl.AccessControlType]::Allow
)
$acl.AddAccessRule($rule)
Set-Acl -LiteralPath $target -AclObject $acl
"""

WINDOWS_READ_ACL = r"""
$ErrorActionPreference = 'Stop'
$identity = [System.Security.Principal.WindowsIdentity]::GetCurrent()
$acl = Get-Acl -LiteralPath $withArguments[0]
$aces = @($acl.Access | ForEach-Object {
  $sid = $_.IdentityReference.Translate([System.Security.Principal.SecurityIdentifier]).Value
  [PSCustomObject]@{
    sid = $sid
    inherited = $_.IsInherited
    type = $_.AccessControlType.ToString()
    rights = $_.FileSystemRights.ToString()
  }
})
[PSCustomObject]@{
  current_sid = $identity.User.Value
  owner_sid = $acl.GetOwner([System.Security.Principal.SecurityIdentifier]).Value
  protected = $acl.AreAccessRulesProtected
  aces = $aces
} | ConvertTo-Json -Compress -Depth 4
"""


def _is_windows() -> bool:
    return os.name == "nt"


def _powershell() -> str:
    executable = shutil.which("powershell.exe") or shutil.which("powershell")
    if not executable:
        raise PermissionError("PowerShell is required to apply the private Windows ACL")
    return executable


def _run_powershell(script: str, *arguments: str) -> subprocess.CompletedProcess[str]:
    # Windows PowerShell treats trailing -Command arguments as script text.
    # Keep paths in per-process JSON data, separate from the fixed program.
    prefix = "$withArguments = @(ConvertFrom-Json $env:WITH_ACL_ARGUMENTS_JSON)\n"
    encoded = base64.b64encode((prefix + script).encode("utf-16-le")).decode("ascii")
    environment = {**os.environ, "WITH_ACL_ARGUMENTS_JSON": json.dumps(arguments)}
    return subprocess.run(  # noqa: S603 - absolute executable, fixed script, no shell
        [_powershell(), "-NoLogo", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
        env=environment,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )


def harden_private_path(path: str | Path, *, directory: bool | None = None) -> None:
    target = Path(path)
    if not target.exists():
        raise FileNotFoundError(target)
    is_directory = target.is_dir() if directory is None else directory
    if not _is_windows():
        os.chmod(target, 0o700 if is_directory else 0o600)
        return
    result = _run_powershell(WINDOWS_SET_ACL, str(target), "directory" if is_directory else "file")
    if result.returncode != 0:
        raise PermissionError("failed to restrict the path to the current Windows user")


def private_path_status(path: str | Path, *, expected: int) -> dict[str, Any]:
    target = Path(path)
    if not target.exists():
        return {"exists": False, "ok": False, "expected": oct(expected)}
    if not _is_windows():
        actual = stat.S_IMODE(target.stat().st_mode)
        return {
            "exists": True,
            "actual": oct(actual),
            "expected": oct(expected),
            "ok": actual == expected,
        }
    result = _run_powershell(WINDOWS_READ_ACL, str(target))
    if result.returncode != 0:
        return {"exists": True, "platform": "windows", "ok": False, "error": "acl_query_failed"}
    try:
        payload = json.loads(result.stdout)
        aces = payload.get("aces", [])
        if isinstance(aces, dict):
            aces = [aces]
        current_sid = payload["current_sid"]
        exclusive = bool(aces) and all(
            item.get("sid") == current_sid
            and not item.get("inherited")
            and item.get("type") == "Allow"
            and "FullControl" in item.get("rights", "")
            for item in aces
        )
        ok = bool(payload.get("protected") and payload.get("owner_sid") == current_sid and exclusive)
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return {"exists": True, "platform": "windows", "ok": False, "error": "acl_parse_failed"}
    return {"exists": True, "platform": "windows", "ok": ok, "exclusive_current_user": exclusive}
