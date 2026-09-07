"""Explicit-only, signed GitHub Release updater for PyInstaller onedir builds."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import platform
import re
import shutil
import stat
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from . import __version__
from .backup import BackupManager
from .errors import (
    ConfirmationRequiredError,
    UpdateUnavailableError,
    UpdateVerificationError,
    ValidationError,
)
from .paths import atomic_write_text, db_path_for, require_absolute, resolve_data_dir, resource_root
from .store import MemoryStore

GITHUB_LATEST_RELEASE = "https://api.github.com/repos/prest4u/with-memory/releases/latest"
MAX_METADATA_BYTES = 2 * 1024 * 1024
MAX_ARCHIVE_BYTES = 500 * 1024 * 1024
MAX_EXPANDED_BYTES = 1024 * 1024 * 1024
PUBLIC_KEY_NAME = "release-public-key.pem"
MANIFEST_FORMAT = "with-release-v1"
VERSION_RE = re.compile(r"^(?:v)?(\d+)\.(\d+)\.(\d+)(?:[-+]([0-9A-Za-z.-]+))?$")
UPDATE_HOSTS = {"api.github.com", "github.com"}


def _approved_update_url(url: str) -> bool:
    parsed = urllib.parse.urlparse(url)
    host = (parsed.hostname or "").lower()
    return parsed.scheme == "https" and (host in UPDATE_HOSTS or host.endswith(".githubusercontent.com"))


class _GithubRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Reject a redirect before urllib makes a request to its target."""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> urllib.request.Request | None:
        if not _approved_update_url(newurl):
            raise UpdateVerificationError("update redirect left the approved GitHub HTTPS hosts")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _version(value: str) -> tuple[int, int, int, tuple[str, ...]]:
    match = VERSION_RE.fullmatch(value.strip())
    if not match:
        raise ValidationError("release version is not valid semantic versioning")
    suffix = tuple((match.group(4) or "").split(".")) if match.group(4) else ()
    return int(match.group(1)), int(match.group(2)), int(match.group(3)), suffix


def _is_newer(candidate: str, current: str) -> bool:
    candidate_value = _version(candidate)
    current_value = _version(current)
    if candidate_value[:3] != current_value[:3]:
        return candidate_value[:3] > current_value[:3]
    if not candidate_value[3] and current_value[3]:
        return True
    if candidate_value[3] and not current_value[3]:
        return False
    return candidate_value[3] > current_value[3]


def platform_tag() -> str:
    system = platform.system().lower()
    machine = platform.machine().lower()
    if machine in {"amd64", "x64"}:
        machine = "x86_64"
    if machine in {"aarch64"}:
        machine = "arm64"
    supported = {
        ("darwin", "arm64"): "macos-arm64",
        ("darwin", "x86_64"): "macos-x86_64",
        ("windows", "x86_64"): "windows-x86_64",
        ("linux", "x86_64"): "linux-x86_64",
    }
    try:
        return supported[(system, machine)]
    except KeyError as exc:
        raise UpdateUnavailableError(f"no With. release artifact supports {system}/{machine}") from exc


def default_install_root() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
        return (base / "With").resolve()
    return (Path.home() / ".local" / "share" / "with").resolve()


