// Pure bridge factory: the entire renderer-visible API. No token, no Node, no raw ipcRenderer.
import { CHANNELS, type BackendStatus, type KenApi, type KenRequest, validateRequest } from "../shared/ipc";

export interface IpcRendererLike {
  invoke(channel: string, ...args: unknown[]): Promise<unknown>;
  on(channel: string, listener: (event: unknown, ...args: unknown[]) => void): unknown;
  removeListener(channel: string, listener: (event: unknown, ...args: unknown[]) => void): unknown;
}

export function createBridge(ipc: IpcRendererLike): Readonly<KenApi> {
  return Object.freeze({
    request: (req: KenRequest) => ipc.invoke(CHANNELS.request, validateRequest(req)) as ReturnType<KenApi["request"]>,
    backendStatus: () => ipc.invoke(CHANNELS.backendStatus) as Promise<BackendStatus>,
    onBackendStatus: (cb: (s: BackendStatus) => void) => {
      const listener = (_e: unknown, s: unknown): void => cb(s as BackendStatus);
      ipc.on(CHANNELS.statusEvent, listener);
      return () => void ipc.removeListener(CHANNELS.statusEvent, listener);
    },
  });
}
