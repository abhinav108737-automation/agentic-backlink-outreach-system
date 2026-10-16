---
paths:
  - "**/*.ts"
  - "**/*.tsx"
---
- TypeScript strict.
- No `any` without reason.
- Renderer never imports privileged Electron/Node APIs directly.
- Use preload/typed IPC or local API.
- ESLint + TypeScript + Vitest must pass.
