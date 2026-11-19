// Electron shell for VeraInViewer: starts the frozen trame server with `--electron` and shows
// it in a native window. The server renders the 3D view with VTK into a GPU texture shared
// with this app (trame-rca shared-texture view, see @kitware/trame-rca-electron) instead of
// rendering it with VTK.wasm in the page. The server lives as long as the window.

const { app, BrowserWindow, dialog, shell } = require("electron");
const fs = require("node:fs");
const path = require("node:path");

const SERVER_NAME = "vera-in-viewer-server";
const HOST = "127.0.0.1";
const STARTUP_TIMEOUT_MS = 120_000; // first start of a fresh bundle can be slow (AV scans, cold cache)

// @kitware/trame-rca-electron is an ES module: loaded with import() once the app is ready
let trameRca = null;
let server = null; // { process, close } from launchTrame
let mainWindow = null;
let quitting = false;

// -----------------------------------------------------------------------------
// Server
// -----------------------------------------------------------------------------

function serverCommand() {
  const exe = process.platform === "win32" ? `${SERVER_NAME}.exe` : SERVER_NAME;
  const bundled = app.isPackaged
    ? path.join(process.resourcesPath, "server", exe)
    : path.join(__dirname, "server", "dist", SERVER_NAME, exe);

  if (process.env.VERA_SERVER_DEV !== "1" && fs.existsSync(bundled)) {
    return { command: bundled, args: [], cwd: path.dirname(bundled) };
  }
  if (app.isPackaged) {
    throw new Error(`Server executable not found: ${bundled}`);
  }
  // Development fallback: run the same entry point from the parent project sources with uv
  return {
    command: "uv",
    args: ["run", "--project", "..", "python", path.join("server", "entry.py")],
    cwd: __dirname,
  };
}

// Arguments given to the app (e.g. `--data ~/cases`) are forwarded to the server.
function forwardedArgs() {
  const args = process.argv.slice(app.isPackaged ? 1 : 2);
  const start = args.indexOf("--");
  const userArgs = start >= 0 ? args.slice(start + 1) : args;
  // Drop Chromium / Electron switches and the macOS Finder process serial number
  return userArgs.filter(
    (arg) =>
      !arg.startsWith("-psn_") &&
      !arg.startsWith("--inspect") &&
      !arg.startsWith("--remote-debugging") &&
      !arg.startsWith("--no-sandbox"),
  );
}

// Start the server and load its page in the window once it serves it (trame-rca-electron
// waits for the server to connect to its texture channel and to send the page URL).
// The server output goes to the Electron stdout / stderr.
function startServer(win) {
  return new Promise((resolve, reject) => {
    const { command, args, cwd } = serverCommand();
    let started = false;
    const timer = setTimeout(
      () => reject(new Error(`The server was not ready within ${STARTUP_TIMEOUT_MS / 1000}s.`)),
      STARTUP_TIMEOUT_MS,
    );
    // launchTrame loads the server page once ready: the window leaves loading.html
    win.webContents.on("did-navigate", (_event, url) => {
      if (!started && url.startsWith(`http://${HOST}:`)) {
        started = true;
        clearTimeout(timer);
        resolve();
      }
    });

    server = trameRca.launchTrame(win, {
      command,
      // launchTrame appends --server --port 0 --trame-electron-port <port>
      args: [
        ...args,
        "--electron", // render with trame-rca into a shared texture, no VTK.wasm
        "--host",
        HOST,
        "--timeout",
        "0", // never reap on idle, Electron owns the lifetime
        ...forwardedArgs(),
      ],
      cwd,
      env: { ...process.env, PYTHONUNBUFFERED: "1" },
      onExit: (code, signal, error) => {
        server = null;
        clearTimeout(timer);
        if (quitting || !mainWindow) {
          return; // launchTrame stops the server when the window closes
        }
        const message = error
          ? `Could not start the server: ${error.message}`
          : `The server stopped ${started ? "unexpectedly" : "during startup"} (code ${code}, signal ${signal}).`;
        if (started) {
          fatal(message);
        } else {
          reject(new Error(message));
        }
      },
    });
  });
}

function stopServer() {
  const current = server;
  server = null;
  current?.close();
}

// -----------------------------------------------------------------------------
// Window
// -----------------------------------------------------------------------------

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1400,
    height: 900,
    minWidth: 800,
    minHeight: 600,
    title: "VeraInViewer",
    backgroundColor: "#1e1e1e",
    show: false,
    // trame-rca-electron preload, which draws the shared textures into the page (not sandboxed)
    webPreferences: trameRca.webPreferences({
      contextIsolation: true,
      nodeIntegration: false,
    }),
  });
  mainWindow.setMenuBarVisibility(false);
  mainWindow.once("ready-to-show", () => mainWindow.show());
  mainWindow.on("closed", () => {
    mainWindow = null;
  });

  // Links to other sites open in the default browser, not in the app
  const isLocal = (url) => url.startsWith(`http://${HOST}:`) || url.startsWith("file:");
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (!isLocal(url)) {
      shell.openExternal(url);
    }
    return { action: "deny" };
  });
  mainWindow.webContents.on("will-navigate", (event, url) => {
    if (!isLocal(url)) {
      event.preventDefault();
      shell.openExternal(url);
    }
  });

  mainWindow.loadFile(path.join(__dirname, "loading.html"));
  return mainWindow;
}

function fatal(message) {
  if (quitting) {
    return;
  }
  quitting = true;
  stopServer();
  dialog.showMessageBoxSync({
    type: "error",
    title: "VeraInViewer",
    message,
    detail: "See the application output for the server logs.",
  });
  app.exit(1);
}

// -----------------------------------------------------------------------------
// App lifecycle
// -----------------------------------------------------------------------------

app.on("window-all-closed", () => app.quit());
app.on("before-quit", () => {
  quitting = true;
  stopServer();
});
process.on("exit", stopServer);

app.whenReady().then(async () => {
  try {
    trameRca = await import("@kitware/trame-rca-electron");
    await startServer(createWindow());
  } catch (error) {
    fatal(error.message);
  }
});
