#!/usr/bin/env python3
"""Validate the shape of owner attestations referencing one frozen RC commit.

This does not authenticate reviewers, execute harness tests, or hash their source
records. Protected-environment reviewers must inspect those records separately."""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

HARNESSES = {"codex", "claude-code", "cursor", "hermes", "kimi", "qwen"}
REVIEWS = {"security", "migration", "privacy", "release"}
SHA256_RE = re.compile(r"[0-9a-f]{64}")
COMMIT_RE = re.compile(r"[0-9a-f]{40}")
SEMVER_RE = re.compile(r"\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?")


def _valid_hash(value: object) -> bool:
    text = str(value)
    return bool(SHA256_RE.fullmatch(text) and set(text) != {"0"})


def _valid_timestamp(value: object) -> bool:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None


def _exact_keys(value: dict[str, Any], expected: set[str], *, label: str) -> None:
    if set(value) != expected:
        raise ValueError(f"{label} fields must be exactly {sorted(expected)}")


def validate_evidence(value: Any, *, version: str, source_commit: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("release evidence must be a JSON object")
    _exact_keys(
        value,
        {
            "format",
            "version",
            "source_commit",
            "harnesses",
            "reviews",
            "migration",
            "mcp_conformance",
            "recorded_at",
        },
        label="release evidence",
    )
    if value["format"] != "with-release-evidence-v1":
        raise ValueError("release evidence format is unsupported")
    if value["version"] != version or value["source_commit"] != source_commit:
        raise ValueError("release evidence is not bound to this version and source commit")
    if not COMMIT_RE.fullmatch(source_commit) or set(source_commit) == {"0"}:
        raise ValueError("source commit must be a full lowercase SHA-1")
    if not _valid_timestamp(value["recorded_at"]):
        raise ValueError("release evidence recorded_at must be an ISO timestamp with timezone")

    harnesses = value["harnesses"]
    if not isinstance(harnesses, dict) or set(harnesses) != HARNESSES:
        raise ValueError(f"real harness evidence must cover exactly {sorted(HARNESSES)}")
    for name, evidence in harnesses.items():
        if not isinstance(evidence, dict):
            raise ValueError(f"{name} harness evidence must be an object")
        _exact_keys(evidence, {"status", "tested_at", "evidence_sha256"}, label=f"{name} harness evidence")
        if (
            evidence["status"] != "passed"
            or not _valid_timestamp(evidence["tested_at"])
            or not _valid_hash(evidence["evidence_sha256"])
        ):
            raise ValueError(f"{name} real harness smoke has not passed with hashed evidence")

    reviews = value["reviews"]
    if not isinstance(reviews, dict) or set(reviews) != REVIEWS:
        raise ValueError(f"independent reviews must cover exactly {sorted(REVIEWS)}")
    reviewers: set[str] = set()
    for kind, review in reviews.items():
        if not isinstance(review, dict):
            raise ValueError(f"{kind} review must be an object")
        _exact_keys(
            review,
            {"status", "reviewer", "source_commit", "evidence_sha256"},
            label=f"{kind} review",
        )
        reviewer = str(review["reviewer"]).strip()
        if (
            review["status"] != "passed"
            or review["source_commit"] != source_commit
            or len(reviewer) < 3
            or not _valid_hash(review["evidence_sha256"])
        ):
            raise ValueError(f"{kind} review is not a passing review of the frozen source commit")
        reviewers.add(reviewer.casefold())
    if len(reviewers) != len(REVIEWS):
        raise ValueError("security, migration, privacy, and release reviews require distinct reviewers")

    migration = value["migration"]
    if not isinstance(migration, dict):
        raise ValueError("migration evidence must be an object")
    _exact_keys(
        migration,
        {"synthetic_fixture", "real_database_plan", "backup_verified", "real_database_modified"},
        label="migration evidence",
    )
    if migration != {
        "synthetic_fixture": "passed",
        "real_database_plan": "passed_read_only",
        "backup_verified": True,
        "real_database_modified": False,
    }:
        raise ValueError("migration evidence does not meet the no-live-write RC policy")

    conformance = value["mcp_conformance"]
    if not isinstance(conformance, dict):
        raise ValueError("MCP conformance evidence must be an object")
    _exact_keys(
        conformance,
        {
            "status",
            "runner",
            "runner_version",
            "transport",
            "target",
            "suite",
            "spec_versions",
            "tested_at",
            "evidence_sha256",
        },
        label="MCP conformance evidence",
    )
    spec_versions = conformance["spec_versions"]
    if (
        conformance["status"] != "passed"
        or conformance["runner"] != "@modelcontextprotocol/conformance"
        or not SEMVER_RE.fullmatch(str(conformance["runner_version"]))
        or conformance["transport"] != "stdio"
        or conformance["target"] != "shipping-server"
        or conformance["suite"] != "server-active"
        or not isinstance(spec_versions, list)
        or "2025-11-25" not in spec_versions
        or any(not isinstance(item, str) or not item for item in spec_versions)
        or not _valid_timestamp(conformance["tested_at"])
        or not _valid_hash(conformance["evidence_sha256"])
    ):
        raise ValueError("official MCP server conformance has not passed against the shipping stdio server")
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--source-commit", required=True)
    args = parser.parse_args()
    try:
        value = json.loads(args.evidence.read_text(encoding="utf-8"))
        validate_evidence(value, version=args.version, source_commit=args.source_commit)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise SystemExit(f"release evidence rejected: {exc}") from exc
    print(
        json.dumps(
            {
                "ok": True,
                "check": "owner-attestation-schema",
                "authenticity_verified": False,
                "evidence": str(args.evidence),
                "source_commit": args.source_commit,
                "manual_review_required": (
                    "Verify the underlying reports, hashes, reviewer identities and approvals before release."
                ),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
