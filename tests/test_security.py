from __future__ import annotations

import base64
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from eric_memory.security import (
    WINDOWS_READ_ACL,
    WINDOWS_SET_ACL,
    _run_powershell,
    harden_private_path,
    private_path_status,
)
from tests.helpers import TempServiceTest


class SecurityPermissionsTests(TempServiceTest):
    def test_support_bundle_excludes_fact_text_and_complete_paths(self) -> None:
        canary = "SUPPORT-BUNDLE-FACT-CANARY-4f7d"
        self.service.add(canary, entities=["SupportCanary"])
        backup = self.service.backup_create()
        self.service.logger.write(
            operation_uid="00000000-0000-4000-8000-000000000001",
            action="synthetic.support-test",
            outcome="ok",
            principal="local",
            metadata={"content": canary, "path": str(self.data_dir / "private-source")},
        )
        output = Path(self._tmpdir.name).resolve() / "support.json"
        result = self.service.support_bundle(str(output))
        payload_text = output.read_text(encoding="utf-8")
        payload = json.loads(payload_text)
        self.assertFalse(result["facts_included"])
        self.assertFalse(result["paths_included"])
        self.assertNotIn(canary, payload_text)
        self.assertNotIn(str(self.data_dir), payload_text)
        self.assertNotIn(backup["backup"]["path"], payload_text)
        self.assertEqual(payload["privacy"], {"facts_included": False, "paths_included": False})

    def test_windows_acl_is_current_user_only_and_verified(self) -> None:
        target = Path(self._tmpdir.name) / "private.txt"
        target.write_text("synthetic", encoding="utf-8")
        acl = {
            "current_sid": "S-1-5-21-1000",
            "owner_sid": "S-1-5-21-1000",
            "protected": True,
            "aces": [
                {
                    "sid": "S-1-5-21-1000",
                    "inherited": False,
                    "type": "Allow",
                    "rights": "FullControl",
                }
            ],
        }
        completed = subprocess.CompletedProcess([], 0, json.dumps(acl), "")
        with (
            patch("eric_memory.security._is_windows", return_value=True),
            patch("eric_memory.security._run_powershell", return_value=completed) as runner,
        ):
            harden_private_path(target, directory=False)
            status = private_path_status(target, expected=0o600)
        self.assertTrue(status["ok"])
        self.assertTrue(status["exclusive_current_user"])
        self.assertEqual(runner.call_count, 2)

    def test_windows_acl_rejects_inherited_or_foreign_access(self) -> None:
        target = Path(self._tmpdir.name) / "private.txt"
        target.write_text("synthetic", encoding="utf-8")
        acl = {
            "current_sid": "S-1-current",
            "owner_sid": "S-1-current",
            "protected": True,
            "aces": [{"sid": "S-1-other", "inherited": False, "type": "Allow", "rights": "FullControl"}],
        }
        completed = subprocess.CompletedProcess([], 0, json.dumps(acl), "")
        with (
            patch("eric_memory.security._is_windows", return_value=True),
            patch("eric_memory.security._run_powershell", return_value=completed),
        ):
            status = private_path_status(target, expected=0o600)
        self.assertFalse(status["ok"])


class WindowsInvocationTests(unittest.TestCase):
    def test_powershell_paths_are_data_and_never_command_text(self) -> None:
        path = "C:\\private 中文\\$(Write-Output injected) ' quoted"
        with (
            patch("eric_memory.security._powershell", return_value="powershell.exe"),
            patch(
                "eric_memory.security.subprocess.run", return_value=subprocess.CompletedProcess([], 0, "", "")
            ) as runner,
        ):
            _run_powershell(WINDOWS_READ_ACL, path)
        command = runner.call_args.args[0]
        decoded = base64.b64decode(command[-1]).decode("utf-16-le")
        self.assertEqual(command[-2], "-EncodedCommand")
        self.assertNotIn(path, decoded)
        self.assertEqual(json.loads(runner.call_args.kwargs["env"]["WITH_ACL_ARGUMENTS_JSON"]), [path])

    @unittest.skipUnless(os.name == "nt", "requires a real Windows ACL")
    def test_real_windows_private_directory_and_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "private 中文 $literal ' quoted"
            root.mkdir()
            # Simulate inheritance from a parent shell with incompatible modules.
            with patch.dict(os.environ, {"PSModulePath": str(root / "unrelated modules")}):
                applied = _run_powershell(WINDOWS_SET_ACL, str(root), "directory")
                self.assertEqual(applied.returncode, 0, applied.stderr)
                self.assertTrue(private_path_status(root, expected=0o700)["ok"])
                target = root / "private.txt"
                target.write_text("synthetic", encoding="utf-8")
                harden_private_path(target, directory=False)
                self.assertTrue(private_path_status(target, expected=0o600)["ok"])
