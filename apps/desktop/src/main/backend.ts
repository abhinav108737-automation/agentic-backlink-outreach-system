// Supervises the FastAPI backend and owns the per-launch token (ADR-0014).
// The token lives only in this module: it is never returned, logged, or sent to the renderer.
import { type ChildProcess, spawn } from "node:child_process";
import { randomBytes } from "node:crypto";
import { type BackendStatus, type KenRequest, type KenResponse, validateRequest } from "../shared/ipc";

export interface BackendOptions {
  command: string;
  args: string[];
  cwd: string;
  env?: NodeJS.ProcessEnv;
  startTimeoutMs?: number;
}

const READY = /^READY port=(\d{2,5})$/;

export class BackendSupervisor {
  private child: ChildProcess | null = null;
  private token = "";
  private port = 0;
  private current: BackendStatus = { state: "stopped" };
  private readonly listeners = new Set<(s: BackendStatus) => void>();

  constructor(private readonly opts: BackendOptions) {}

  status(): BackendStatus {
    return { ...this.current };
  }

  onStatus(cb: (s: BackendStatus) => void): () => void {
    this.listeners.add(cb);
    return () => this.listeners.delete(cb);
  }

  private set(s: BackendStatus): void {
    this.current = s;
    for (const l of this.listeners) l({ ...s });
  }

  async start(): Promise<void> {
    if (this.child) throw new Error("backend already started");
    this.token = randomBytes(32).toString("base64url"); // 256-bit, fresh every launch
    this.set({ state: "starting" });
    const child = spawn(this.opts.command, this.opts.args, {
      cwd: this.opts.cwd,
      env: { ...process.env, ...this.opts.env },
      stdio: ["pipe", "pipe", "pipe"], // token goes over stdin, never argv/env (visible to `ps`)
    });
    this.child = child;
    child.stderr?.on("data", (d: Buffer) => process.stderr.write(d)); // backend logs are redacted
    child.on("exit", (code) => {
      this.child = null;
      if (this.current.state !== "stopped") this.set({ state: "failed", error: `backend exited (${code})` });
    });
    child.on("error", (e) => this.set({ state: "failed", error: `spawn failed: ${e.message}` }));

    child.stdin?.write(`${this.token}\n`);

    const port = await new Promise<number>((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error("backend start timeout")), this.opts.startTimeoutMs ?? 45_000);
      let buf = "";
      child.stdout?.on("data", (d: Buffer) => {
        buf += d.toString();
        for (const line of buf.split("\n").slice(0, -1)) {
          const m = READY.exec(line.trim());
          if (m?.[1]) {
            clearTimeout(timer);
            resolve(Number(m[1]));
            return;
          }
        }
      });
      child.on("exit", () => {
        clearTimeout(timer);
        reject(new Error("backend exited before READY"));
      });
    }).catch((e: Error) => {
      this.set({ state: "failed", error: e.message });
      this.stop();
      throw e;
    });
    this.port = port;
    this.set({ state: "ready" });
  }

  /** The only way the renderer reaches the API. Token is attached here, never exposed. */
  async request(raw: KenRequest): Promise<KenResponse> {
    const req = validateRequest(raw);
    if (this.current.state !== "ready") throw new Error(`backend not ready (${this.current.state})`);
    const correlationId = randomBytes(8).toString("hex");
    const res = await fetch(`http://127.0.0.1:${this.port}${req.path}`, {
      method: req.method,
      headers: {
        Authorization: `Bearer ${this.token}`,
        "X-Correlation-ID": correlationId,
        ...(req.body !== undefined ? { "Content-Type": "application/json" } : {}),
      },
      body: req.body !== undefined ? JSON.stringify(req.body) : undefined,
      redirect: "error", // never follow a redirect with the token attached
      signal: AbortSignal.timeout(30_000),
    });
    const text = await res.text();
    let body: unknown = text;
    try {
      body = text ? JSON.parse(text) : null;
    } catch {
      /* keep raw text */
    }
    return { status: res.status, body, correlationId: res.headers.get("x-correlation-id") };
  }

  stop(): void {
    if (this.current.state !== "failed") this.set({ state: "stopped" });
    const c = this.child;
    this.child = null;
    if (!c) return;
    c.stdin?.end(); // EOF is the graceful shutdown signal
    setTimeout(() => c.kill("SIGKILL"), 3000).unref();
  }
}
