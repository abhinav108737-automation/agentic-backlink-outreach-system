// req: SEC-01
// Exercises the REAL backend process through the REAL ipc handlers and bridge.
import { resolve } from "node:path";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { BackendSupervisor } from "../src/main/backend";
import { type IpcMainLike, registerIpc } from "../src/main/ipc";
import { createBridge, type IpcRendererLike } from "../src/preload/bridge";
import { CHANNELS } from "../src/shared/ipc";

const repoRoot = resolve(__dirname, "../../..");
const sup = new BackendSupervisor({
  command: "uv",
  args: ["run", "--project", repoRoot, "python", "-m", "kb_api"],
  cwd: repoRoot,
});

// An in-memory IPC pair that structured-clones payloads like Electron does.
function wire(): { bridge: ReturnType<typeof createBridge>; observed: string[] } {
  const handlers = new Map<string, (e: unknown, ...a: unknown[]) => unknown>();
  const main: IpcMainLike = { handle: (ch, fn) => void handlers.set(ch, fn) };
  registerIpc(main, sup, () => true);
  const observed: string[] = [];
  const renderer: IpcRendererLike = {
    invoke: async (ch, ...args) => {
      const fn = handlers.get(ch);
      if (!fn) throw new Error(`no handler for ${ch}`);
      const out = structuredClone(await fn({}, ...structuredClone(args)));
      observed.push(JSON.stringify(out));
      return out;
    },
    on: () => undefined,
    removeListener: () => undefined,
  };
  return { bridge: createBridge(renderer), observed };
}

beforeAll(async () => {
  await sup.start();
});
afterAll(() => sup.stop());

describe("backend supervisor + IPC proxy", () => {
  it("completes the handshake", () => {
    expect(sup.status()).toEqual({ state: "ready" });
  });

  it("proxies authenticated requests: live 200, private route reaches the backend (501 stub)", async () => {
    const { bridge } = wire();
    expect((await bridge.request({ method: "GET", path: "/health/live" })).status).toBe(200);
    const stub = await bridge.request({ method: "GET", path: "/prospects" });
    expect(stub.status).toBe(501); // 501, not 401: the token was attached by main
    expect(stub.correlationId).toMatch(/^[0-9a-f]{16}$/);
    const ready = await bridge.request({ method: "GET", path: "/health/ready" });
    expect([200, 503]).toContain(ready.status);
    expect((ready.body as { components: unknown[] }).components).toHaveLength(3);
  });

  it("renderer-visible surface can never contain the token", async () => {
    const token = Reflect.get(sup, "token") as string;
    expect(token.length).toBeGreaterThanOrEqual(43);
    const { bridge, observed } = wire();
    await bridge.backendStatus();
    await bridge.request({ method: "GET", path: "/health/ready" });
    await bridge.request({ method: "GET", path: "/prospects" });
    const missing = await bridge.request({ method: "GET", path: "/health/nope-404" });
    expect(missing.status).toBe(404); // a real backend error body must not leak the token either
    const everything = observed.join("\n") + JSON.stringify(Object.keys(bridge)) + JSON.stringify(sup.status());
    expect(everything).not.toContain(token);
    expect(everything.toLowerCase()).not.toContain("bearer");
    expect(everything.toLowerCase()).not.toContain("authorization");
  });

  it("refuses to proxy absolute URLs even if the bridge validation were bypassed", async () => {
    await expect(sup.request({ method: "GET", path: "http://evil.example/steal" })).rejects.toThrow(TypeError);
    await expect(sup.request({ method: "GET", path: "//evil.example" })).rejects.toThrow(TypeError);
  });

  it("registers only the two allowlisted IPC channels", () => {
    const seen: string[] = [];
    registerIpc({ handle: (ch) => void seen.push(ch) }, sup, () => true);
    expect(seen.sort()).toEqual([CHANNELS.backendStatus, CHANNELS.request].sort());
  });
});

describe("ipc sender validation", () => {
  it("rejects every channel when the sender is not our app frame", () => {
    const handlers = new Map<string, (e: unknown, ...a: unknown[]) => unknown>();
    registerIpc({ handle: (ch, fn) => void handlers.set(ch, fn) }, sup, () => false);
    for (const fn of handlers.values()) expect(() => fn({}, { method: "GET", path: "/health/live" })).toThrow(/untrusted/);
  });
  it("passes the event to the trust check and proceeds when trusted", async () => {
    const handlers = new Map<string, (e: unknown, ...a: unknown[]) => unknown>();
    const seen: unknown[] = [];
    registerIpc({ handle: (ch, fn) => void handlers.set(ch, fn) }, sup, (e) => (seen.push(e), true));
    const evt = { senderFrame: { url: "file:///x" } };
    const res = (await handlers.get(CHANNELS.request)?.(evt, { method: "GET", path: "/health/live" })) as { status: number };
    expect(seen).toEqual([evt]);
    expect(res.status).toBe(200);
  });
});

describe("failure handling", () => {
  it("refuses a second start() on a running supervisor", async () => {
    await expect(sup.start()).rejects.toThrow(/already started/);
  });

  it("reports failed (not hung) when the backend cannot start", async () => {
    const bad = new BackendSupervisor({ command: "node", args: ["-e", "process.exit(3)"], cwd: repoRoot, startTimeoutMs: 15_000 });
    await expect(bad.start()).rejects.toThrow();
    expect(bad.status().state).toBe("failed");
    await expect(bad.request({ method: "GET", path: "/health/live" })).rejects.toThrow(/not ready/);
  });

  it("stop() ends the backend and status becomes stopped", async () => {
    const s = new BackendSupervisor({ command: "uv", args: ["run", "--project", repoRoot, "python", "-m", "kb_api"], cwd: repoRoot });
    await s.start();
    s.stop();
    expect(s.status().state).toBe("stopped");
  });
});
