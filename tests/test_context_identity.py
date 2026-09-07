from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from eric_memory.context_service import _identity, read_source
from eric_memory.errors import ValidationError


class SourceIdentityTests(unittest.TestCase):
    def test_windows_identity_compares_birth_time_across_stat_apis(self) -> None:
        fields = {"st_dev": 1, "st_ino": 2, "st_size": 3, "st_mtime_ns": 4, "st_birthtime_ns": 5}
        path_info = SimpleNamespace(**fields, st_ctime_ns=5)
        handle_info = SimpleNamespace(**fields, st_ctime_ns=9)
        with patch("eric_memory.context_service.os.name", "nt"):
            self.assertEqual(_identity(path_info), _identity(handle_info))
        with patch("eric_memory.context_service.os.name", "posix"):
            self.assertNotEqual(_identity(path_info), _identity(handle_info))

    def test_handle_change_time_is_still_checked_during_read(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            path = root / "stable.txt"
            path.write_bytes(b"Orchid")
            info = path.stat()
            fields = {key: getattr(info, key) for key in dir(info) if key.startswith("st_")}
            changed = SimpleNamespace(**{**fields, "st_ctime_ns": info.st_ctime_ns + 1})
            source = {
                "canonical_root": str(root),
                "include": [],
                "exclude": [],
                "file_types": ["txt"],
                "max_file_bytes": 4096,
            }
            with (
                patch("eric_memory.context_service.os.fstat", side_effect=[info, changed]),
                patch("eric_memory.context_service._identity", return_value=(1, 2, 3, 4, 5)),
                self.assertRaisesRegex(ValidationError, "changed during"),
            ):
                read_source(source, path.name)

    def test_stable_file_identity_survives_repeated_create_and_rewrite(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source = {
                "canonical_root": str(root),
                "include": [],
                "exclude": [],
                "file_types": ["txt"],
                "max_file_bytes": 4096,
            }
            observed = []

            def observe(info):
                value = _identity(info)
                observed.append(value)
                return value

            for index in range(300):
                observed.clear()
                path = root / f"stable-{index % 10}.txt"
                content = f"Orchid 中文 iteration {index}\n"
                path.write_bytes(content.encode("utf-8"))
                with patch("eric_memory.context_service._identity", side_effect=observe):
                    try:
                        self.assertEqual(read_source(source, path.name), content)
                    except ValidationError as exc:
                        self.fail(f"stable input rejected: {exc}; compared identities: {observed}")
