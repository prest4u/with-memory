from __future__ import annotations

import unittest
from pathlib import Path

from tests.helpers import SRC

from eric_memory.paths import PathError, require_absolute


class PathTests(unittest.TestCase):
    def test_rejects_tilde_and_relative(self) -> None:
        with self.assertRaises(PathError):
            require_absolute("~/eric-memory-data", name="data dir")
        with self.assertRaises(PathError):
            require_absolute("eric-memory-data", name="data dir")
        abs_path = require_absolute(Path("/tmp/eric-memory-test"), name="data dir")
        self.assertTrue(abs_path.is_absolute())
        self.assertNotIn("~", str(abs_path))

    def test_src_layout_is_importable(self) -> None:
        self.assertTrue((SRC / "eric_memory" / "service.py").is_file())