def _request(url: str, *, max_bytes: int) -> bytes:
    if not _approved_update_url(url):
        raise UpdateVerificationError("update URLs must use an approved GitHub HTTPS host")
    request = urllib.request.Request(  # noqa: S310 - URL scheme and host are checked above
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"With/{__version__}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        # The URL and every redirect target are checked before any response body is trusted.
        opener = urllib.request.build_opener(_GithubRedirectHandler())
        with opener.open(request, timeout=30) as response:  # noqa: S310
            if not _approved_update_url(response.geturl()):
                raise UpdateVerificationError("update redirect left the approved GitHub HTTPS hosts")
            length = response.headers.get("Content-Length")
            if length and int(length) > max_bytes:
                raise UpdateVerificationError("update response exceeds the allowed size")
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = response.read(min(1024 * 1024, max_bytes + 1 - total))
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise UpdateVerificationError("update response exceeds the allowed size")
                chunks.append(chunk)
            return b"".join(chunks)
    except urllib.error.URLError as exc:
        raise UpdateUnavailableError("GitHub Release metadata could not be reached") from exc


def _release_metadata() -> dict[str, Any]:
    try:
        value = json.loads(_request(GITHUB_LATEST_RELEASE, max_bytes=MAX_METADATA_BYTES))
    except json.JSONDecodeError as exc:
        raise UpdateVerificationError("GitHub returned invalid release metadata") from exc
    if not isinstance(value, dict) or value.get("draft") or value.get("prerelease"):
        raise UpdateVerificationError("latest release metadata is not a GA release")
    return value


def _asset_map(release: dict[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in release.get("assets", []):
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        url = item.get("browser_download_url")
        if isinstance(name, str) and isinstance(url, str):
            result[name] = url
    return result


def _release_summary(release: dict[str, Any]) -> dict[str, Any]:
    tag = str(release.get("tag_name", ""))
    version = tag[1:] if tag.startswith("v") else tag
    _version(version)
    expected_archive = f"with-{version}-{platform_tag()}.zip"
    assets = _asset_map(release)
    expected = {
        "archive": expected_archive,
        "manifest": f"with-{version}-manifest.json",
        "signature": f"with-{version}-manifest.json.sig",
    }
    return {
        "version": version,
        "tag": tag,
        "published_at": str(release.get("published_at", "")),
        "html_url": str(release.get("html_url", "")),
        "expected_assets": expected,
        "assets_present": {key: value in assets for key, value in expected.items()},
    }


def check_update() -> dict[str, Any]:
    release = _release_metadata()
    summary = _release_summary(release)
    return {
        "current_version": __version__,
        "update_available": _is_newer(summary["version"], __version__),
        "release": summary,
        "platform": platform_tag(),
        "trust_root_configured": (resource_root() / PUBLIC_KEY_NAME).is_file(),
        "network_policy": "network accessed only because update check was explicitly invoked",
    }


def _atomic_write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temp = Path(temporary)
    try:
        if os.name != "nt":
            os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    except Exception:
        temp.unlink(missing_ok=True)
        raise


def _verify_manifest(
    manifest_bytes: bytes,
    signature_bytes: bytes,
    *,
    public_key_path: Path,
    expected_version: str,
) -> dict[str, Any]:
    if not public_key_path.is_file():
        raise UpdateVerificationError("release signing trust root is missing; GA update apply is disabled")
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        public_key = serialization.load_pem_public_key(public_key_path.read_bytes())
        if not isinstance(public_key, Ed25519PublicKey):
            raise TypeError("not Ed25519")
        raw_signature = signature_bytes.strip()
        if len(raw_signature) != 64:
            raw_signature = base64.b64decode(raw_signature, validate=True)
        public_key.verify(raw_signature, manifest_bytes)
    except Exception as exc:  # noqa: BLE001 - all failures become one stable verification error
        raise UpdateVerificationError("release manifest signature verification failed") from exc
    try:
        manifest = json.loads(manifest_bytes)
    except json.JSONDecodeError as exc:
        raise UpdateVerificationError("signed release manifest is not valid JSON") from exc
    if not isinstance(manifest, dict):
        raise UpdateVerificationError("signed release manifest must be an object")
    allowed = {"format", "version", "schema_version", "assets", "source_commit"}
    if set(manifest) - allowed:
        raise UpdateVerificationError("signed release manifest has unexpected fields")
    if manifest.get("format") != MANIFEST_FORMAT or manifest.get("version") != expected_version:
        raise UpdateVerificationError("signed release manifest identity does not match the release")
    if not isinstance(manifest.get("schema_version"), int) or not isinstance(manifest.get("assets"), dict):
        raise UpdateVerificationError("signed release manifest has invalid fields")
    for name, descriptor in manifest["assets"].items():
        if not isinstance(name, str) or not isinstance(descriptor, dict):
            raise UpdateVerificationError("signed asset descriptor is invalid")
        if set(descriptor) != {"sha256", "size"}:
            raise UpdateVerificationError("signed asset descriptor has unexpected fields")
        digest = descriptor.get("sha256")
        size = descriptor.get("size")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise UpdateVerificationError("signed asset hash is invalid")
        if not isinstance(size, int) or not 0 < size <= MAX_ARCHIVE_BYTES:
            raise UpdateVerificationError("signed asset size is invalid")
    return manifest


def _database_update_plan(data_dir: Path, target_schema: int) -> dict[str, Any]:
    database = db_path_for(data_dir)
    if not database.is_file():
        return {
            "present": False,
            "current_schema": None,
            "target_schema": target_schema,
            "migration_required": False,
            "planned_backup_dir": str((data_dir / "backups").resolve()),
        }
    store = MemoryStore(database, mode="ro")
    try:
        current = store.schema_version
    finally:
        store.close()
    return {
        "present": True,
        "current_schema": current,
        "target_schema": target_schema,
        "migration_required": current < target_schema,
        "planned_backup_dir": str((data_dir / "backups").resolve()),
    }


def prepare_update(
    data_dir: str | Path | None = None,
    *,
    install_root: str | Path | None = None,
    public_key_path: str | Path | None = None,
) -> dict[str, Any]:
    release = _release_metadata()
    summary = _release_summary(release)
    if not _is_newer(summary["version"], __version__):
        raise UpdateUnavailableError("no newer GA release is available")
    if not all(summary["assets_present"].values()):
        raise UpdateVerificationError("release is missing its archive, manifest, or signature")
    assets = _asset_map(release)
    root = require_absolute(install_root or default_install_root(), name="install root")
    staging = root / "staging" / summary["version"]
    manifest_name = summary["expected_assets"]["manifest"]
    signature_name = summary["expected_assets"]["signature"]
    manifest_bytes = _request(assets[manifest_name], max_bytes=MAX_METADATA_BYTES)
    signature_bytes = _request(assets[signature_name], max_bytes=16 * 1024)
    _atomic_write_bytes(staging / manifest_name, manifest_bytes)
    _atomic_write_bytes(staging / signature_name, signature_bytes)
    trust_root = require_absolute(public_key_path or (resource_root() / PUBLIC_KEY_NAME), name="release public key")
    manifest = _verify_manifest(
        manifest_bytes,
        signature_bytes,
        public_key_path=trust_root,
        expected_version=summary["version"],
    )
    archive_name = summary["expected_assets"]["archive"]
    if archive_name not in manifest["assets"]:
        raise UpdateVerificationError("signed manifest does not contain this platform artifact")
    data = resolve_data_dir(data_dir)
    return {
        "format": "with-update-plan-v1",
        "current_version": __version__,
        "release": summary,
        "platform": platform_tag(),
        "install_root": str(root),
        "staging_dir": str(staging),
        "archive_url": assets[archive_name],
        "archive": {"name": archive_name, **manifest["assets"][archive_name]},
        "manifest_path": str(staging / manifest_name),
        "signature_path": str(staging / signature_name),
        "public_key_path": str(trust_root),
        "source_commit": str(manifest.get("source_commit", "")),
        "database": _database_update_plan(data, int(manifest["schema_version"])),
        "confirmation": f"APPLY {summary['version']}",
    }


def _safe_extract(archive: Path, destination: Path) -> Path:
    total = 0
    with zipfile.ZipFile(archive) as bundle:
        members = bundle.infolist()
        for member in members:
            posix_relative = PurePosixPath(member.filename)
            windows_relative = PureWindowsPath(member.filename)
            mode = member.external_attr >> 16
            if (
                not member.filename
                or posix_relative.is_absolute()
                or windows_relative.is_absolute()
                or ".." in posix_relative.parts
                or ".." in windows_relative.parts
                or stat.S_ISLNK(mode)
            ):
                raise UpdateVerificationError("release archive contains an unsafe path")
            total += int(member.file_size)
            if total > MAX_EXPANDED_BYTES:
                raise UpdateVerificationError("expanded release exceeds the allowed size")
        bundle.extractall(destination)
    executable_name = "eric-memory.exe" if os.name == "nt" else "eric-memory"
    executable = destination / "with" / executable_name
    if not executable.is_file() or executable.is_symlink():
        raise UpdateVerificationError("release archive does not contain the canonical CLI executable")
    return executable.parent


def _install_version(archive: Path, *, root: Path, version: str) -> dict[str, Any]:
    versions = root / "versions"
    versions.mkdir(parents=True, exist_ok=True, mode=0o700)
    target = versions / version
    if target.exists():
        raise UpdateVerificationError("target version directory already exists")
    temporary = Path(tempfile.mkdtemp(prefix=f".{version}-", dir=versions))
    try:
        payload_root = _safe_extract(archive, temporary)
        if payload_root == temporary:
            os.replace(temporary, target)
        else:
            os.replace(payload_root, target)
            shutil.rmtree(temporary, ignore_errors=True)
        if os.name != "nt":
            for executable in target.rglob("eric-memory"):
                executable.chmod(executable.stat().st_mode | 0o500)
        bin_dir = root / "bin"
        bin_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        executable = target / ("eric-memory.exe" if os.name == "nt" else "eric-memory")
        if not executable.is_file():
            matches = list(target.rglob(executable.name))
            if len(matches) != 1:
                raise UpdateVerificationError("installed release executable is ambiguous")
            executable = matches[0]
        if os.name == "nt":
            launcher = bin_dir / "eric-memory.cmd"
            atomic_write_text(launcher, f'@"{executable}" %*\r\n', mode=0o600)
        else:
            launcher = bin_dir / "eric-memory"
            link_temp = bin_dir / f".eric-memory-{os.getpid()}"
            link_temp.unlink(missing_ok=True)
            link_temp.symlink_to(executable)
            os.replace(link_temp, launcher)
        atomic_write_text(
            root / "current.json",
            json.dumps(
                {"version": version, "executable": str(executable), "updated_by": __version__},
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            mode=0o600,
        )
        return {
            "version_dir": str(target),
            "launcher": str(launcher),
            "executable": str(executable),
        }
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def apply_update(
    plan: dict[str, Any],
    *,
    confirmation: str,
    data_dir: str | Path | None = None,
) -> dict[str, Any]:
    if plan.get("format") != "with-update-plan-v1" or confirmation != plan.get("confirmation"):
        raise ConfirmationRequiredError("exact signed update version confirmation is required")
    version = str(plan["release"]["version"])
    manifest_path = require_absolute(plan["manifest_path"], name="manifest")
    signature_path = require_absolute(plan["signature_path"], name="signature")
    public_key = require_absolute(plan["public_key_path"], name="release public key")
    manifest = _verify_manifest(
        manifest_path.read_bytes(),
        signature_path.read_bytes(),
        public_key_path=public_key,
        expected_version=version,
    )
    descriptor = manifest["assets"].get(plan["archive"]["name"])
    if descriptor != {"sha256": plan["archive"]["sha256"], "size": plan["archive"]["size"]}:
        raise UpdateVerificationError("update plan no longer matches its signed manifest")
    staging = require_absolute(plan["staging_dir"], name="staging directory")
    archive = staging / str(plan["archive"]["name"])
    payload = _request(str(plan["archive_url"]), max_bytes=MAX_ARCHIVE_BYTES)
    if len(payload) != int(descriptor["size"]):
        raise UpdateVerificationError("release artifact size does not match signed manifest")
    if hashlib.sha256(payload).hexdigest() != descriptor["sha256"]:
        raise UpdateVerificationError("release artifact hash does not match signed manifest")
    _atomic_write_bytes(archive, payload)

    data = resolve_data_dir(data_dir)
    database_backup = None
    database = db_path_for(data)
    if database.is_file():
        store = MemoryStore(database, mode="ro")
        try:
            database_backup = BackupManager(data).create(store, kind="pre-update")
        finally:
            store.close()
    installed = _install_version(
        archive,
        root=require_absolute(plan["install_root"], name="install root"),
        version=version,
    )
    return {
        "applied": True,
        "version": version,
        "signature_verified": True,
        "sha256_verified": True,
        "database_backup": database_backup,
        "database": plan["database"],
        "migration_pending": bool(plan["database"]["migration_required"]),
        "next_command": ("eric-memory migrate --plan" if plan["database"]["migration_required"] else None),
        **installed,
        "path_modified": False,
    }
