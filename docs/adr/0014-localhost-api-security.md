# ADR-0014: Localhost API security between Electron and FastAPI

Status: Accepted. Date: 2026-10-16

## Context
The renderer talks to a local FastAPI backend. Any other local process, or a malicious web page via DNS rebinding, could otherwise reach an unauthenticated localhost API holding campaign data and the ability to send email. The source documents implied an `auth` module but specified nothing.

## Decision
Per-launch authentication:
- The backend binds **127.0.0.1 only**, on a dynamically selected free port.
- Electron main generates a high-entropy (256-bit) token per launch, passes it to the backend over **stdin** (never argv, which is world-readable in a process list), and the backend prints `READY port=<n>` on stdout to complete the handshake.
- The renderer never receives the token. It calls `kenApi.request()` over IPC; **main** attaches the `Authorization: Bearer` header and proxies to the backend.
- Every privileged request is authenticated with a constant-time comparison; failures return 401.
- CORS denies all origins; a Host-header allowlist (`127.0.0.1:<port>`) blocks DNS rebinding.
- The token is never persisted to disk and never logged; the redaction list covers it.
- Requests carry `X-Correlation-ID`; mutations accept `Idempotency-Key`.

## Consequences
- The renderer cannot be tricked into leaking a credential it does not hold.
- Debugging with raw `curl` requires reading the token from the running main process, which is intended friction.
- Foundation Gate tests assert each property, including that the renderer cannot read the token.
