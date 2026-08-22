from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

root = Path(SPEC).resolve().parent.parent
datas = [
    (str(root / "templates"), "templates"),
    (str(root / "static"), "static"),
    (str(root / "prices" / "templates"), "prices/templates"),
    (str(root / "forecasting" / "templates"), "forecasting/templates"),
]
datas += collect_data_files("django")

hiddenimports = collect_submodules("whitenoise")
for package in ("gabay", "prices", "forecasting", "portal"):
    # Django discovers migrations, management commands, template tags, and app
    # configuration dynamically.  Explicit collection keeps those paths
    # available inside the portable one-folder runtime.
    hiddenimports += collect_submodules(
        package,
        filter=lambda name: ".tests" not in name and not name.endswith(".tests"),
    )

analysis = Analysis(
    [str(root / "packaging" / "entrypoint.py")],
    pathex=[str(root)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "IPython", "matplotlib", "notebook", "pytest", "pygments"],
    noarchive=False,
)
pyz = PYZ(analysis.pure)
exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="GABAY",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
)
coll = COLLECT(
    exe,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    name="GABAY",
)
