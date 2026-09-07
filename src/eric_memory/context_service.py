"""Project-authorized context capture and bounded recall for every harness."""

from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import TYPE_CHECKING, Any

from .content_policy import inspect_context_content
from .context_store import MAX_DOCUMENT_BYTES, ContextStore, bounded_results, encoded_size
from .errors import ContentRejectedError, NotFoundError, PermissionRequiredError, ValidationError
from .files import SKIP_DIR_NAMES, _matches
from .permissions import CONTEXT_READ, CONTEXT_WRITE, FACT_SEARCH
from .scopes import ScopeSpec, validate_scope

if TYPE_CHECKING:
    from .service import MemoryService


def validate_project(project: str) -> ScopeSpec:
    return validate_scope("project", project)


def _identity(info: os.stat_result) -> tuple[int, int, int, int, int]:
    # CPython 3.12 Windows lstat retains creation time in st_ctime while fstat
    # exposes change time. Birth time is consistent across both APIs.
    stamp = getattr(info, "st_birthtime_ns", info.st_ctime_ns) if os.name == "nt" else info.st_ctime_ns
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, stamp


def read_source(source: dict[str, Any], locator: str) -> str:
    """Read one stable regular UTF-8 file; no traversal, symlinks or pipe inputs."""
    parts = PurePosixPath(locator).parts
    if (
        not parts
        or len(locator) > 1000
        or "\\" in locator
        or ":" in locator
        or "\x00" in locator
        or PurePosixPath(locator).is_absolute()
        or PureWindowsPath(locator).is_absolute()
        or any(part in {".", ".."} or part.startswith(".") or part in SKIP_DIR_NAMES for part in parts)
        or str(PurePosixPath(locator)) != locator
    ):
        raise ValidationError("source_locator must be a relative, visible file path under the approved source")
    if not _matches(
        locator,
        include=source["include"],
        exclude=source["exclude"],
        file_types={item.casefold().lstrip(".") for item in source["file_types"]},
    ):
        raise PermissionRequiredError("file does not match the approved source rules")
    root = Path(source["canonical_root"])
    path = root.joinpath(*parts)
    maximum = min(MAX_DOCUMENT_BYTES, int(source["max_file_bytes"]))
    descriptors: list[int] = []
    try:
        # Inspect each component, including root ancestors on Windows/macOS.
        current = root
        for parent in reversed(root.parents):
            if parent.is_symlink():
                raise ValidationError("source root contains a symbolic link")
        for component in (root, *(root.joinpath(*parts[:i]) for i in range(1, len(parts) + 1))):
            info = component.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise ValidationError("symbolic links and reparse points cannot be indexed")
            current = component
        expected = current.lstat()
        if not stat.S_ISREG(expected.st_mode) or expected.st_nlink != 1:
            raise ValidationError("context input must be a regular file with one hard link")
        if expected.st_size > maximum:
            raise ValidationError("context input exceeds the approved file size limit")
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        # dir_fd traversal pins intermediate directories on POSIX. Windows
        # relies on component/reparse inspection and the opened-file identity.
        if os.open in os.supports_dir_fd and hasattr(os, "O_DIRECTORY"):
            directory_fd = os.open(root, flags | os.O_DIRECTORY)
            descriptors.append(directory_fd)
            for segment in parts[:-1]:
                directory_fd = os.open(segment, flags | os.O_DIRECTORY, dir_fd=directory_fd)
                descriptors.append(directory_fd)
            fd = os.open(parts[-1], flags, dir_fd=directory_fd)
        else:
            fd = os.open(path, flags)
        descriptors.append(fd)
        opened = os.fstat(fd)
        if not stat.S_ISREG(opened.st_mode) or _identity(expected) != _identity(opened):
            raise ValidationError("source changed before capture; retry once the file is stable")
        pieces: list[bytes] = []
        size = 0
        while True:
            block = os.read(fd, min(65536, maximum + 1 - size))
            if not block:
                break
            pieces.append(block)
            size += len(block)
            if size > maximum:
                raise ValidationError("context input exceeds the approved file size limit")
        after_read = os.fstat(fd)
        if (
            _identity(opened) != _identity(after_read)
            or opened.st_ctime_ns != after_read.st_ctime_ns
            or _identity(opened) != _identity(path.lstat())
        ):
            raise ValidationError("source changed during capture; retry once the file is stable")
        if not path.resolve(strict=True).is_relative_to(root):
            raise ValidationError("source escaped its approved directory")
        raw = b"".join(pieces)
        if b"\x00" in raw:
            raise ValidationError("context input must be UTF-8 text")
        text = raw.decode("utf-8")
        if not text.strip():
            raise ValidationError("context input is empty")
        return text
    except (OSError, UnicodeError) as exc:
        raise ValidationError("approved context file could not be read as stable UTF-8 text") from exc
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


