import { app, BrowserWindow, ipcMain, session } from "electron";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { BackendSupervisor } from "./backend";
import { registerIpc } from "./ipc";
import { CSP, isAllowedNavigation, makeTrustSender, webPreferences } from "./security";
import { CHANNELS } from "../shared/ipc";

const here = dirname(fileURLToPath(import.meta.url));
// Developer escape hatches (custom backend command, dev-server URL, smoke mode, repo root) are
// honoured ONLY in unpackaged runs, so a shipped app cannot be redirected by its environment.
const dev = !app.isPackaged;
const devEnv = (k: string): string | undefined => (dev ? process.env[k] : undefined);
const repoRoot = devEnv("KB_REPO_ROOT") ?? resolve(here, "../../../..");
const smoke = devEnv("KEN_SMOKE") === "1";
const customCmd = devEnv("KB_BACKEND_CMD");
const rendererUrl = devEnv("ELECTRON_RENDERER_URL");
const appUrl = rendererUrl ?? `file://${join(here, "../renderer/index.html")}`;

const backend = new BackendSupervisor({
  command: customCmd ?? "uv",
  args: customCmd ? [] : ["run", "--project", repoRoot, "python", "-m", "kb_api"],
  cwd: repoRoot,
});

function createWindow(): BrowserWindow {
  const win = new BrowserWindow({
    width: 1280,
    height: 800,
    show: false,
    webPreferences: webPreferences(join(here, "../preload/index.cjs")),
  });
  const guard = (e: { preventDefault(): void }, url: string): void => {
    if (!isAllowedNavigation(url, appUrl)) e.preventDefault();
  };
  win.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
  win.webContents.on("will-navigate", guard);
  win.webContents.on("will-redirect", guard);
  win.webContents.on("will-attach-webview", (e) => e.preventDefault());
  win.once("ready-to-show", () => win.show());
  backend.onStatus((s) => {
    if (!win.isDestroyed()) win.webContents.send(CHANNELS.statusEvent, s);
  });
  if (rendererUrl) void win.loadURL(appUrl);
  else void win.loadFile(join(here, "../renderer/index.html"));
  return win;
}

async function runSmoke(win: BrowserWindow): Promise<void> {
  const wc = win.webContents;
  // The page usually finishes loading while the backend is still starting, so don't wait for an
  // event that already fired.
  if (wc.isLoading()) await new Promise<void>((r) => wc.once("did-finish-load", () => r()));
  const probe = (await wc.executeJavaScript(
    `JSON.stringify({kenApi: typeof window.kenApi, keys: Object.keys(window.kenApi||{}).sort(),
      require: typeof window.require, process: typeof window.process, Buffer: typeof window.Buffer})`,
  )) as string;
  const health = await backend.request({ method: "GET", path: "/health/ready" });
  const live = await backend.request({ method: "GET", path: "/health/live" });
  console.log(`SMOKE_PROBE ${probe}`);
  console.log(`SMOKE_HEALTH ready=${health.status} live=${live.status}`);
  console.log("SMOKE_OK");
}

app.whenReady().then(async () => {
  if (smoke) {
    setTimeout(() => {
      console.error("SMOKE_TIMEOUT");
      app.exit(1);
    }, 60_000).unref();
  }
  session.defaultSession.webRequest.onHeadersReceived((details, cb) => {
    cb({ responseHeaders: { ...details.responseHeaders, "Content-Security-Policy": [CSP] } });
  });
  session.defaultSession.setPermissionRequestHandler((_wc, _perm, cb) => cb(false)); // deny all
  session.defaultSession.setPermissionCheckHandler(() => false);
  registerIpc(ipcMain, backend, makeTrustSender(appUrl));
  const win = createWindow();
  try {
    await backend.start();
    if (smoke) {
      await runSmoke(win);
      app.quit();
    }
  } catch (e) {
    console.error(`backend failed: ${(e as Error).message}`);
    if (smoke) app.exit(1);
  }
});

app.on("window-all-closed", () => app.quit());
app.on("before-quit", () => backend.stop());
