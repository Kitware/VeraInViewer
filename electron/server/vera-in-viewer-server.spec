# PyInstaller spec for the trame server bundled in the Electron app.
# Built by build.py (run it rather than calling pyinstaller directly: it prefetches VTK.wasm first).
# ruff: noqa: F821 (Analysis, PYZ, EXE, COLLECT and SPECPATH are provided by PyInstaller)

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

NAME = "vera-in-viewer-server"

datas = []
hiddenimports = []

# trame resolves its widgets / modules / ui through namespace packages and serves the
# web assets (JS, CSS, VTK.wasm) straight from the package directories.
for package in (
    "trame",
    "trame_client",
    "trame_common",
    "trame_server",
    "trame_vuetify",
    "trame_vtklocal",
    "wslink",
    "vera_in_viewer",
):
    datas += collect_data_files(package)
    hiddenimports += collect_submodules(package)

# trame_vtklocal deserializes the scene with vtkObjectManager: every VTK class the
# scene uses must be importable. Those used by the app are imported statically.
hiddenimports += [
    "vtkmodules.vtkSerializationManager",
    "vtkmodules.vtkRenderingOpenGL2",
    "vtkmodules.vtkInteractionStyle",
    "vtkmodules.util.numpy_support",
]

a = Analysis(
    [str(Path(SPECPATH) / "entry.py")],
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "matplotlib", "IPython", "pytest"],
    noarchive=False,
)


def keep_data(dest: str) -> bool:
    """Of VTK.wasm, only keep ``wasm32/<version>/vtk-wasm.tgz``.

    The client uses the wasm32 build (the app never asks for wasm64) and, when the
    archive is advertised (see entry.py), loads it instead of the extracted files.
    Filtered here as pyinstaller-hooks-contrib also collects the trame packages data.
    """
    parts = Path(dest).parts
    if parts[:3] != ("trame_vtklocal", "module", "serve") or parts[3] not in ("wasm32", "wasm64"):
        return True
    return parts[3] == "wasm32" and parts[-1] == "vtk-wasm.tgz"


a.datas = [entry for entry in a.datas if keep_data(entry[0])]
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=NAME,
    console=True,  # Electron reads stdout / stderr, no window is shown
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name=NAME, upx=False)
