"""Entry point frozen by PyInstaller into the ``vera-in-viewer-server`` executable.

Electron starts it through @kitware/trame-rca-electron (``launchTrame``) with::

    --electron --host 127.0.0.1 --timeout 0 [user args] --server --port 0 --trame-electron-port <port>

``--electron`` makes the app render the 3D view on the server with trame-rca, into a GPU
texture shared with Electron, instead of using VTK.wasm in the page. Once the server listens,
trame-rca connects to the ``--trame-electron-port`` texture channel (authenticated with the
``TRAME_ELECTRON_TOKEN`` environment variable) and sends the page URL for Electron to load.
"""

from __future__ import annotations

import multiprocessing

from vera_in_viewer.app import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
