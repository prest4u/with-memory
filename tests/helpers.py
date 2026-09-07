from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from eric_memory.service import MemoryService  # noqa: E402 - source checkout compatibility


class TempServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.data_dir = Path(self._tmpdir.name) / "data"
        self.service = MemoryService.for_init(self.data_dir)
        self.addCleanup(lambda: self.service.close())
        self.service.init(data_dir=self.data_dir, tier="full", write_repo_pointer=False)


def make_holograph_fixture(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.executescript(
        """
        CREATE TABLE facts (
            fact_id INTEGER PRIMARY KEY,
            content TEXT NOT NULL UNIQUE,
            category TEXT DEFAULT 'general',
            tags TEXT DEFAULT '',
            trust_score REAL DEFAULT 0.5,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE entities (
            entity_id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            entity_type TEXT DEFAULT 'unknown',
            aliases TEXT DEFAULT ''
        );
        CREATE TABLE fact_entities (
            fact_id INTEGER,
            entity_id INTEGER,
            PRIMARY KEY (fact_id, entity_id)
        );
        """
    )
    rows = [
        (1, "青云现行品牌是青云未来。", "project", "project:青云未来,status:active", 0.9),
        (2, "青云曾用名只叫青云，现已作废。", "project", "project:青云,status:deprecated", 0.4),
        (
            3,
            "Alex Rivera is STU-0001, Grade 9, midterm track.",
            "teaching",
            "student:示例学员",
            0.8,
        ),
        (4, "未标注状态的旧条应导入为现行。", "general", "", 0.5),
        (
            5,
            "同一条同时写了 ready 和 deprecated，必须按作废导入。",
            "teaching",
            "status:ready,status:deprecated",
            0.4,
        ),
    ]
    conn.executemany(
        "INSERT INTO facts(fact_id, content, category, tags, trust_score) VALUES (?, ?, ?, ?, ?)",
        rows,
    )
    conn.execute("INSERT INTO entities(entity_id, name) VALUES (1, '青云')")
    conn.execute("INSERT INTO entities(entity_id, name) VALUES (2, '示例学员')")
    conn.execute("INSERT INTO entities(entity_id, name) VALUES (3, 'STU-0001 示例学员')")
    conn.execute("INSERT INTO fact_entities(fact_id, entity_id) VALUES (1, 1)")
    conn.execute("INSERT INTO fact_entities(fact_id, entity_id) VALUES (2, 1)")
    conn.execute("INSERT INTO fact_entities(fact_id, entity_id) VALUES (3, 2)")
    conn.execute("INSERT INTO fact_entities(fact_id, entity_id) VALUES (3, 3)")
    conn.commit()
    conn.close()
    return path
