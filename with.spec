# PyInstaller onedir specification. Run on each target OS; never cross-compile.

from pathlib import Path

project_root = Path(SPECPATH).resolve()
datas = [
    (str(project_root / "src" / "eric_memory" / "resources"), "eric_memory/resources"),
    (str(project_root / "skills"), "distribution/skills"),
    (str(project_root / "quests"), "distribution/quests"),
    (str(project_root / "adapters"), "distribution/adapters"),
    (str(project_root / "vault-template"), "distribution/vault-template"),
    (str(project_root / "LICENSE"), "distribution"),
]
binaries = []
hiddenimports = [
    "cryptography.hazmat.primitives.asymmetric.ed25519",
    "cryptography.hazmat.primitives.serialization",
]
excluded_build_tools = [
    "ast_serialize",
    "coverage",
    "cyclonedx_py",
    "librt",
    "mypy",
    "mypy_extensions",
    "pip",
    "pip_audit",
    "ruff",
    "setuptools",
    "tomli",
    "wheel",
]
# PyInstaller's collect_all("mcp") imports the optional mcp.cli package, whose
# Typer dependency is deliberately not part of the runtime SDK installation.
# Normal analysis discovers the SDK server/types modules. The only delayed
# cryptography imports are named explicitly above so the normal native hook can
# collect their binary dependency without copying the entire source package.

analysis = Analysis(
    [str(project_root / "src" / "eric_memory" / "__main__.py")],
    pathex=[str(project_root / "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Release jobs install dev tools in the build environment. Pydantic's hook
    # otherwise discovers its optional mypy plugin and accidentally ships the
    # entire compiler/toolchain. None of these packages are runtime surfaces.
    excludes=excluded_build_tools,
    noarchive=False,
    optimize=1,
)
pyz = PYZ(analysis.pure)
exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="eric-memory",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
collection = COLLECT(
    exe,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="with",
)
