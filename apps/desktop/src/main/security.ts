// Electron hardening in one auditable place (SEC-02).
import type { WebPreferences } from "electron";

/** Renderer talks to main over IPC only, so it needs no network access at all. */
export const CSP = [
  "default-src 'none'",
  "script-src 'self'",
  "style-src 'self'",
  "img-src 'self' data:",
  "font-src 'self'",
  "connect-src 'none'",
  "object-src 'none'",
  "base-uri 'none'",
  "form-action 'none'",
  "frame-ancestors 'none'",
].join("; ");

export function webPreferences(preloadPath: string): WebPreferences {
  return {
    preload: preloadPath,
    contextIsolation: true,
    nodeIntegration: false,
    nodeIntegrationInWorker: false,
    nodeIntegrationInSubFrames: false,
    sandbox: true,
    webSecurity: true,
    allowRunningInsecureContent: false,
    experimentalFeatures: false,
    webviewTag: false,
    spellcheck: false,
  };
}

/** IPC sender check: the frame that invoked us must be our own app bundle. */
export function makeTrustSender(appUrl: string): (event: unknown) => boolean {
  return (event) => {
    const url = (event as { senderFrame?: { url?: string } } | null)?.senderFrame?.url;
    return typeof url === "string" && isAllowedNavigation(url, appUrl);
  };
}

/** Pure decision used by will-navigate: only the app's own bundle may be loaded. */
export function isAllowedNavigation(url: string, appUrl: string): boolean {
  try {
    const target = new URL(url);
    const app = new URL(appUrl);
    if (app.protocol === "file:") return target.protocol === "file:" && target.pathname === app.pathname;
    return target.origin === app.origin; // dev server
  } catch {
    return false;
  }
}
