// Electron shell for VeraInViewer: starts the frozen trame server on a port picked by the
// server itself and shows it in a native window. The server lives as long as the app.

const { app, BrowserWindow, dialog, shell } = require("electron");
const { spawn, spawnSync } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");

const SERVER_NAME = "vera-in-viewer-server";
const HOST = "127.0.0.1";
// Printed by server/entry.py once the server listens (trame on_server_ready)
const READY_LINE = /^electron-server-port=(\d+)$/;
const STARTUP_TIMEOUT_MS = 120_000; // first start of a fresh bundle can be slow (AV scans, cold cache)
const LOG_LINES = 200;

let server = null;
let mainWindow = null;
let quitting = false;
const serverLog = [];

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

function log(line) {
  console.log(line);
  serverLog.push(line);
  serverLog.splice(0, Math.max(0, serverLog.length - LOG_LINES));
}

// Call onLine for every complete line of a stream (chunks may split lines)
function readLines(stream, onLine) {
  let pending = "";
  stream.setEncoding("utf8");
  stream.on("data", (chunk) => {
    const lines = (pending + chunk).split(/\r?\n/);
    pending = lines.pop();
    lines.forEach(onLine);
  });
  stream.on("end", () => pending && onLine(pending));
}

// Start the server on a port of its choosing (--port 0) and resolve with that port
// once it prints the ready line.
function startServer() {
  return new Promise((resolve, reject) => {
    const { command, args, cwd } = serverCommand();
    const fullArgs = [
      ...args,
      "--server", // do not open a browser
      "--host",
      HOST,
      "--port",
      "0",
      "--timeout",
      "0", // never reap on idle, Electron owns the lifetime
      ...forwardedArgs(),
    ];
    log(`> ${command} ${fullArgs.join(" ")}`);

    let ready = false;
    const timer = setTimeout(
      () => reject(new Error(`The server was not ready within ${STARTUP_TIMEOUT_MS / 1000}s.`)),
      STARTUP_TIMEOUT_MS,
    );

    server = spawn(command, fullArgs, {
      cwd,
      env: { ...process.env, PYTHONUNBUFFERED: "1" },
      windowsHide: true,
    });
    readLines(server.stdout, (line) => {
      const match = !ready && line.trim().match(READY_LINE);
      if (match) {
        ready = true;
        clearTimeout(timer);
        resolve(Number(match[1]));
      } else {
        log(line);
      }
    });
    readLines(server.stderr, log);
    server.on("error", (error) => {
      clearTimeout(timer);
      reject(new Error(`Could not start the server: ${error.message}`));
    });
    server.on("exit", (code, signal) => {
      server = null;
      clearTimeout(timer);
      const message = `The server stopped ${ready ? "unexpectedly" : "during startup"} (code ${code}, signal ${signal}).`;
      if (ready) {
        fatal(message);
      } else {
        reject(new Error(message));
      }
    });
  });
}

function stopServer() {
  if (!server) {
    return;
  }
  const { pid } = server;
  server = null;
  if (process.platform === "win32") {
    spawnSync("taskkill", ["/pid", String(pid), "/T", "/F"], { windowsHide: true });
  } else {
    try {
      process.kill(pid, "SIGTERM");
    } catch {
      // already gone
    }
  }
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
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
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
  const details = serverLog.slice(-30).join("\n");
  dialog.showMessageBoxSync({
    type: "error",
    title: "VeraInViewer",
    message,
    detail: details || undefined,
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
  createWindow();
  try {
    const port = await startServer();
    if (mainWindow) {
      await mainWindow.loadURL(`http://${HOST}:${port}/`);
    }
  } catch (error) {
    fatal(error.message);
  }
});
