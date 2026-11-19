"""Entry point frozen by PyInstaller into the ``vera-in-viewer-server`` executable.

Electron starts it with ``--server --host 127.0.0.1 --port 0`` so the server binds a free
port, then waits for the ``electron-server-port=<port>`` line printed once the server
listens, and loads ``http://127.0.0.1:<port>/``.
"""

from __future__ import annotations

import multiprocessing
import sys
from pathlib import Path

# Must match READY_LINE in electron/main.js
READY_PREFIX = "electron-server-port="


def _use_bundled_wasm() -> None:
    """Serve the VTK.wasm archive shipped in the bundle instead of downloading VTK.wasm.

    trame-vtklocal downloads and extracts VTK.wasm (32 and 64 bits) next to its package
    on first use, or into ``~/.vtk-wasm`` when the package directory is read only. The
    bundle only ships ``wasm32/<vtk version>/vtk-wasm.tgz`` (see the spec file), which the
    client loads as a whole, so register it without checking for the extracted files:
    the app works offline and never writes inside its own bundle.
    """
    import trame_vtklocal.module as vtklocal_module  # noqa: PLC0415
    from trame_vtklocal.module.wasm import get_wasm_info  # noqa: PLC0415

    serve_path = vtklocal_module.serve_path

    def register_bundled_wasm(_serve_path, wasm_bits="wasm32", **kwargs):
        version, _ = get_wasm_info(wasm_bits)
        base_url = f"{vtklocal_module.WASM_BASE_URL}/{wasm_bits}/{version}"
        config = {"url": base_url, "wasmBaseName": kwargs.get("wasm_base_name", "vtk")}
        if (Path(serve_path) / wasm_bits / version / "vtk-wasm.tgz").exists():
            config["tgz_url"] = f"{base_url}/vtk-wasm.tgz"
        return {"state": {f"__trame_vtklocal_{wasm_bits}": config}}

    vtklocal_module.wasm_serve_path = serve_path
    vtklocal_module.serve[vtklocal_module.WASM_BASE_URL] = serve_path
    vtklocal_module.register_wasm = register_bundled_wasm  # used by setup()


def main() -> None:
    if getattr(sys, "frozen", False):
        _use_bundled_wasm()

    # Imported after the patch: the app enables the trame_vtklocal module at startup
    from vera_in_viewer.app import VeraInViewer  # noqa: PLC0415

    app = VeraInViewer()

    @app.server.controller.add("on_server_ready")
    def notify_electron(**_):
        # Called once listening: server.port is the one bound (--port 0 picks a free one)
        print(f"{READY_PREFIX}{app.server.port}", flush=True)

    app.server.start()


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
