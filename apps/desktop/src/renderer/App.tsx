import { useEffect, useState } from "react";
import type { ReadinessReport } from "@ken/contracts";
import type { BackendStatus } from "../shared/ipc";

export function App() {
  const [status, setStatus] = useState<BackendStatus>({ state: "starting" });
  const [report, setReport] = useState<ReadinessReport | null>(null);

  useEffect(() => {
    void window.kenApi.backendStatus().then(setStatus);
    return window.kenApi.onBackendStatus(setStatus);
  }, []);

  useEffect(() => {
    if (status.state !== "ready") return;
    void window.kenApi.request({ method: "GET", path: "/health/ready" }).then((r) => setReport(r.body as ReadinessReport));
  }, [status.state]);

  return (
    <main className="shell">
      <h1>Ken Backlink OS</h1>
      <p className="mode">Mode: shadow &middot; real send disabled</p>
      <section aria-label="System health">
        <h2>System health</h2>
        <p data-testid="backend-state">Backend: {status.state}{status.error ? ` (${status.error})` : ""}</p>
        {report && (
          <ul>
            <li data-testid="overall">Overall: {report.status}</li>
            {report.components.map((c) => (
              <li key={c.name}>{c.name}: {c.status}</li>
            ))}
          </ul>
        )}
      </section>
    </main>
  );
}