class ContextService:
    def __init__(self, memory: MemoryService) -> None:
        self.memory = memory
        self.store = ContextStore(memory.data_dir, memory.store.library_uid)

    def _authorize(self, capability: str, project: str) -> ScopeSpec:
        scope = validate_project(project)
        self.memory._authorize(capability, scope=scope)
        return scope

    def _allowed_sources(self, project: str) -> dict[str, str]:
        allowed: dict[str, str] = {}
        for item in self.store.sources(project):
            try:
                source = self.memory.store.get_source(item["source_uid"])
            except NotFoundError:
                continue
            if source["status"] == "approved" and source["consent_uid"] == item["consent_uid"]:
                allowed[source["source_uid"]] = source["consent_uid"]
        return allowed

    def enable(
        self, project: str, source_uid: str, *, ttl_days: int = 7, allow_content_storage: bool = False
    ) -> dict[str, Any]:
        self.memory._require_local_admin()
        project = validate_project(project).key.casefold()
        self.memory._validate_safe_metadata(project)
        if not allow_content_storage:
            raise PermissionRequiredError("temporary source-body storage requires --allow-content-storage")
        source = self.memory.store.get_source(source_uid)
        if source["status"] != "approved":
            raise PermissionRequiredError("source approval is required before enabling temporary storage")
        return self.store.enable(project, source_uid, source["consent_uid"], ttl_days)

    def disable(self, project: str, source_uid: str | None = None) -> dict[str, Any]:
        self.memory._require_local_admin()
        project = validate_project(project).key.casefold()
        return self.store.disable(project, source_uid)

    def clean(self) -> dict[str, Any]:
        self.memory._require_local_admin()
        return self.store.clean()

    def reset(self) -> dict[str, Any]:
        self.memory._require_local_admin()
        return self.store.reset()

    def status(self, project: str) -> dict[str, Any]:
        project = validate_project(project).key.casefold()
        self._authorize(CONTEXT_READ, project)
        sources = self._allowed_sources(project)
        return {"project": project, "enabled_sources": list(sources), "enabled": bool(sources)}

    def index(
        self, project: str, source_uid: str, source_locator: str, *, label: str = "", expected_sha256: str | None = None
    ) -> dict[str, Any]:
        project = validate_project(project).key.casefold()
        self._authorize(CONTEXT_WRITE, project)
        self._authorize(CONTEXT_READ, project)
        self.memory._validate_safe_metadata(label, source_locator=source_locator)
        if len(label) > 200:
            raise ValidationError("label must be at most 200 characters")
        source = self.memory._authorize_source_scan(source_uid)
        if self._allowed_sources(project).get(source_uid) != source["consent_uid"]:
            raise PermissionRequiredError("temporary content storage is not enabled for this project and source")
        content = read_source(source, source_locator)
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        if expected_sha256 is not None and expected_sha256 != digest:
            raise ValidationError("source hash changed; capture a stable file before indexing")
        decision = inspect_context_content(content)
        if decision.action != "allow":
            raise ContentRejectedError(
                "context input rejected before storage", details={"policy_codes": list(decision.codes)}
            )
        # Source consent can change while the file is read. Verify again before
        # storing; reads also re-check consent, so revoked data cannot reappear.
        latest = self.memory._authorize_source_scan(source_uid)
        if latest["status"] != "approved" or latest["consent_uid"] != source["consent_uid"]:
            raise PermissionRequiredError("source consent changed during capture")
        return self.store.index(
            project=project,
            source=source,
            locator=source_locator,
            label=label or source_locator,
            content=content,
        )

    def recall(self, query: str, project: str, *, limit: int = 6, max_bytes: int = 8192) -> dict[str, Any]:
        project = validate_project(project).key.casefold()
        self._authorize(CONTEXT_READ, project)
        if not query.strip() or len(query) > 256 or not 1 <= limit <= 20:
            raise ValidationError("query must contain 1-256 characters and limit must be 1-20")
        self.memory._validate_safe_metadata(query)
        facts_access = True
        try:
            self._authorize(FACT_SEARCH, project)
            facts = self.memory.search(query, limit=limit, scope="project", project=project, include_files=False)[
                "facts"
            ]
        except PermissionRequiredError:
            facts, facts_access = [], False
        entries: list[dict[str, Any]] = [
            {
                "kind": "fact",
                "status": "active",
                "fact_id": fact["fact_id"],
                "content": fact["content"],
                "as_of": fact["as_of"],
                "source_ref": fact.get("source_ref", ""),
            }
            for fact in facts
        ]
        context = self.store.search(project, query, self._allowed_sources(project), limit)
        # Reserve results for both types; their ranking scores have different units.
        combined = []
        for i in range(max(len(entries), len(context))):
            if i < len(entries):
                combined.append(entries[i])
            if i < len(context):
                combined.append(context[i])
        return bounded_results(
            {
                "project": project,
                "query": query,
                "context_is_untrusted": True,
                "facts_access": facts_access,
                "next": "Use memory_context_read with artifact_uid and line numbers for exact text.",
            },
            combined[:limit],
            max_bytes,
        )

    def read(
        self,
        project: str,
        artifact_uid: str,
        *,
        start_line: int = 1,
        start_column: int = 1,
        line_count: int = 40,
        max_bytes: int = 8192,
    ) -> dict[str, Any]:
        project = validate_project(project).key.casefold()
        self._authorize(CONTEXT_READ, project)
        if start_line < 1 or start_column < 1 or not 1 <= line_count <= 200:
            raise ValidationError("start_line/start_column must be positive and line_count must be 1-200")
        artifact = self.store.read(project, artifact_uid, self._allowed_sources(project))
        lines = artifact["content"].splitlines(keepends=True)
        if start_line > len(lines):
            raise ValidationError("start_line is past the captured document")
        if start_column > len(lines[start_line - 1]):
            raise ValidationError("start_column is past the requested line")
        entries = [
            {
                "line": i + 1,
                "column": start_column if i == start_line - 1 else 1,
                "content": lines[i][start_column - 1 :] if i == start_line - 1 else lines[i],
            }
            for i in range(start_line - 1, min(len(lines), start_line - 1 + line_count))
        ]
        payload = bounded_results(
            {
                "project": project,
                "artifact_uid": artifact_uid,
                "status": "temporary",
                "context_is_untrusted": True,
                "source_uid": artifact["source_uid"],
                "source_locator": artifact["source_locator"],
                "sha256": artifact["sha256"],
                "total_lines": len(lines),
                "expires_at": artifact["expires_at"],
                "next_line": 99999999,
                "next_column": 99999999,
            },
            entries,
            max_bytes,
            sequential=True,
        )
        last = payload["results"][-1] if payload["results"] else None
        next_line, next_column = start_line, start_column
        if last:
            next_line, next_column = last["line"], last["column"] + len(last["content"])
            if next_column > len(lines[next_line - 1]):
                next_line, next_column = next_line + 1, 1
        payload["next_line"] = next_line if next_line <= len(lines) else None
        payload["next_column"] = next_column if next_line <= len(lines) else None
        for _ in range(5):
            payload["returned_bytes"] = encoded_size(payload)
        return payload
