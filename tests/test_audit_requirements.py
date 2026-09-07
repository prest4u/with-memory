from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.export_audit_requirements import ROOT, audit_requirements, installed_requirements


class AuditRequirementsTests(unittest.TestCase):
    def test_export_uses_runtime_and_dev_pins_without_local_root(self) -> None:
        requirements = audit_requirements(ROOT / "pyproject.toml")
        self.assertIn("cryptography==50.0.1", requirements)
        self.assertIn("mcp==2.1.1", requirements)
        self.assertIn("pip-audit==2.10.1", requirements)
        self.assertNotIn("eric-memory==0.1.0", requirements)
        self.assertTrue(all("==" in item for item in requirements))

    def test_invalid_or_duplicate_dependency_tables_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            pyproject = Path(temporary) / "pyproject.toml"
            pyproject.write_text(
                '[project]\ndependencies = ["same==1", "same==1"]\n'
                '[project.optional-dependencies]\ndev = ["tool==1"]\n',
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                audit_requirements(pyproject)

    def test_installed_snapshot_contains_transitive_packages_but_not_local_root(self) -> None:
        requirements = installed_requirements()
        lowered = {item.casefold() for item in requirements}
        self.assertTrue(any(item.startswith("cryptography==") for item in lowered))
        self.assertFalse(any(item.startswith("eric-memory==") for item in lowered))


if __name__ == "__main__":
    raise SystemExit(unittest.main())
