// req: SEC-02
import { describe, expect, it, vi } from "vitest";
import { createBridge, type IpcRendererLike } from "../src/preload/bridge";
import { CHANNELS, validateRequest } from "../src/shared/ipc";

const fakeIpc = (): IpcRendererLike & { invoke: ReturnType<typeof vi.fn> } => ({
  invoke: vi.fn().mockResolvedValue({ ok: true }),
  on: vi.fn(),
  removeListener: vi.fn(),
});

describe("preload bridge surface", () => {
  it("exposes exactly request, backendStatus, onBackendStatus (contract C10)", () => {
    expect(Object.keys(createBridge(fakeIpc())).sort()).toEqual(["backendStatus", "onBackendStatus", "request"]);
  });

  it("is frozen and does not leak ipcRenderer or Node primitives", () => {
    const b = createBridge(fakeIpc()) as unknown as Record<string, unknown>;
    expect(Object.isFrozen(b)).toBe(true);
    for (const k of ["ipcRenderer", "invoke", "send", "on", "require", "process", "token", "fetch"]) expect(k in b).toBe(false);
  });

  it("only ever invokes the two allowlisted channels", async () => {
    const ipc = fakeIpc();
    const b = createBridge(ipc);
    await b.request({ method: "GET", path: "/health/ready" });
    await b.backendStatus();
    expect(ipc.invoke.mock.calls.map((c) => c[0])).toEqual([CHANNELS.request, CHANNELS.backendStatus]);
  });

  it("unsubscribes cleanly", () => {
    const ipc = fakeIpc();
    const off = createBridge(ipc).onBackendStatus(() => {});
    off();
    expect(ipc.removeListener).toHaveBeenCalledOnce();
  });

  it("rejects requests that could aim the privileged proxy elsewhere (SSRF / token exfiltration)", async () => {
    const b = createBridge(fakeIpc());
    const bad = [
      "http://evil.example/x", "https://evil.example", "//evil.example/x", "evil.example/x", "/../etc/passwd",
      "/a/../b", "/a\\b", "/a b", "/x\r\nHost: evil", "javascript:alert(1)", "",
      "/health/%2e%2e/settings", "/health/%2E%2E/x", "/health%2fready", "/health%5cready", "/health/%00",
      "/admin/anything", "/openapi.json", "/docs", "/notanarea", "/",
    ];
    for (const path of bad) expect(() => b.request({ method: "GET", path })).toThrow(TypeError);
    expect(() => b.request({ method: "CONNECT" as never, path: "/x" })).toThrow(TypeError);
    expect(() => validateRequest(null)).toThrow(TypeError);
    expect(() => validateRequest({ method: "GET", path: "/" + "a".repeat(3000) })).toThrow(TypeError);
  });

  it("accepts normal API paths", () => {
    for (const path of ["/health/ready", "/prospects?campaign_id=abc-123&limit=10", "/campaigns/0190a1b2-c3d4/run", "/reports/weekly?week=2026-W42"])
      expect(validateRequest({ method: "GET", path }).path).toBe(path);
  });
});
