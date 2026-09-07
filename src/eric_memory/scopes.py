"""Structured scope parsing and stable fingerprints."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .errors import ValidationError

SCOPE_KINDS = {"user", "project", "workspace"}
TAG_SCOPE_RE = re.compile(r"^(project|workspace):([^,\x00-\x1f\x7f]{1,200})$")


@dataclass(frozen=True)
class ScopeSpec:
    kind: str
    key: str = ""

    @property
    def fingerprint(self) -> str:
        return f"{self.kind}:{self.key.casefold()}"

    def to_dict(self) -> dict[str, str]:
        return {"kind": self.kind, "key": self.key}


def validate_scope(kind: str = "user", key: str | None = None) -> ScopeSpec:
    normalized_kind = (kind or "user").strip().lower()
    if normalized_kind not in SCOPE_KINDS:
        raise ValidationError(f"invalid scope kind: {normalized_kind}")
    normalized_key = (key or "").strip()
    if normalized_kind == "user":
        if normalized_key:
            raise ValidationError("user scope must not include a scope key")
        return ScopeSpec("user", "")
    if not normalized_key:
        raise ValidationError(f"scope={normalized_kind} requires an exact scope key")
    if len(normalized_key) > 200 or any(ord(ch) < 32 for ch in normalized_key):
        raise ValidationError("scope key is invalid")
    return ScopeSpec(normalized_kind, normalized_key)


def scope_from_request(
    scope: str = "user",
    *,
    project: str | None = None,
    workspace: str | None = None,
) -> ScopeSpec:
    normalized = (scope or "user").strip().lower()
    if project and workspace:
        raise ValidationError("project and workspace scopes are mutually exclusive")
    if normalized == "project" or project is not None:
        if normalized == "workspace":
            raise ValidationError("workspace scope cannot include project")
        return validate_scope("project", project)
    if normalized == "workspace" or workspace is not None:
        return validate_scope("workspace", workspace)
    return validate_scope(normalized)


def scope_from_tags(tags: str) -> tuple[ScopeSpec, list[str]]:
    """Convert one unambiguous project/workspace tag; retain/report all others."""
    found: list[ScopeSpec] = []
    malformed: list[str] = []
    for raw in (tags or "").split(","):
        token = raw.strip()
        if not token:
            continue
        if token.startswith(("project:", "workspace:")):
            match = TAG_SCOPE_RE.fullmatch(token)
            if match and match.group(2).strip():
                found.append(ScopeSpec(match.group(1), match.group(2).strip()))
            else:
                malformed.append(token)
    unique = {(item.kind, item.key.casefold()): item for item in found}
    if malformed or len(unique) != 1:
        issues = malformed
        if len(unique) > 1:
            issues.append("conflicting structured scope tags")
        return ScopeSpec("user", ""), issues
    return next(iter(unique.values())), []
