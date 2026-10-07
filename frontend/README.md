# AetherGate v2 — Web Console

Same-origin React + TypeScript operator console for the v2 `/admin/v1` API. See
`../docs/web-console.md` for the design, data sources, and deployment path.

## Setup

```bash
npm install
```

## Development

```bash
npm run dev            # Vite dev server (proxies /admin, /health, /v1 to the local API)
npm run build          # tsc -b && vite build
npm run lint           # eslint
npm run preview        # serve the production build
```

The Vite dev proxy target defaults to `http://localhost:8000`; override with
`VITE_API_PROXY_TARGET` if the API binds another port. Runtime client code uses same-origin relative
paths — it never hard-codes the API host port.

## Generated API types

The frontend does not hand-maintain endpoint/types as its source of truth:

```bash
npm run generate:openapi   # deterministic FastAPI OpenAPI export -> src/generated/openapi.json
npm run generate:client    # openapi-typescript -> src/generated/schema.d.ts (committed)
```

`src/generated/schema.d.ts` is auto-generated; do not edit it. The application wrapper
(`src/lib/client.ts`) owns credentials, CSRF, and error handling on top of the generated types.

## Tests

```bash
npm run test          # vitest unit/component tests
npm run test:watch    # vitest in watch mode
npm run test:e2e      # Playwright browser tests (requires a live stack)
```

The Playwright specs in `e2e/` target `WEB_BASE_URL` (default `http://127.0.0.1:8080`) and use the
deterministic local OIDC provider — no external IdP credentials required.
