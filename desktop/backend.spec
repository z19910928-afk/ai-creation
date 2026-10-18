# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules, copy_metadata


project = Path(SPECPATH).resolve().parent
static_files = (
    "index.html",
    "app.js",
    "editor-sync.js",
    "author-workspace.js",
    "styles.css",
    "knowledge-workspace.js",
    "writing-inbox.js",
    "knowledge-workspace.css",
    "editor-preferences.css",
    "agent-chat.js",
    "agent-chat.css",
    "agent-settings.js",
    "agent-settings.css",
    "model-picker.js",
    "model-picker.css",
)
schema_files = ("docs/schema-v1.sql", "docs/schema-v1.1-migration.sql")
datas = []
for name in static_files:
    source = project / "frontend" / name
    if not source.is_file() or source.is_symlink():
        raise RuntimeError("A required desktop resource is missing or unsafe")
    datas.append((str(source), "frontend"))
for name in schema_files:
    source = project / name
    if not source.is_file() or source.is_symlink():
        raise RuntimeError("A required desktop resource is missing or unsafe")
    datas.append((str(source), str(Path(name).parent)))
datas += copy_metadata("langgraph", recursive=True)
hiddenimports = ["backend.server"]
for package in ("langgraph", "langgraph_sdk", "langchain_core", "pydantic", "pydantic_core"):
    hiddenimports += collect_submodules(package)

a = Analysis(
    [str(project / "desktop" / "service_entry.py")],
    pathex=[str(project)],
    binaries=[],
    datas=datas,
    hiddenimports=sorted(set(hiddenimports)),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "unittest"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="moyu-service",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=True,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="moyu-service",
)
