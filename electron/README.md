# VeraInViewer desktop app

Packages VeraInViewer as a standalone desktop application:

- **PyInstaller** freezes the trame server (Python, VTK, trame, VTK.wasm and the built-in samples)
  into a relocatable directory, `server/dist/vera-in-viewer-server`. No Python install is needed
  on the target machine and the app works offline.
- **Electron** provides the native window: it starts that server, shows a splash screen until
  the server is ready, loads the UI and stops the server when the app quits.

The server is started with `--host 127.0.0.1 --port 0`, so it binds a free port itself. Once it
listens, trame calls `on_server_ready`, where `server/entry.py` prints
`electron-server-port=<port>` on stdout: Electron waits for that line and loads `http://127.0.0.1:<port>/`.

```
electron/
├── main.js                         Electron main process (server lifecycle + window)
├── loading.html                    splash screen shown while the server starts
├── package.json                    npm scripts + electron-builder configuration
└── server/
    ├── entry.py                    frozen entry point (serves the bundled VTK.wasm)
    ├── vera-in-viewer-server.spec  PyInstaller spec
    └── build.py                    prefetch VTK.wasm, then run PyInstaller
```

## Requirements

- [uv](https://docs.astral.sh/uv/) (the server is frozen from the parent project's environment)
- Node.js 20+ and npm

Builds are not cross platform: build on macOS for macOS, on Windows for Windows and on Linux for
Linux (e.g. a CI matrix).

## Build

```bash
cd electron
npm install
npm run build          # = npm run build:server && npm run dist
```

- `npm run build:server` downloads the VTK.wasm archive (`wasm32/<vtk version>/vtk-wasm.tgz`)
  matching the installed VTK into the trame-vtklocal package, then runs PyInstaller. That archive
  is the only VTK.wasm file bundled: the client loads it as a whole, and the app never uses the
  wasm64 build or the extracted files. The frozen server can be tried on its own:
  `server/dist/vera-in-viewer-server/vera-in-viewer-server --data ~/cases`.
- `npm run dist` builds the installers into `dist/` (macOS: `.dmg` + `.zip`, Windows: NSIS
  installer + `.zip`, Linux: `.AppImage` + `.tar.gz`). `npm run pack` only builds the unpacked
  app, which is faster to test.

## Run during development

```bash
npm start   # Electron + frozen server from server/dist (falls back to `uv run` if not built)
npm run dev # Electron + `uv run --project .. python server/entry.py`: no refreeze needed
```

Arguments after `--` are forwarded to the server, e.g. `npm start -- --data ~/cases`. The packaged
app accepts them too: `VeraInViewer.app/Contents/MacOS/VeraInViewer --data ~/cases/core.xml`.

Server output is echoed on the Electron stdout; the last lines are shown in the error dialog
if the server fails to start or stops.

## Notes

- **VTK version**: the bundled VTK.wasm must match the VTK the server was frozen with. The build
  uses whatever VTK is in the parent project environment (`uv.lock`); to bundle a VTK nightly (for
  the screenshot button), install it in that environment first and freeze with
  `uv run --no-sync --project .. --with pyinstaller python server/build.py`.
- **Icons**: drop `icon.icns` (macOS), `icon.ico` (Windows) and `icon.png` (512x512, Linux) in
  `electron/resources/` to replace the default Electron icon.
- **Code signing**: electron-builder signs the app (including the frozen server) when signing
  identities are configured (`CSC_LINK`, `APPLE_ID`, ... see the electron-builder docs). Unsigned
  macOS builds must be opened with right click > Open the first time.
