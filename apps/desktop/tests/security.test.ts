// req: SEC-02
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { CSP, isAllowedNavigation, makeTrustSender, webPreferences } from "../src/main/security";

describe("window hardening", () => {
  const wp = webPreferences("/p/preload.cjs");

  it("sets the mandatory flags", () => {
    expect(wp.contextIsolation).toBe(true);
    expect(wp.nodeIntegration).toBe(false);
    expect(wp.sandbox).toBe(true);
    expect(wp.webSecurity).toBe(true);
    expect(wp.nodeIntegrationInWorker).toBe(false);
    expect(wp.nodeIntegrationInSubFrames).toBe(false);
    expect(wp.allowRunningInsecureContent).toBe(false);
    expect(wp.webviewTag).toBe(false);
  });
});

describe("content security policy", () => {
  it("has no unsafe-eval / unsafe-inline / remote origins and no network from the renderer", () => {
    expect(CSP).not.toMatch(/unsafe-eval|unsafe-inline|https?:|\*/);
    expect(CSP).toContain("default-src 'none'");
    expect(CSP).toContain("connect-src 'none'");
    expect(CSP).toContain("object-src 'none'");
  });

  it("is identical in index.html (meta) and security.ts (header) so they cannot drift", () => {
    const html = readFileSync(resolve(__dirname, "../src/renderer/index.html"), "utf8");
    const m = /http-equiv="Content-Security-Policy" content="([^"]+)"/.exec(html);
    expect(m?.[1]).toBe(CSP);
  });
});

describe("navigation guard", () => {
  const app = "file:///app/out/renderer/index.html";
  it("allows only the app bundle", () => {
    expect(isAllowedNavigation(app, app)).toBe(true);
    expect(isAllowedNavigation("https://evil.example", app)).toBe(false);
    expect(isAllowedNavigation("file:///etc/passwd", app)).toBe(false);
    expect(isAllowedNavigation("http://127.0.0.1:9999/", app)).toBe(false);
    expect(isAllowedNavigation("not a url", app)).toBe(false);
  });
  it("allows the dev server origin only in dev", () => {
    const dev = "http://localhost:5173/";
    expect(isAllowedNavigation("http://localhost:5173/#/x", dev)).toBe(true);
    expect(isAllowedNavigation("http://localhost:5174/", dev)).toBe(false);
  });
});

describe("ipc sender trust", () => {
  const trust = makeTrustSender("file:///app/out/renderer/index.html");
  it("trusts only our own app frame", () => {
    expect(trust({ senderFrame: { url: "file:///app/out/renderer/index.html" } })).toBe(true);
    expect(trust({ senderFrame: { url: "https://evil.example/" } })).toBe(false);
    expect(trust({ senderFrame: { url: "file:///tmp/evil.html" } })).toBe(false);
    expect(trust({ senderFrame: {} })).toBe(false);
    expect(trust({})).toBe(false);
    expect(trust(null)).toBe(false);
  });
});
