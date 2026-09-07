[English](full.md) · [中文](../完整.md)

# Maintainer workflow

The former “full tier” is now the same unified schema and trust loop. `--tier full` is a deprecated compatibility argument.

Maintainers additionally own migration/rollback testing, source consent policy, retrieval golden sets, 100k benchmarks, frozen native builds, signing/notarization, SBOM/provenance, real harness evidence, and independent review. Start at [architecture](architecture.md), [migration and recovery](migration-backup-restore.md), and [release process](release-process.md).

Development uses an isolated virtual environment and synthetic databases:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade "pip==26.2.1"
.venv/bin/python -m pip install -e ".[dev]"
.venv/bin/ruff check .
.venv/bin/mypy
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python scripts/evaluate_search.py
.venv/bin/python scripts/benchmark.py --facts 10000 --files 5000
```

Never run `mac_gate`, migration apply, restore, or destructive QA against the owner's live database. Formal release candidates require a fresh independent reviewer and evidence bound to one immutable commit.
