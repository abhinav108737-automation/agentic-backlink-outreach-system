/// <reference types="vite/client" />
import type { KenApi } from "../shared/ipc";

declare global {
  interface Window {
    kenApi: KenApi;
  }
}
