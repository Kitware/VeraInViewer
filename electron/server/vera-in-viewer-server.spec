# PyInstaller spec for the trame server bundled in the Electron app.
# Built by build.py. The server always runs with --electron: VTK renders on the server into a
# texture shared with Electron (trame-rca), so trame-vtklocal and VTK.wasm are left out.
# ruff: noqa: F821 (Analysis, PYZ, EXE, COLLECT and SPECPATH are provided by PyInstaller)

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

NAME = "vera-in-viewer-server"

datas = []
hiddenimports = []

# trame resolves its widgets / modules / ui through namespace packages and serves the
# web assets (JS, CSS) straight from the package directories.
for package in (
    "trame",
    "trame_client",
    "trame_common",
    "trame_server",
    "trame_vuetify",
    "trame_rca",
    "wslink",
    "vera_in_viewer",
):
    datas += collect_data_files(package)
    hiddenimports += collect_submodules(package)

# VTK object factories and the trame-rca interaction adapter
hiddenimports += [
    "vtkmodules.vtkRenderingOpenGL2",
    "vtkmodules.vtkInteractionStyle",
    "vtkmodules.vtkWebCore",
    "vtkmodules.util.numpy_support",
]
# Only used without --electron (VTK.wasm in the browser)
excludes = ["trame_vtklocal", "trame.widgets.vtklocal", "trame.modules.vtklocal"]
hiddenimports = [name for name in hiddenimports if name not in excludes]

a = Analysis(
    [str(Path(SPECPATH) / "entry.py")],
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "matplotlib", "IPython", "pytest", *excludes],
    noarchive=False,
)

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
