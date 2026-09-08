"""One service facade shared by the CLI and every MCP transport."""

from __future__ import annotations

import json
import os
import uuid
from datetime import date
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from .backup import BackupManager, atomic_restore_database, validate_restore_schema
from .catalog import CATALOG, catalog_by_key, catalog_dicts
from .content_policy import inspect_content, inspect_metadata, validate_pointer
from .context_service import ContextService
from .doctor import create_support_bundle, doctor_report
from .errors import (
    ConfirmationRequiredError,
    ContentRejectedError,
    NotFoundError,
    PermissionRequiredError,
    ValidationError,
)
from .files import MAX_FILES, scan_folder
from .importer import import_holograph
from .migration import migrate_apply, migrate_rollback, migration_plan, rollback_plan
from .obsidian import copy_template, remove_managed_projection, write_vault
from .operations import OperationLogger
from .paths import (
    MemoryConfig,
    atomic_write_text,
    db_path_for,
    default_vault_dir,
    expand_once,
    load_config,
    resolve_data_dir,
    resource_root,
    save_config,
    write_pointer,
)
from .permissions import (
    CANDIDATE_READ_OWN,
    CANDIDATE_SUBMIT,
    FACT_SEARCH,
    FACT_WRITE,
    FILE_SEARCH,
    HISTORY_READ,
    MANAGE,
    SOURCE_SCAN,
    STATUS_READ,
    SYNC_RUN,
)
from .scopes import ScopeSpec, scope_from_request, scope_from_tags, validate_scope
from .search import search_facts
from .store import ConnectionMode, MemoryStore, normalized_content_hash, today_utc, utc_now


