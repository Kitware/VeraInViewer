"""Freeze the trame server into ``electron/server/dist/vera-in-viewer-server``.

Run from the ``electron`` directory with ``npm run build:server``, i.e.::

    uv run --project .. --with pyinstaller python server/build.py

It runs PyInstaller on ``vera-in-viewer-server.spec`` (one directory build: no unpacking at
every start like ``--onefile``). The server renders with VTK on the GPU (``--electron``), so
no VTK.wasm is downloaded nor bundled.
"""

from __future__ import annotations

from pathlib import Path

import PyInstaller.__main__

HERE = Path(__file__).parent.resolve()
SPEC = HERE / "vera-in-viewer-server.spec"


def main() -> None:
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
