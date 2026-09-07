from __future__ import annotations

import copy
import unittest

from scripts.verify_release_evidence import HARNESSES, REVIEWS, validate_evidence


class ReleaseEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.commit = "a" * 40
        self.value = {
            "format": "with-release-evidence-v1",
            "version": "1.0.0-rc.1",
            "source_commit": self.commit,
            "recorded_at": "2026-09-01T10:00:00Z",
            "harnesses": {
                name: {
                    "status": "passed",
                    "tested_at": "2026-09-01T09:00:00Z",
                    "evidence_sha256": f"{index + 1:x}" * 64,
                }
                for index, name in enumerate(sorted(HARNESSES))
            },
            "reviews": {
                name: {
                    "status": "passed",
                    "reviewer": f"reviewer-{name}",
                    "source_commit": self.commit,
                    "evidence_sha256": f"{index + 9:x}" * 64,
                }
                for index, name in enumerate(sorted(REVIEWS))
            },
            "migration": {
                "synthetic_fixture": "passed",
                "real_database_plan": "passed_read_only",
                "backup_verified": True,
                "real_database_modified": False,
            },
            "mcp_conformance": {
                "status": "passed",
                "runner": "@modelcontextprotocol/conformance",
                "runner_version": "0.1.16",
                "transport": "stdio",
                "target": "shipping-server",
                "suite": "server-active",
                "spec_versions": ["2025-11-25"],
                "tested_at": "2026-09-01T09:30:00Z",
                "evidence_sha256": "f" * 64,
            },
        }

    def test_complete_attestation_schema_references_one_commit(self) -> None:
        result = validate_evidence(self.value, version="1.0.0-rc.1", source_commit=self.commit)
        self.assertIs(result, self.value)
        with self.assertRaises(ValueError):
            validate_evidence(self.value, version="1.0.0-rc.1", source_commit="b" * 40)

    def test_missing_harness_duplicate_reviewer_and_zero_hash_are_rejected(self) -> None:
        missing = copy.deepcopy(self.value)
        missing["harnesses"].pop("qwen")
        with self.assertRaises(ValueError):
            validate_evidence(missing, version="1.0.0-rc.1", source_commit=self.commit)

        duplicate = copy.deepcopy(self.value)
        duplicate["reviews"]["security"]["reviewer"] = duplicate["reviews"]["privacy"]["reviewer"]
        with self.assertRaises(ValueError):
            validate_evidence(duplicate, version="1.0.0-rc.1", source_commit=self.commit)

        zero = copy.deepcopy(self.value)
        zero["harnesses"]["codex"]["evidence_sha256"] = "0" * 64
        with self.assertRaises(ValueError):
            validate_evidence(zero, version="1.0.0-rc.1", source_commit=self.commit)

        wrong_transport = copy.deepcopy(self.value)
        wrong_transport["mcp_conformance"]["transport"] = "http"
        with self.assertRaises(ValueError):
            validate_evidence(wrong_transport, version="1.0.0-rc.1", source_commit=self.commit)