class MemoryService:
    def __init__(
        self,
        data_dir: str | Path | None = None,
        *,
        mode: ConnectionMode = "rw-existing",
        principal: str = "local",
    ) -> None:
        self.data_dir = resolve_data_dir(data_dir)
        self.config = load_config(self.data_dir)
        db_path = Path(self.config.db_path) if self.config else db_path_for(self.data_dir)
        self.store = MemoryStore(db_path, mode=mode)
        self.mode = mode
        self.principal_key = (principal or "legacy").strip().lower()
        self.backups = BackupManager(self.data_dir)
        self.logger = OperationLogger(self.data_dir)

    @classmethod
    def for_init(cls, data_dir: str | Path | None = None, *, principal: str = "local") -> MemoryService:
        return cls(data_dir, mode="create", principal=principal)

    def close(self) -> None:
        self.store.close()

    @property
    def context(self) -> ContextService:
        return ContextService(self)

    def _authorize(self, capability: str, *, scope: ScopeSpec | None = None) -> None:
        if self.store.schema_version < 2:
            if capability in {STATUS_READ, FACT_SEARCH}:
                return
            if self.principal_key == "local" and capability == HISTORY_READ:
                return
            raise PermissionRequiredError("schema v1 permits only status/search until migration")
        self.store.require_capability(self.principal_key, capability, scope=scope)

    def _require_local_admin(self) -> None:
        if self.principal_key != "local":
            raise PermissionRequiredError("this operation requires the interactive local administrator")
        if self.store.schema_version < 2:
            return
        info = self.store.principal_info(self.principal_key)
        if not info["local_admin"]:
            raise PermissionRequiredError("this operation requires the interactive local administrator")

    def _authorize_fact(self, capability: str, fact_id: int) -> None:
        self._authorize(capability)
        fact = self.store.get_fact(fact_id)
        if fact is None:
            raise NotFoundError(f"fact_id {fact_id} not found")
        self._authorize(capability, scope=validate_scope(fact.scope["kind"], fact.scope["key"]))

    def _before_write(self) -> dict[str, Any] | None:
        self._require_local_writable_schema()
        return self.backups.maybe_daily(self.store)

    def _require_local_writable_schema(self) -> None:
        if self.store.mode == "ro":
            raise PermissionError("command requires a writable database connection")
        self.store._require_v2()

    def _log(
        self,
        operation_uid: str,
        action: str,
        outcome: str = "success",
        *,
        error_code: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.logger.write(
            operation_uid=operation_uid,
            action=action,
            outcome=outcome,
            principal=self.principal_key,
            error_code=error_code,
            metadata=metadata,
        )

    def _log_after_commit(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        try:
            self._log(*args, **kwargs)
        except Exception as exc:  # committed SQLite state remains authoritative
            return {"operation_log": {"status": "failed", "error_code": type(exc).__name__}}
        return {}

    @staticmethod
    def _validate_safe_metadata(*values: str, source_locator: str = "") -> None:
        decision = inspect_metadata([value for value in values if value], source_locator=source_locator)
        if decision.action != "allow":
            raise ContentRejectedError(
                "metadata was rejected before persistence",
                details={"policy_codes": list(decision.codes)},
            )

    def _project_after_commit(self, payload: dict[str, Any], operation_uid: str) -> dict[str, Any]:
        payload["committed"] = True
        payload["operation_uid"] = operation_uid
        if self.config is None or not self.config.obsidian_enabled:
            payload["projection"] = "disabled"
            try:
                self.store.set_projection_state("disabled", operation_uid=operation_uid)
            except Exception as exc:  # state metadata must not turn a committed write into a failure
                payload["projection_state_error"] = type(exc).__name__
            return payload
        try:
            write_vault(self.store, self.config.vault_dir)
            self.store.set_projection_state("clean", operation_uid=operation_uid)
            payload["projection"] = "clean"
        except Exception as exc:  # noqa: BLE001 - database commit remains authoritative
            try:
                self.store.set_projection_state("dirty", operation_uid=operation_uid, error_code=type(exc).__name__)
            except Exception as state_exc:
                payload["projection_state_error"] = type(state_exc).__name__
            payload["projection"] = "dirty"
            warning_key = "projection_warning" if "warning" in payload else "warning"
            payload[warning_key] = {
                "code": "PROJECTION_DIRTY",
                "message": "database commit succeeded; run `eric-memory sync` to repair the projection",
            }
        return payload

    def init(
        self,
        *,
        data_dir: str | Path | None = None,
        vault_dir: str | Path | None = None,
        tier: str = "simple",
        folders: list[str] | None = None,
        write_repo_pointer: bool = False,
        obsidian_enabled: bool = True,
    ) -> dict[str, Any]:
        if self.store.schema_version != 2:
            raise ValidationError("init requires a new or existing schema-v2 database")
        target = resolve_data_dir(data_dir) if data_dir else self.data_dir
        if target != self.data_dir:
            self.store.close()
            self.data_dir = target
            self.store = MemoryStore(db_path_for(target), mode="create")
            self.backups = BackupManager(target)
            self.logger = OperationLogger(target)
        target.mkdir(parents=True, exist_ok=True, mode=0o700)
        if os.name != "nt":
            os.chmod(target, 0o700)
        vault = expand_once(vault_dir, name="vault dir") if vault_dir else default_vault_dir(target)
        cfg = MemoryConfig(
            data_dir=str(target),
            vault_dir=str(vault),
            db_path=str(db_path_for(target)),
            tier="unified",
            obsidian_enabled=obsidian_enabled,
        )
        save_config(cfg)
        if write_repo_pointer:
            write_pointer(target)
        self.config = cfg
        registered_folders: list[dict[str, Any]] = []
        if obsidian_enabled:
            vault.mkdir(parents=True, exist_ok=True)
            copy_template(resource_root() / "vault-template", vault)
        for folder in folders or []:
            registered_folders.append(self.add_folder(folder))
        if obsidian_enabled:
            write_vault(self.store, vault)
            self.store.set_projection_state("clean")
        else:
            self.store.set_projection_state("disabled")
        return {
            "data_dir": cfg.data_dir,
            "db_path": cfg.db_path,
            "vault_dir": cfg.vault_dir,
            "tier": cfg.tier,
            "folders": registered_folders,
            "schema_version": 2,
            "library_uid": self.store.library_uid,
            "warning": (
                "--tier is deprecated and ignored; With. v1 uses one unified mode"
                if tier in {"simple", "full"}
                else None
            ),
        }

    def entity_cleanup_plan(self) -> dict[str, Any]:
        self._require_local_admin()
        proposals = self.store.entity_cleanup_plan()
        return {
            "dry_run": True,
            "proposals": proposals,
            "applied": 0,
            "note": "Review these possible import leftovers before removing any entity links.",
        }

    def status(self) -> dict[str, Any]:
        self._authorize(STATUS_READ)
        cfg = self.config or load_config(self.data_dir)
        if self.principal_key == "local":
            harnesses = [item.to_dict() for item in self.store.list_harnesses()]
            folders = self.store.list_folders()
            sources = self.store.list_sources() if self.store.schema_version >= 2 else []
        elif self.principal_key == "legacy" or self.store.schema_version < 2:
            harnesses = []
            folders = []
            sources = []
        else:
            harness = self.store.get_harness(self.principal_key)
            harnesses = [harness.to_dict()] if harness else []
            sources = [item for item in self.store.list_sources() if item["harness_key"] == self.principal_key]
            allowed_roots = {str(item["canonical_root"]) for item in sources}
            folders = [item for item in self.store.list_folders() if str(item["path"]) in allowed_roots]
        return {
            "data_dir": str(self.data_dir) if self.principal_key == "local" else "<redacted>",
            "db_path": str(self.store.db_path) if self.principal_key == "local" else "<redacted>",
            "vault_dir": (cfg.vault_dir if cfg else str(default_vault_dir(self.data_dir)))
            if self.principal_key == "local"
            else "<redacted>",
            "tier": "unified" if self.store.schema_version >= 2 else (cfg.tier if cfg else "legacy"),
            "schema_version": self.store.schema_version,
            "library_uid": self.store.library_uid if self.store.schema_version >= 2 else None,
            "counts": self.store.counts(),
            "harnesses": harnesses,
            "folders": folders,
            "sources": sources,
            "projection": self.store.projection_state(),
            "catalog": catalog_dicts(),
        }

    def _scope_for_add(
        self,
        tags: str,
        *,
        scope: str | None,
        project: str | None,
        workspace: str | None,
    ) -> ScopeSpec:
        if scope is not None or project is not None or workspace is not None:
            return scope_from_request(scope or "user", project=project, workspace=workspace)
        return scope_from_tags(tags)[0]

    def add(
        self,
        content: str,
        *,
        category: str = "general",
        tags: str = "",
        entities: list[str] | None = None,
        as_of: str | None = None,
        trust: float = 0.5,
        supersedes: int | None = None,
        scope: str | None = None,
        project: str | None = None,
        workspace: str | None = None,
        actor: str | None = None,
    ) -> dict[str, Any]:
        scope_spec = self._scope_for_add(tags, scope=scope, project=project, workspace=workspace)
        self._authorize(FACT_WRITE, scope=scope_spec)
        if supersedes is not None:
            self._authorize_fact(FACT_WRITE, supersedes)
        self._validate_safe_metadata(scope_spec.key, category, tags, *(entities or []))
        value = validate_pointer(content)
        decision = inspect_content(value)
        if decision.action == "reject":
            raise ContentRejectedError(
                "content was rejected before persistence",
                details={"policy_codes": list(decision.codes)},
            )
        if decision.action == "quarantine":
            quarantined = self.candidate_add(
                content=value,
                submission_uid=str(uuid.uuid4()),
                category=category,
                entities=entities,
                as_of=self._iso_day(as_of),
                confidence=trust,
                scope=scope_spec.kind,
                project=scope_spec.key if scope_spec.kind == "project" else None,
                workspace=scope_spec.key if scope_spec.kind == "workspace" else None,
                manual_input=True,
            )
            return {
                "quarantined_candidate": quarantined["candidate"],
                "committed": True,
                "projection": "unchanged",
                "warning": {
                    "code": "CONTENT_QUARANTINED",
                    "message": "submit a redacted 1–3 sentence pointer",
                },
            }
        operation_uid = str(uuid.uuid4())
        self._before_write()
        if supersedes is None:
            fact, created = self.store.add_fact_with_result(
                value,
                category=category,
                tags=tags,
                entities=entities,
                as_of=self._iso_day(as_of),
                trust=trust,
                scope=scope_spec,
                actor=actor or self.principal_key,
                operation_uid=operation_uid,
            )
            deprecated = None
        else:
            fact, deprecated, created = self.store.add_and_supersede(
                value,
                int(supersedes),
                category=category,
                tags=tags,
                entities=entities,
                as_of=as_of,
                trust=trust,
                scope=scope_spec,
                actor=actor or self.principal_key,
                operation_uid=operation_uid,
            )
        payload: dict[str, Any] = {"fact": fact.to_dict(), "created": created}
        if deprecated is not None:
            payload["deprecated"] = deprecated.to_dict()
        payload.update(
            self._log_after_commit(operation_uid, "fact.add", metadata={"fact_uid": fact.fact_uid, "created": created})
        )
        return self._project_after_commit(payload, operation_uid)

    def search(
        self,
        query: str,
        *,
        limit: int = 10,
        include_deprecated: bool = False,
        scope: str = "user",
        project: str | None = None,
        workspace: str | None = None,
        include_files: bool = True,
    ) -> dict[str, Any]:
        requested_scope = scope_from_request(scope, project=project, workspace=workspace)
        self._authorize(FACT_SEARCH, scope=requested_scope if requested_scope.kind != "user" else None)
        if include_deprecated:
            self._authorize(HISTORY_READ, scope=requested_scope if requested_scope.kind != "user" else None)
        result = search_facts(
            self.store,
            query,
            limit=limit,
            include_deprecated=include_deprecated,
            scope=scope,
            project=project,
            workspace=workspace,
            principal_key=self.principal_key,
        )
        payload = {
            "layer": result["layer"],
            "rank_version": result.get("rank_version", "rrf-v1"),
            "query": result["query"],
            "include_deprecated": result["include_deprecated"],
            "scope": result["scope"],
            "facts": [fact.to_dict() for fact in result["facts"]],
        }
        if include_files and self.store.schema_version >= 2:
            self._authorize(FILE_SEARCH)
            payload["files"] = self.store.search_files(query, limit=min(10, limit))
        return payload

    def deprecate(
        self,
        fact_id: int,
        *,
        superseded_by: int | None = None,
        reason: str = "",
        actor: str | None = None,
    ) -> dict[str, Any]:
        self._authorize_fact(FACT_WRITE, fact_id)
        if superseded_by is not None:
            self._authorize_fact(FACT_WRITE, superseded_by)
        self._validate_safe_metadata(reason)
        operation_uid = str(uuid.uuid4())
        self._before_write()
        fact = self.store.deprecate(
            fact_id,
            superseded_by=superseded_by,
            reason=reason,
            actor=actor or self.principal_key,
            operation_uid=operation_uid,
        )
        log_result = self._log_after_commit(operation_uid, "fact.deprecate", metadata={"fact_uid": fact.fact_uid})
        return self._project_after_commit({"fact": fact.to_dict(), **log_result}, operation_uid)

    def redact(self, fact_id: int, *, reason: str = "") -> dict[str, Any]:
        """Local admin only: wipe credential text from an existing fact."""
        self._require_local_admin()
        self._authorize_fact(FACT_WRITE, fact_id)
        self._validate_safe_metadata(reason)
        fact = self.store.get_fact(fact_id)
        if fact is None:
            raise NotFoundError(f"fact_id {fact_id} not found")
        decision = inspect_content(fact.content)
        if decision.action != "reject":
            raise ValidationError("redact is only for facts that fail explicit credential policy")
        operation_uid = str(uuid.uuid4())
        self._before_write()
        updated = self.store.redact_fact(fact_id, actor=self.principal_key, operation_uid=operation_uid, reason=reason)
        log_result = self._log_after_commit(operation_uid, "fact.redact", metadata={"fact_uid": updated.fact_uid})
        return self._project_after_commit({"fact": updated.to_dict(), **log_result}, operation_uid)

    # Candidates and local review -----------------------------------------
    @staticmethod
    def _iso_day(value: str | None) -> str:
        day = value or today_utc()
        try:
            date.fromisoformat(day)
        except ValueError as exc:
            raise ValidationError("as_of must be an ISO date (YYYY-MM-DD)") from exc
        return day

    def candidate_add(
        self,
        *,
        content: str,
        submission_uid: str,
        category: str = "general",
        entities: list[str] | None = None,
        as_of: str | None = None,
        confidence: float = 0.5,
        scope: str = "user",
        project: str | None = None,
        workspace: str | None = None,
        source_uid: str | None = None,
        source_locator: str = "",
        manual_input: bool = False,
    ) -> dict[str, Any]:
        scope_spec = scope_from_request(scope, project=project, workspace=workspace)
        self._authorize(CANDIDATE_SUBMIT, scope=scope_spec)
        self._authorize(FACT_SEARCH, scope=scope_spec)
        self._validate_safe_metadata(
            scope_spec.key, submission_uid, category, *(entities or []), source_locator=source_locator
        )
        if manual_input:
            self._require_local_admin()
        elif not source_uid:
            raise ValidationError("harness candidates require source_uid/source_locator")
        if source_uid and not source_locator.strip():
            raise ValidationError("source_locator is required when source_uid is provided")
        locator = source_locator.strip()
        if "\x00" in locator or len(locator) > 1000:
            raise ValidationError("source_locator is invalid or too long")
        if source_uid:
            source = self.store.get_source(source_uid)
            if source["status"] != "approved":
                raise ValidationError("candidate source is revoked")
            if self.principal_key != "local":
                if source["harness_key"] != self.principal_key:
                    raise PermissionRequiredError("candidate source is not assigned to this principal")
                locator_path = PureWindowsPath(locator)
                if PurePosixPath(locator).is_absolute() or locator_path.is_absolute():
                    raise ValidationError("harness source_locator must be relative to the approved root")
                if any(part in {".", ".."} for part in (*PurePosixPath(locator).parts, *locator_path.parts)):
                    raise ValidationError("harness source_locator must not traverse directories")
        value = validate_pointer(content)
        decision = inspect_content(value)
        if decision.action == "reject":
            raise ContentRejectedError(
                "content was rejected before persistence",
                details={"policy_codes": list(decision.codes)},
            )
        operation_uid = str(uuid.uuid4())
        self._before_write()
        self._expire_candidates_before_read()
        candidate, created = self.store.add_candidate(
            submission_uid=submission_uid,
            principal_key=self.principal_key,
            content=value if decision.action == "allow" else None,
            content_summary="" if decision.action == "allow" else decision.safe_summary,
            category=category,
            entities=entities or [],
            as_of=self._iso_day(as_of),
            confidence=confidence,
            scope=scope_spec,
            source_uid=source_uid,
            source_locator=locator,
            manual_input=manual_input,
            status="pending" if decision.action == "allow" else "quarantined",
            policy_codes=list(decision.codes),
            actor=self.principal_key,
            operation_uid=operation_uid,
        )
        log_result = self._log_after_commit(
            operation_uid,
            "candidate.submit",
            metadata={
                "candidate_uid": candidate.candidate_uid,
                "status": candidate.status,
                "created": created,
            },
        )
        return self._project_after_commit(
            {
                "candidate": candidate.to_dict(),
                "created": created,
                **log_result,
            },
            operation_uid,
        )

    def candidate_list(self, *, status: str | None = "pending", limit: int = 100) -> dict[str, Any]:
        self._authorize(CANDIDATE_READ_OWN)
        allowed = {None, "pending", "accepted", "rejected", "expired", "quarantined"}
        if status not in allowed:
            raise ValidationError("invalid candidate status")
        self._expire_candidates_before_read()
        principal = None if self.principal_key == "local" else self.principal_key
        return {
            "candidates": [
                item.to_dict()
                for item in self.store.list_candidates(status=status, principal_key=principal, limit=limit)
            ]
        }

    def candidate_show(self, candidate_uid: str) -> dict[str, Any]:
        self._authorize(CANDIDATE_READ_OWN)
        self._expire_candidates_before_read()
        candidate = self.store.get_candidate(candidate_uid)
        if self.principal_key != "local" and candidate.principal_key != self.principal_key:
            raise PermissionRequiredError("candidate belongs to another principal")
        return {"candidate": candidate.to_dict()}

    def review_context(self, candidate_uid: str) -> dict[str, Any]:
        self._require_local_admin()
        self._expire_candidates_before_read()
        candidate = self.store.get_candidate(candidate_uid)
        query = " ".join(candidate.entities) or (candidate.content or "")[:128]
        related = self.search(query, include_files=False, limit=10) if query else {"facts": []}
        return {
            "candidate": candidate.to_dict(),
            "source": self.store.get_source(candidate.source_uid) if candidate.source_uid else None,
            "related_active_facts": related["facts"],
        }

    def _expire_candidates_before_read(self) -> None:
        if self.store.expired_candidate_count():
            self._before_write()
            self.store.expire_candidates()

    def review_accept(self, candidate_uid: str, *, supersedes: list[int] | None = None) -> dict[str, Any]:
        self._require_local_admin()
        for fact_id in supersedes or []:
            self._authorize_fact(FACT_WRITE, fact_id)
        operation_uid = str(uuid.uuid4())
        self._before_write()
        candidate, fact, deprecated = self.store.accept_candidate(
            candidate_uid,
            supersedes=supersedes,
            actor=self.principal_key,
            operation_uid=operation_uid,
        )
        payload = {
            "candidate": candidate.to_dict(),
            "fact": fact.to_dict(),
            "deprecated": [item.to_dict() for item in deprecated],
        }
        payload.update(
            self._log_after_commit(operation_uid, "candidate.accept", metadata={"candidate_uid": candidate_uid})
        )
        return self._project_after_commit(payload, operation_uid)

    def review_reject(self, candidate_uid: str, *, reason: str = "") -> dict[str, Any]:
        self._require_local_admin()
        self._validate_safe_metadata(reason)
        operation_uid = str(uuid.uuid4())
        self._before_write()
        candidate = self.store.reject_candidate(
            candidate_uid, reason=reason, actor=self.principal_key, operation_uid=operation_uid
        )
        log_result = self._log_after_commit(
            operation_uid, "candidate.reject", metadata={"candidate_uid": candidate_uid}
        )
        return self._project_after_commit(
            {"candidate": candidate.to_dict(), **log_result},
            operation_uid,
        )

    # Consent, scanning, and harvest --------------------------------------
    @staticmethod
    def _canonical_source_root(
        root: str | Path,
        *,
        home_confirmation: str | None = None,
    ) -> Path:
        requested = expand_once(root, name="source root", resolve=False)
        # Reject a symlink as the approved root itself. Platform aliases such
        # as macOS /var -> /private/var are canonicalized below; every entry
        # encountered beneath the canonical root is still lstat'ed and all
        # symlinks are skipped by the scanner.
        try:
            if requested.is_symlink():
                raise ValidationError("source root must not be a symbolic link")
        except OSError as exc:
            raise ValidationError("source root could not be inspected") from exc
        canonical = requested.resolve(strict=True)
        if not canonical.is_dir():
            raise ValidationError("source root must be a directory")
        if canonical == Path(canonical.anchor):
            raise ValidationError("operating-system roots cannot be approved")
        home = Path.home().resolve()
        if canonical == home and home_confirmation != str(canonical):
            raise ConfirmationRequiredError(
                "approving the entire home directory requires typing its complete canonical path",
                details={"confirmation": str(canonical)},
            )
        return canonical

    def source_approve(
        self,
        root: str,
        *,
        harness_key: str = "",
        include: list[str] | None = None,
        exclude: list[str] | None = None,
        file_types: list[str] | None = None,
        max_file_bytes: int = 50 * 1024 * 1024,
        home_confirmation: str | None = None,
    ) -> dict[str, Any]:
        self._require_local_admin()
        canonical = self._canonical_source_root(root, home_confirmation=home_confirmation)
        self._before_write()
        if harness_key:
            try:
                self.store.principal_info(harness_key)
            except PermissionRequiredError:
                self.store.ensure_principal(harness_key, display_name=harness_key)
        operation_uid = str(uuid.uuid4())
        source = self.store.approve_source(
            canonical,
            harness_key=harness_key,
            include=include,
            exclude=exclude,
            file_types=file_types,
            max_file_bytes=max_file_bytes,
            actor=self.principal_key,
        )
        self.store.upsert_folder(canonical)
        return self._project_after_commit({"source": source}, operation_uid)

    def source_list(self, *, include_revoked: bool = False) -> dict[str, Any]:
        self._authorize(STATUS_READ if self.principal_key == "local" else SOURCE_SCAN)
        self.store._require_v2()
        sources = self.store.list_sources(include_revoked=include_revoked)
        if self.principal_key != "local":
            sources = [item for item in sources if item["harness_key"] == self.principal_key]
        return {"sources": sources}

    def _authorize_source_scan(self, source_uid: str) -> dict[str, Any]:
        self._authorize(SOURCE_SCAN)
        source = self.store.get_source(source_uid)
        if self.principal_key != "local" and source["harness_key"] != self.principal_key:
            raise PermissionRequiredError("source is not assigned to this principal")
        return source

    def harvest_begin(self, source_uid: str, *, max_files: int = MAX_FILES) -> dict[str, Any]:
        source = self._authorize_source_scan(source_uid)
        self._before_write()
        run_uid = self.store.start_scan(source_uid, self.principal_key)
        try:
            result = scan_folder(
                source["canonical_root"],
                previous=self.store.source_snapshot(source_uid),
                include=source["include"],
                exclude=source["exclude"],
                file_types=source["file_types"],
                max_file_bytes=source["max_file_bytes"],
                max_files=max_files,
            )
        except FileNotFoundError:
            stale = self.store.mark_source_missing(run_uid)
            return {
                "run_uid": run_uid,
                "source_uid": source_uid,
                "status": "error",
                "error": {"code": "SOURCE_MISSING", "stale": stale},
                "changed_files": [],
            }
        stats = self.store.apply_scan(
            run_uid,
            changed=result.changed,
            unchanged_paths=result.unchanged_paths,
            seen_paths=result.seen_paths,
            truncated=result.truncated,
            error_code="SCAN_TRUNCATED" if result.truncated else "",
        )
        return {
            "run_uid": run_uid,
            "source_uid": source_uid,
            "status": "error" if result.truncated else "open",
            "changed_files": result.changed,
            "stats": {**stats, "skipped": result.skipped},
            "error": ({"code": "SCAN_TRUNCATED", "max_files": max_files} if result.truncated else None),
        }

    def harvest_complete(self, run_uid: str, *, cursor: str) -> dict[str, Any]:
        self._authorize(SOURCE_SCAN)
        self._validate_safe_metadata(cursor)
        run = self.store.scan_run(run_uid)
        source = self._authorize_source_scan(str(run["source_uid"]))
        del source
        result = self.store.complete_scan(run_uid, cursor=cursor)
        return {"run": result, "committed": True}

    def harvest_abandon(self, run_uid: str) -> dict[str, Any]:
        self._authorize(SOURCE_SCAN)
        existing = self.store.scan_run(run_uid)
        self._authorize_source_scan(str(existing["source_uid"]))
        self._before_write()
        run = self.store.abandon_scan(run_uid, actor=self.principal_key)
        return {"run": run, "committed": True}

    def ack_scope_issues(self) -> dict[str, Any]:
        self._require_local_admin()
        self._before_write()
        acked = self.store.ack_migration_issues(actor=self.principal_key)
        return {"acked": acked, "committed": True}

    def resolve_scope_issue(
        self,
        fact_id: int,
        *,
        scope: str = "user",
        project: str | None = None,
        workspace: str | None = None,
    ) -> dict[str, Any]:
        self._require_local_admin()
        self._authorize_fact(FACT_WRITE, fact_id)
        scope_spec = scope_from_request(scope, project=project, workspace=workspace)
        self._before_write()
        result = self.store.resolve_scope_issue(fact_id, scope_spec, actor=self.principal_key)
        return {**result, "committed": True}

    def source_scan(self, source_uid: str, *, max_files: int = MAX_FILES) -> dict[str, Any]:
        result = self.harvest_begin(source_uid, max_files=max_files)
        if result["status"] == "open":
            completed = self.harvest_complete(result["run_uid"], cursor=utc_now())
            result["status"] = "complete"
            result["run"] = completed["run"]
        return result

    def source_revoke(self, source_uid: str) -> dict[str, Any]:
        self._require_local_admin()
        self._before_write()
        operation_uid = str(uuid.uuid4())
        source = self.store.revoke_source(source_uid, actor=self.principal_key)
        payload: dict[str, Any] = {"source": source}
        try:
            self.context.store.forget_source(source_uid)
            payload["context_cleanup"] = "complete"
        except Exception as exc:  # Revocation must take effect even if disposable storage is damaged.
            payload["context_cleanup"] = "pending"
            payload["warning"] = {
                "code": "CONTEXT_CLEANUP_PENDING",
                "error_code": type(exc).__name__,
                "message": "Source access is revoked. Retry source revoke, or use local context reset --confirm.",
            }
        return self._project_after_commit(payload, operation_uid)

    # Legacy folder/harness surfaces --------------------------------------
    def add_folder(self, path: str, *, label: str = "") -> dict[str, Any]:
        self._require_local_admin()
        canonical = self._canonical_source_root(path)
        self._before_write()
        stored = self.store.upsert_folder(canonical, label=label)
        source = self.store.source_by_root(canonical)
        return {
            "path": stored,
            "label": label,
            "source_uid": source["source_uid"] if source and source["status"] == "approved" else None,
            "approved": bool(source and source["status"] == "approved"),
            "warning": (
                None
                if source and source["status"] == "approved"
                else "folder registered but not readable; run local `source approve` before scanning"
            ),
        }

    def index_files(self, folder: str | None = None) -> dict[str, Any]:
        self._require_local_admin()
        targets: list[str]
        if folder:
            target = str(expand_once(folder, name="folder").resolve(strict=True))
            source = self.store.source_by_root(target)
            if source is None or source["status"] != "approved":
                raise ValidationError("folder is not an approved source; run local `source approve` first")
            targets = [target]
        else:
            targets = [
                str(item["canonical_root"]) for item in self.store.list_sources() if item["source_kind"] == "folder"
            ]
        indexed: list[dict[str, Any]] = []
        for target in targets:
            source = self.store.source_by_root(target)
            assert source is not None
            report = self.source_scan(str(source["source_uid"]))
            if report["status"] == "error":
                indexed.append(
                    {
                        "folder": target,
                        "files": 0,
                        "error": str(report["error"]["code"]).replace("SOURCE_", "").lower(),
                    }
                )
            else:
                indexed.append(
                    {
                        "folder": target,
                        "files": int(report["stats"]["changed"]) + int(report["stats"]["unchanged"]),
                    }
                )
        return {"indexed": indexed}

    def harness_add(
        self,
        key: str,
        *,
        display_name: str | None = None,
        session_root: str | None = None,
        mcp_mounted: bool | None = None,
        harvest_ok: bool | None = None,
        notes: str | None = None,
        actor: str | None = None,
    ) -> dict[str, Any]:
        self._require_local_admin()
        normalized = key.strip().lower()
        preset = catalog_by_key().get(normalized)
        existing = self.store.get_harness(normalized)
        name = display_name or (existing.display_name if existing else None) or (preset.display_name if preset else key)
        root = existing.session_root if existing else ""
        if session_root is not None:
            root = str(self._canonical_source_root(session_root)) if session_root else ""
        mounted = existing.mcp_mounted if existing and mcp_mounted is None else bool(mcp_mounted)
        harvest = existing.harvest_ok if existing and harvest_ok is None else bool(harvest_ok)
        stored_notes = existing.notes if existing and notes is None else (notes or "")
        if preset and preset.mainland_note and preset.mainland_note not in stored_notes:
            stored_notes = " ".join(item for item in (stored_notes, preset.mainland_note) if item)
        if harvest and not root:
            raise ValidationError("harvest requires an approved absolute session_root")
        if harvest:
            source = self.store.source_by_root(root)
            if source is None or source["status"] != "approved" or source["harness_key"] != normalized:
                raise ValidationError(
                    "harvest requires a source already approved and assigned by local `source approve`"
                )
        self._before_write()
        harness = self.store.upsert_harness(
            normalized,
            display_name=name,
            session_root=root,
            mcp_mounted=mounted,
            harvest_ok=harvest,
            notes=stored_notes,
            actor=actor or self.principal_key,
        )
        try:
            self.store.principal_info(normalized)
        except PermissionRequiredError:
            self.store.ensure_principal(normalized, display_name=name)
        return self._project_after_commit({"harness": harness.to_dict()}, str(uuid.uuid4()))

    def harness_list(self) -> dict[str, Any]:
        self._authorize(MANAGE)
        return {
            "harnesses": [item.to_dict() for item in self.store.list_harnesses()],
            "principals": self.store.list_principals() if self.principal_key == "local" else [],
            "catalog": catalog_dicts(),
        }

    def harness_grant(
        self,
        key: str,
        capability: str,
        *,
        scope: str | None = None,
        scope_key: str | None = None,
    ) -> dict[str, Any]:
        self._require_local_admin()
        spec = validate_scope(scope, scope_key) if scope else None
        self._validate_safe_metadata(spec.key if spec else "")
        self._before_write()
        return {"principal": self.store.grant(key, capability, scope=spec, actor=self.principal_key)}

    def harness_revoke_grant(
        self,
        key: str,
        capability: str,
        *,
        scope: str | None = None,
        scope_key: str | None = None,
    ) -> dict[str, Any]:
        self._require_local_admin()
        self._before_write()
        spec = validate_scope(scope, scope_key) if scope else None
        return {"principal": self.store.revoke_grant(key, capability, scope=spec, actor=self.principal_key)}

    def catalog(self) -> dict[str, Any]:
        return {"catalog": catalog_dicts()}

    # Maintenance and exports ---------------------------------------------
    def import_holograph(self, source: str, *, actor: str | None = None) -> dict[str, Any]:
        self._require_local_admin()
        self._before_write()
        report = import_holograph(
            self.store, expand_once(source, name="holograph db"), actor=actor or self.principal_key
        )
        return self._project_after_commit(report, str(uuid.uuid4()))

    def sync(self, *, actor: str | None = None, projection_only: bool = False) -> dict[str, Any]:
        self._authorize(SYNC_RUN)
        if self.config is None:
            raise ValidationError("memory is not initialized; run `eric-memory init`")
        self._before_write()
        scans: list[dict[str, Any]] = []
        for source in [] if projection_only else self.store.list_sources():
            if source["source_kind"] != "folder":
                continue
            if self.principal_key != "local" and source["harness_key"] != self.principal_key:
                continue
            scans.append(self.source_scan(str(source["source_uid"])))
        operation_uid = str(uuid.uuid4())
        payload: dict[str, Any] = {
            "vault_dir": self.config.vault_dir,
            "scans": scans,
            "files": {
                "indexed": [
                    {
                        "folder": self.store.get_source(item["source_uid"])["canonical_root"],
                        "files": (
                            int(item.get("stats", {}).get("changed", 0))
                            + int(item.get("stats", {}).get("unchanged", 0))
                        ),
                        **({"error": item["error"]["code"]} if item.get("error") else {}),
                    }
                    for item in scans
                ]
            },
            "harvest": [
                {
                    "key": self.store.get_source(item["source_uid"])["harness_key"],
                    "session_root": self.store.get_source(item["source_uid"])["canonical_root"],
                    "files": int(item.get("stats", {}).get("changed", 0))
                    + int(item.get("stats", {}).get("unchanged", 0)),
                }
                for item in scans
                if self.store.get_source(item["source_uid"])["harness_key"]
            ],
            "counts": self.store.counts(),
            "actor": actor or self.principal_key,
        }
        if self.config.obsidian_enabled:
            try:
                payload["pages"] = write_vault(self.store, self.config.vault_dir)
                self.store.set_projection_state("clean", operation_uid=operation_uid)
                payload["projection"] = "clean"
            except Exception as exc:  # noqa: BLE001
                self.store.set_projection_state("dirty", operation_uid=operation_uid, error_code=type(exc).__name__)
                payload["projection"] = "dirty"
                payload["warning"] = {"code": "PROJECTION_DIRTY"}
        else:
            payload["pages"] = {}
            payload["projection"] = "disabled"
        return payload

    def history(self, fact_id: int) -> dict[str, Any]:
        self._authorize_fact(FACT_SEARCH, fact_id)
        self._authorize_fact(HISTORY_READ, fact_id)
        result = self.store.history(fact_id)
        for item in result["chain"]:
            self._authorize(FACT_SEARCH, scope=validate_scope(item["scope"]["kind"], item["scope"]["key"]))
            self._authorize(HISTORY_READ, scope=validate_scope(item["scope"]["kind"], item["scope"]["key"]))
        return result

    def backup_create(self, *, kind: str = "manual") -> dict[str, Any]:
        self._require_local_admin()
        return {"backup": self.backups.create(self.store, kind=kind)}

    def backup_list(self) -> dict[str, Any]:
        self._require_local_admin()
        return {"backups": self.backups.list()}

    def backup_verify(self, path: str) -> dict[str, Any]:
        self._require_local_admin()
        return self.backups.verify(expand_once(path, name="backup"))

    def backup_prune(self) -> dict[str, Any]:
        self._require_local_admin()
        return self.backups.prune()

    def backup_remove(self, paths: list[str]) -> dict[str, Any]:
        self._require_local_admin()
        with self.store.maintenance_lock():
            return {"removed": self.backups.remove_managed(paths)}

    def restore(self, backup_path: str) -> dict[str, Any]:
        self._require_local_admin()
        source = expand_once(backup_path, name="backup")
        verified = self.backups.verify(source)
        if not verified["ok"]:
            raise ValidationError("backup verification failed")
        validate_restore_schema(source)
        db_path = self.store.db_path
        with self.store.maintenance_lock():
            # Hold the writer lock from the recovery snapshot through replacement;
            # otherwise a concurrent committed write could be lost from both.
            safety = self.backups.create(self.store, kind="pre-restore")
            # Restored consent revisions must never revive invalidated context.
            context_reset: dict[str, Any] = {}
            context_store = self.context.store

            def reset_context() -> None:
                context_reset.update(context_store.reset())

            self.store.close()
            try:
                restored = atomic_restore_database(
                    db_path, source, expected_sha256=verified["sha256"], before_replace=reset_context
                )
            finally:
                self.store = MemoryStore(db_path, mode="rw-existing")
        configuration_error = ""
        if self.config is not None:
            self.config.schema_version = self.store.schema_version
            self.config.tier = "unified" if self.store.schema_version >= 2 else "legacy"
            try:
                save_config(self.config)
            except Exception as exc:
                configuration_error = type(exc).__name__
        operation_uid = str(uuid.uuid4())
        payload: dict[str, Any] = {
            "restored": restored,
            "configuration": "pending" if configuration_error else "saved",
            "configuration_error": configuration_error,
            "safety_snapshot": safety,
            "context_reset": context_reset,
        }
        if self.store.schema_version >= 2:
            payload = self._project_after_commit(payload, operation_uid)
        else:
            payload.update(
                {
                    "committed": True,
                    "operation_uid": operation_uid,
                    "projection": "dirty" if self.config and self.config.obsidian_enabled else "disabled",
                }
            )
        payload.update(
            self._log_after_commit(operation_uid, "database.restore", metadata={"schema": self.store.schema_version})
        )
        return payload

    def migrate_plan(self) -> dict[str, Any]:
        return migration_plan(self.store.db_path)

    def migrate_apply(self) -> dict[str, Any]:
        self._require_local_admin()
        db_path = self.store.db_path
        self.store.close()
        try:
            result = migrate_apply(self.data_dir, db_path)
        finally:
            self.store = MemoryStore(db_path, mode="rw-existing")
        self.config = load_config(self.data_dir)
        return result

    def rollback_plan(self) -> dict[str, Any]:
        self._require_local_admin()
        return rollback_plan(self.store.db_path)

    def migrate_rollback(
        self,
        *,
        confirm_loss: bool = False,
        incremental_export: str | None = None,
    ) -> dict[str, Any]:
        self._require_local_admin()
        db_path = self.store.db_path
        self.store.close()
        try:
            result = migrate_rollback(
                self.data_dir,
                db_path,
                confirm_loss=confirm_loss,
                incremental_export=(
                    expand_once(incremental_export, name="incremental export") if incremental_export else None
                ),
            )
        finally:
            self.store = MemoryStore(db_path, mode="rw-existing")
        self.config = load_config(self.data_dir)
        return result

    def purge_plan(self, fact_id: int) -> dict[str, Any]:
        self._require_local_admin()
        fact = self.store.get_fact(fact_id)
        if fact is None:
            raise NotFoundError(f"fact_id {fact_id} not found")
        backups = self.backups.backups_containing_fact(
            fact_uid=fact.fact_uid,
            content=fact.content,
            normalized_hash=normalized_content_hash(fact.content),
        )
        source_rows = self.store.connection.execute(
            "SELECT COUNT(*) FROM fact_sources WHERE fact_id = ?", (fact_id,)
        ).fetchone()[0]
        return {
            "fact": fact.to_dict(),
            "confirmation": fact.fact_uid,
            "source_links": int(source_rows),
            "managed_backups_to_remove": backups,
            "projection": self.store.projection_state(),
            "working_context": {
                "present": self.context.store.path.exists(),
                "action": "remove all temporary documents and enabled-source settings before purge",
            },
            "uncontrolled_copies": [
                "Time Machine and system snapshots",
                "third-party Obsidian synchronization",
                "copies made outside With. managed backups",
            ],
        }

    def purge(self, fact_id: int, *, confirmation: str) -> dict[str, Any]:
        self._require_local_admin()
        with self.store.maintenance_lock():
            plan = self.purge_plan(fact_id)
            if confirmation != plan["confirmation"]:
                raise ConfirmationRequiredError("type the exact fact UID to confirm purge")
            projection_probe: Path | None = None
            if self.config is not None and self.config.obsidian_enabled:
                vault = expand_once(self.config.vault_dir, name="vault dir")
                projection_probe = vault / f".with-purge-probe-{uuid.uuid4().hex}"
                atomic_write_text(projection_probe, "purge preflight\n", mode=0o600)
                projection_probe.unlink()
            mandatory = self.backups.create(self.store, kind="pre-purge")
            context_reset = self.context.store.reset()
            affected = list(plan["managed_backups_to_remove"])
            affected.append(str(mandatory["path"]))
            fact_uid = str(plan["confirmation"])
            result = self.store.purge_fact_row(fact_uid, actor=self.principal_key)
            affected = sorted(set(affected))
            cleanup_error = ""
            try:
                removed = self.backups.remove_managed(affected)
            except OSError as exc:
                cleanup_error = type(exc).__name__
                removed = [path for path in affected if not Path(path).exists()]
            remaining = [path for path in affected if Path(path).exists()]
            operation_uid = str(uuid.uuid4())
            projected = self._project_after_commit(result, operation_uid)
            if projected.get("projection") == "dirty" and self.config is not None:
                try:
                    removed_pages = remove_managed_projection(self.config.vault_dir)
                    projected["purge_projection_cleared"] = True
                    projected["removed_projection_pages"] = removed_pages
                except OSError as exc:
                    projected["purge_projection_cleared"] = False
                    projected["purge_projection_error"] = type(exc).__name__
            clean = None
            try:
                clean = self.backups.create(self.store, kind="clean-post-purge")
            except Exception as exc:
                cleanup_error = type(exc).__name__
            projected.update(
                {
                    "removed_managed_backups": removed,
                    "clean_backup": clean,
                    "purge_cleanup": "pending" if remaining or clean is None else "complete",
                    "remaining_managed_backups": remaining,
                    "cleanup_error": cleanup_error,
                    "context_reset": context_reset,
                    "warning": {
                        "code": "UNCONTROLLED_COPIES_REMAIN",
                        "message": (
                            "Time Machine, system snapshots, and third-party Obsidian sync are outside With. control."
                        ),
                    },
                    "preflight_projection": "passed" if projection_probe is not None else "disabled",
                }
            )
            if projected["purge_cleanup"] == "pending":
                projected["warning"] = {
                    "code": "PURGE_CLEANUP_PENDING",
                    "message": "Fact removal committed; remaining backups or the clean snapshot need recovery.",
                }
                projected["recovery"] = {
                    "fact_already_removed": True,
                    "remove_with": "backup remove PATH...",
                    "paths": remaining,
                    "then": "backup create",
                }
            return projected

    def export(self, *, format: str = "jsonl", include_deprecated: bool = True) -> dict[str, Any]:
        self._require_local_admin()
        sql = (
            "SELECT * FROM facts ORDER BY fact_id"
            if include_deprecated
            else "SELECT * FROM facts WHERE status = 'active' ORDER BY fact_id"
        )
        rows = self.store.connection.execute(sql).fetchall()
        facts = [self.store._row_to_fact(row).to_dict() for row in rows]
        if format == "jsonl":
            text = "\n".join(json.dumps(item, ensure_ascii=False) for item in facts)
        elif format == "markdown":
            text = "\n\n".join(f"## #{item['fact_id']} ({item['status']})\n\n{item['content']}" for item in facts)
        else:
            raise ValidationError("format must be jsonl or markdown")
        return {"format": format, "count": len(facts), "content": text + ("\n" if text else "")}

    def doctor(self) -> dict[str, Any]:
        self._authorize(STATUS_READ)
        return doctor_report(self.data_dir, self.store, self.config)

    def support_bundle(self, output: str) -> dict[str, Any]:
        self._require_local_admin()
        return create_support_bundle(
            self.data_dir,
            self.store,
            self.config,
            expand_once(output, name="support bundle"),
        )

    def verify(self, queries: list[str]) -> dict[str, Any]:
        reports: list[dict[str, Any]] = []
        ok = True
        for query in queries:
            current = self.search(query, include_deprecated=False, include_files=False, limit=100)
            leaked = [item for item in current["facts"] if item["status"] == "deprecated"]
            if leaked:
                ok = False
            with_deprecated = []
            try:
                historical = self.search(query, include_deprecated=True, include_files=False, limit=100)
                with_deprecated = historical["facts"]
            except PermissionRequiredError:
                historical = {"facts": []}
            reports.append(
                {
                    "query": query,
                    "active_hits": len(current["facts"]),
                    "with_deprecated_hits": len(with_deprecated),
                    "deprecated_leaked_into_default": leaked,
                    "layer": current["layer"],
                }
            )
        return {"ok": ok, "reports": reports, "counts": self.store.counts()}


def known_preset_keys() -> list[str]:
    return [item.key for item in CATALOG]
