from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


def _public(data: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in data.items() if not k.startswith("_")}


@dataclass
class Fact:
    fact_id: int
    content: str
    category: str
    tags: str
    trust: float
    status: str
    as_of: str
    superseded_by: int | None
    source_kind: str
    source_ref: str
    created_at: str
    updated_at: str
    entities: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return _public(asdict(self))


@dataclass
class Entity:
    entity_id: int
    name: str
    entity_type: str
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return _public(asdict(self))


@dataclass
class FileRecord:
    file_id: int
    path: str
    folder: str
    name: str
    sha256: str
    size: int
    mtime: float
    indexed_at: str

    def to_dict(self) -> dict[str, Any]:
        return _public(asdict(self))


@dataclass
class Harness:
    harness_id: int
    key: str
    display_name: str
    session_root: str
    mcp_mounted: bool
    harvest_ok: bool
    notes: str
    created_at: str
    updated_at: str

    def to_dict(self) -> dict[str, Any]:
        return _public(asdict(self))


@dataclass
class AuditEvent:
    audit_id: int
    action: str
    fact_id: int | None
    actor: str
    detail: str
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return _public(asdict(self))
