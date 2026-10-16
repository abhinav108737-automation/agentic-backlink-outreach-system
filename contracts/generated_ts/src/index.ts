// Lead-owned wrapper. `api.d.ts` is generated from contracts/openapi/openapi.json.
import type { components, paths } from "./api";

export type { components, paths };
export type Schemas = components["schemas"];
export type ReadinessReport = Schemas["ReadinessReport"];
export type ComponentHealth = Schemas["ComponentHealth"];
export const CONTRACT_VERSION = "0.1.0" as const;
