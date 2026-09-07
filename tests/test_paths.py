from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from eric_memory.paths import PathError, expand_once, require_absolute
from tests.helpers import SRC


class PathTests(unittest.TestCase):
    def test_rejects_tilde_and_relative(self) -> None:
        with self.assertRaises(PathError):
            require_absolute("~/eric-memory-data", name="data dir")
        with self.assertRaises(PathError):
            require_absolute("eric-memory-data", name="data dir")
        abs_path = require_absolute((Path(tempfile.gettempdir()) / "eric-memory-test").resolve(), name="data dir")
        self.assertTrue(abs_path.is_absolute())
        self.assertNotIn("~", str(abs_path))

    def test_src_layout_is_importable(self) -> None:
        self.assertTrue((SRC / "eric_memory" / "service.py").is_file())

    def test_literal_tilde_filename_is_not_a_home_expression(self) -> None:
        literal = Path(tempfile.gettempdir()) / "RUNNER~1" / "data"
        self.assertEqual(require_absolute(literal, name="source"), literal)
        self.assertEqual(expand_once(literal, name="source", resolve=False), literal.absolute())
        for expression in ("~", "~someone/data", "${HOME}/data", "%USERPROFILE%/data"):
            with self.subTest(expression=expression), self.assertRaises(PathError):
                require_absolute(expression, name="source")

    def test_repo_check_is_portable(self) -> None:
        scripts = str(Path(__file__).resolve().parents[1] / "scripts")
        if scripts not in sys.path:
            sys.path.insert(0, scripts)
        from repo_check import collect_gaps

        self.assertEqual(collect_gaps(), [])
