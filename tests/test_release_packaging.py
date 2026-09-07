from __future__ import annotations

import os
import re
import stat
import tempfile
import unittest
import zipfile
from pathlib import Path

from scripts.package_release import _copy_public_payload, _zip_tree


class ReleasePackagingTests(unittest.TestCase):
    def test_archive_contains_public_documentation_and_relative_link_targets(self) -> None:
        repository = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "payload"
            source.mkdir()
            _copy_public_payload(repository, source)
            output = root / "release.zip"
            _zip_tree(source, output)
            with zipfile.ZipFile(output) as archive:
                members = set(archive.namelist())
                self.assertIn("with/docs/en/migration-backup-restore.md", members)
                for document in source.rglob("*.md"):
                    for href in re.findall(r"\]\(([^)]+)\)", document.read_text(encoding="utf-8")):
                        if "://" in href or href.startswith(("#", "mailto:", "/")):
                            continue
                        target = (document.parent / href.split("#")[0]).resolve()
                        self.assertTrue(target.is_relative_to(source.resolve()), str(document))
                        member = "with/" + target.relative_to(source.resolve()).as_posix()
                        self.assertIn(member, members, f"{document.name}: {href}")

    @unittest.skipIf(os.name == "nt", "Windows release inputs do not use POSIX symlinks")
    def test_archive_rejects_external_links_and_cycles(self) -> None:
        for kind in ("file", "directory", "cycle"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                source = root / "source"
                source.mkdir()
                outside = root / "private"
                outside.mkdir()
                (outside / "canary").write_bytes(b"private-canary")
                target = outside / "canary" if kind == "file" else outside
                if kind == "cycle":
                    target = source
                (source / "link").symlink_to(target, target_is_directory=kind != "file")
                output = root / "release.zip"
                with self.assertRaises(ValueError):
                    _zip_tree(source, output)
                self.assertFalse(output.exists())

    @unittest.skipIf(os.name == "nt", "Windows release inputs do not use POSIX symlinks")
    def test_archive_materializes_onedir_symlinks_as_safe_regular_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            target = source / "framework" / "Versions" / "1.0"
            target.mkdir(parents=True)
            (target / "Python").write_bytes(b"runtime")
            (source / "framework" / "Versions" / "Current").symlink_to("1.0", target_is_directory=True)
            (source / "Python").symlink_to("framework/Versions/Current/Python")
            output = root / "release.zip"

            _zip_tree(source, output)

            with zipfile.ZipFile(output) as archive:
                python = archive.getinfo("with/Python")
                current = archive.getinfo("with/framework/Versions/Current/Python")
                self.assertFalse(stat.S_ISLNK(python.external_attr >> 16))
                self.assertFalse(stat.S_ISLNK(current.external_attr >> 16))
                self.assertEqual(archive.read(python), b"runtime")
                self.assertEqual(archive.read(current), b"runtime")
