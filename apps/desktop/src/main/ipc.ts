import { CHANNELS, type BackendStatus, type KenRequest, type KenResponse } from "../shared/ipc";

/** Minimal surface so handlers are testable without the Electron runtime. */
export interface IpcMainLike {
  handle(channel: string, listener: (event: unknown, ...args: unknown[]) => unknown): void;
}
export interface Backend {
  status(): BackendStatus;
  request(req: KenRequest): Promise<KenResponse>;
}

/** Every handler first checks the sender: only our own app frame may drive the privileged proxy. */
export function registerIpc(ipc: IpcMainLike, backend: Backend, trustSender: (event: unknown) => boolean): void {
  const guarded =
    <T>(fn: (...args: unknown[]) => T) =>
    (event: unknown, ...args: unknown[]): T => {
      if (!trustSender(event)) throw new Error("untrusted IPC sender");
      return fn(...args);
    };
  ipc.handle(CHANNELS.request, guarded((req) => backend.request(req as KenRequest)));
  ipc.handle(CHANNELS.backendStatus, guarded(() => backend.status()));
}
