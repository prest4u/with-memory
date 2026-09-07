from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.publish_release import main, release_command


class ReleasePublishTests(unittest.TestCase):
    def test_workflow_publication_routes_rc_and_ga_and_excludes_unrelated_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            (root / "with.zip").write_bytes(b"synthetic archive")
            (root / "private-notes.md").write_text("must not publish")
            for version in ("1.0.0-rc.1", "1.0.0"):
                with self.subTest(version=version), patch("scripts.publish_release.subprocess.run") as run:
                    with patch("sys.argv", ["publish_release", "--version", version, "--release-dir", str(root)]):
                        self.assertEqual(main(), 0)
                    command = run.call_args.args[0]
                    self.assertEqual(command[:4], ["gh", "release", "create", f"v{version}"])
                    self.assertIn(str(root / "with.zip"), command)
                    self.assertNotIn(str(root / "private-notes.md"), command)
                    self.assertEqual("--prerelease" in command, "-" in version)
                    self.assertEqual("--latest=false" in command, "-" in version)
            with self.assertRaises(ValueError):
                release_command("1.0.0; injected", root)
        workflow = (Path(__file__).resolve().parents[1] / ".github/workflows/release.yml").read_text()
        self.assertIn('python scripts/publish_release.py --version "$RELEASE_VERSION" --release-dir release', workflow)
        self.assertNotIn("run: gh release create", workflow)
