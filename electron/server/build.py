"""Freeze the trame server into ``electron/server/dist/vera-in-viewer-server``.

Run from the ``electron`` directory with ``npm run build:server``, i.e.::

    uv run --project .. --with pyinstaller python server/build.py

1. download the VTK.wasm archive (wasm32) matching the installed VTK into the
   trame-vtklocal package, so it gets bundled and the app works offline
2. run PyInstaller on ``vera-in-viewer-server.spec`` (one directory build: no unpacking
   at every start like ``--onefile``)
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import PyInstaller.__main__
import trame_vtklocal.module as vtklocal_module
from trame_vtklocal.module.wasm import download_file, get_wasm_info

HERE = Path(__file__).parent.resolve()
SPEC = HERE / "vera-in-viewer-server.spec"


def prefetch_wasm() -> None:
    """Download ``wasm32/<vtk version>/vtk-wasm.tgz``, the only VTK.wasm file bundled."""
    version, url = get_wasm_info("wasm32")
    archive = Path(vtklocal_module.serve_path) / "wasm32" / version / "vtk-wasm.tgz"
    if not archive.exists():
        print(f"Downloading {url}")
        archive.parent.mkdir(parents=True, exist_ok=True)
        asyncio.run(download_file(url, str(archive)))
    print(f"VTK.wasm wasm32 {version}: {archive}")


def main() -> None:
    prefetch_wasm()
    PyInstaller.__main__.run(
        [
            str(SPEC),
            "--noconfirm",
            "--clean",
            "--distpath",
            str(HERE / "dist"),
            "--workpath",
            str(HERE / "build"),
        ]
    )
    print(f"Server bundle: {HERE / 'dist' / 'vera-in-viewer-server'}")


if __name__ == "__main__":
    main()
