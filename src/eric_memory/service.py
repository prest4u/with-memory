"""Single facade used by CLI and MCP. Do not open a second write path."""

from __future__ import annotations

from pathlib import Path

from .catalog import CATALOG, catalog_by_key, catalog_dicts
from .files import walk_folder
from .importer import import_holograph
from .obsidian import copy_template, write_vault
from .paths import (
    MemoryConfig,
    default_vault_dir,
    db_path_for,
    expand_once,
    load_config,
    repo_root,
    require_absolute,
    resolve_data_dir,
    save_config,
    write_pointer,
)
from .search import search_facts
from .store import MemoryStore


class MemoryService:
    def __init__(self, data_dir: str | Path | None = None) -> None:
        self.data_dir = resolve_data_dir(data_dir)
        self.config = load_config(self.data_dir)
        db_path = Path(self.config.db_path) if self.config else db_path_for(self.data_dir)
        self.store = MemoryStore(db_path)

    def close(self) -> None:
        self.store.close()

    def _refresh_vault(self) -> dict[str, str] | None:
        if self.config is None:
            return None
        return write_vault(self.store, self.config.vault_dir)

    def init(
        self,
        *,
        data_dir: str | Path | None = None,
        vault_dir: str | Path | None = None,
        tier: str = "simple",
        folders: list[str] | None = None,
        write_repo_pointer: bool = True,
    ) -> dict:
        target = resolve_data_dir(data_dir) if data_dir else self.data_dir
        target.mkdir(parents=True, exist_ok=True)
        vault = (
            expand_once(vault_dir, name="vault dir")
            if vault_dir
            else default_vault_dir(target)
        )
        cfg = MemoryConfig(
            data_dir=str(target),
            vault_dir=str(vault),
            db_path=str(db_path_for(target)),
            tier=tier if tier in {"simple", "full"} else "simple",
        )
        save_config(cfg)
        if write_repo_pointer:
            write_pointer(target)
        if self.data_dir != target or Path(self.store.db_path) != Path(cfg.db_path):
            self.store.close()
            self.data_dir = target
            self.store = MemoryStore(cfg.db_path)
        self.config = cfg
        vault.mkdir(parents=True, exist_ok=True)
        copy_template(repo_root() / "vault-template", vault)
        registered_folders = []
        for folder in folders or []:
            registered_folders.append(self.add_folder(folder))
        write_vault(self.store, vault)
        return {
            "data_dir": cfg.data_dir,
            "db_path": cfg.db_path,
            "vault_dir": cfg.vault_dir,
            "tier": cfg.tier,
            "folders": registered_folders,
        }

    def status(self) -> dict:
        cfg = self.config or load_config(self.data_dir)
        return {
            "data_dir": str(self.data_dir),
            "db_path": str(self.store.db_path),
            "vault_dir": cfg.vault_dir if cfg else str(default_vault_dir(self.data_dir)),
            "tier": cfg.tier if cfg else "simple",
            "counts": self.store.counts(),
            "harnesses": [h.to_dict() for h in self.store.list_harnesses()],
            "folders": self.store.list_folders(),
            "catalog": catalog_dicts(),
        }

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
        actor: str = "cli",
    ) -> dict:
        fact = self.store.add_fact(
            content,
            category=category,
            tags=tags,
            entities=entities,
            as_of=as_of,
            trust=trust,
            actor=actor,
        )
        deprecated = None
        if supersedes is not None and int(supersedes) != int(fact.fact_id):
            deprecated = self.store.deprecate(
                supersedes,
                superseded_by=fact.fact_id,
                reason="superseded on add",
                actor=actor,
            )
        payload = {"fact": fact.to_dict()}
        if deprecated:
            payload["deprecated"] = deprecated.to_dict()
        self._refresh_vault()
        return payload

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
    ) -> dict:
        result = search_facts(
            self.store,
            query,
            limit=limit,
            include_deprecated=include_deprecated,
            scope=scope,
            project=project,
            workspace=workspace,
        )
        payload = {
            "layer": result["layer"],
            "query": result["query"],
            "include_deprecated": result["include_deprecated"],
            "facts": [fact.to_dict() for fact in result["facts"]],
        }
        if include_files:
            payload["files"] = self.store.search_files(query, limit=min(10, limit))
        return payload

    def deprecate(
        self,
        fact_id: int,
        *,
        superseded_by: int | None = None,
        reason: str = "",
        actor: str = "cli",
    ) -> dict:
        fact = self.store.deprecate(
            fact_id,
            superseded_by=superseded_by,
            reason=reason,
            actor=actor,
        )
        self._refresh_vault()
        return {"fact": fact.to_dict()}

    def add_folder(self, path: str, *, label: str = "") -> dict:
        abs_path = self.store.upsert_folder(expand_once(path, name="folder"), label=label)
        return {"path": abs_path, "label": label}

    def index_files(self, folder: str | None = None) -> dict:
        targets: list[str] = []
        if folder:
            targets.append(str(expand_once(folder, name="folder")))
            self.store.upsert_folder(targets[0])
        else:
            targets.extend(item["path"] for item in self.store.list_folders())
        indexed = []
        for target in targets:
            try:
                records = walk_folder(target)
            except FileNotFoundError:
                indexed.append({"folder": target, "files": 0, "error": "missing"})
                continue
            count = self.store.replace_files(target, records)
            indexed.append({"folder": target, "files": count})
        self._refresh_vault()
        return {"indexed": indexed}

    def harness_add(
        self,
        key: str,
        *,
        display_name: str | None = None,
        session_root: str = "",
        mcp_mounted: bool = False,
        harvest_ok: bool = False,
        notes: str = "",
        actor: str = "cli",
    ) -> dict:
        preset = catalog_by_key().get(key.strip().lower())
        name = display_name or (preset.display_name if preset else key)
        extra_notes = notes
        if preset and preset.mainland_note and preset.mainland_note not in extra_notes:
            extra_notes = " ".join(part for part in [notes, preset.mainland_note] if part)
        existing = self.store.get_harness(key.strip().lower())
        if session_root:
            session_root = str(expand_once(session_root, name="session_root"))
        elif existing:
            session_root = existing.session_root
            if not harvest_ok:
                harvest_ok = existing.harvest_ok
            if not mcp_mounted:
                mcp_mounted = existing.mcp_mounted
        if harvest_ok and not session_root:
            raise ValueError("harvest_ok requires an absolute session_root the user approved")
        harness = self.store.upsert_harness(
            key,
            display_name=name,
            session_root=session_root,
            mcp_mounted=mcp_mounted,
            harvest_ok=harvest_ok,
            notes=extra_notes,
            actor=actor,
        )
        self._refresh_vault()
        return {"harness": harness.to_dict()}

    def harness_list(self) -> dict:
        return {
            "harnesses": [h.to_dict() for h in self.store.list_harnesses()],
            "catalog": catalog_dicts(),
        }

    def catalog(self) -> dict:
        return {"catalog": catalog_dicts()}

    def import_holograph(self, source: str, *, actor: str = "cli") -> dict:
        path = expand_once(source, name="holograph db")
        report = import_holograph(self.store, path, actor=actor)
        self._refresh_vault()
        return report

    def sync(self, *, actor: str = "cli") -> dict:
        if self.config is None:
            self.init()
        assert self.config is not None
        file_report = self.index_files()
        harvest = []
        for harness in self.store.harvestable_harnesses():
            try:
                records = walk_folder(harness.session_root)
            except FileNotFoundError:
                harvest.append(
                    {
                        "key": harness.key,
                        "session_root": harness.session_root,
                        "files": 0,
                        "error": "missing",
                    }
                )
                continue
            count = self.store.replace_files(harness.session_root, records)
            harvest.append(
                {
                    "key": harness.key,
                    "session_root": harness.session_root,
                    "files": count,
                }
            )
        pages = write_vault(self.store, self.config.vault_dir)
        return {
            "vault_dir": self.config.vault_dir,
            "pages": pages,
            "files": file_report,
            "harvest": harvest,
            "counts": self.store.counts(),
            "actor": actor,
        }

    def verify(self, queries: list[str]) -> dict:
        reports = []
        ok = True
        for query in queries:
            current = self.search(query, include_deprecated=False, include_files=False, limit=200)
            historical = self.search(query, include_deprecated=True, include_files=False, limit=200)
            leaked = [f for f in current["facts"] if f["status"] == "deprecated"]
            if leaked:
                ok = False
            reports.append(
                {
                    "query": query,
                    "active_hits": len(current["facts"]),
                    "with_deprecated_hits": len(historical["facts"]),
                    "deprecated_leaked_into_default": leaked,
                    "layer": current["layer"],
                }
            )
        return {"ok": ok, "reports": reports, "counts": self.store.counts()}


def known_preset_keys() -> list[str]:
    return [item.key for item in CATALOG]
