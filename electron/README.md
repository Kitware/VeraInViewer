# VeraInViewer desktop app

Packages VeraInViewer as a standalone desktop application:

- **PyInstaller** freezes the trame server (Python, VTK, trame, trame-rca and the built-in samples)
  into a relocatable directory, `server/dist/vera-in-viewer-server`. No Python install is needed
  on the target machine and the app works offline.
- **Electron** provides the native window: it starts that server, shows a splash screen until
  the server is ready, loads the UI and stops the server when the app quits.

The 3D view does not use VTK.wasm: the server runs with `--electron` and renders with VTK on the
GPU into textures shared with Electron (a [trame-rca](https://github.com/Kitware/trame-rca)
`shared-texture` view). [`@kitware/trame-rca-electron`](https://www.npmjs.com/package/@kitware/trame-rca-electron)
hands them to the page without encoding nor copying frames to the CPU (IOSurface on macOS, NT
handles on Windows, dma-buf on Linux). Mouse and keyboard events go to the server interactor.

`main.js` starts the server with trame-rca-electron's `launchTrame`, which appends
`--server --port 0 --trame-electron-port <port>` (the server binds a free port, then connects to the
texture channel on `<port>` with the `TRAME_ELECTRON_TOKEN` secret it gets in its environment) and
loads the page once the server sends its URL on that channel.

```
electron/
├── main.js                         Electron main process (server lifecycle + window)
├── loading.html                    splash screen shown while the server starts
├── package.json                    npm scripts + electron-builder configuration
└── server/
    ├── entry.py                    frozen entry point
    ├── vera-in-viewer-server.spec  PyInstaller spec
    └── build.py                    run PyInstaller
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

- `npm run build:server` runs PyInstaller. trame-vtklocal (and VTK.wasm) is left out of the bundle:
  the frozen server only works with `--electron`, so it must be started by the Electron app.
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

Server output goes to the Electron stdout / stderr. An error dialog shows up if the server fails to
start or stops.

## Notes

- **Platforms** (trame-rca-electron): Linux needs Electron running under Wayland (X11 is not
  supported) and `kernel.yama.ptrace_scope` at 1 or less. macOS shares the textures as global
  IOSurfaces, which does not work in an App Sandbox (so not on the Mac App Store).
- **koffi**: trame-rca-electron's native dependency is unpacked from the asar archive
  (`asarUnpack` in `package.json`).
- **Protocol**: the `trame-rca` Python package and the `@kitware/trame-rca-electron` npm package
  speak a versioned protocol: upgrade them together.
- **Icons**: drop `icon.icns` (macOS), `icon.ico` (Windows) and `icon.png` (512x512, Linux) in
  `electron/resources/` to replace the default Electron icon.
- **Code signing**: electron-builder signs the app (including the frozen server) when signing
  identities are configured (`CSC_LINK`, `APPLE_ID`, ... see the electron-builder docs). Unsigned
  macOS builds must be opened with right click > Open the first time.
