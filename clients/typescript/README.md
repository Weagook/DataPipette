# DataPipette TypeScript API client

This independently buildable package is the typed client for the public DataPipette API. It is currently private to the monorepo and is not published to npm.

The committed `src/generated/schema.ts` is generated from [`docs/openapi.json`](../../docs/openapi.json). Do not edit it by hand. The small runtime wrapper uses `openapi-fetch` and adds explicit browser-cookie and service-token constructors plus safe Problem Details errors.

## Development

From the repository root:

```bash
npm ci --prefix clients/typescript
npm run generate:check --prefix clients/typescript
npm run typecheck --prefix clients/typescript
npm test --prefix clients/typescript
npm run build --prefix clients/typescript
```

After an intentional OpenAPI change, regenerate and review the client schema:

```bash
npm run generate --prefix clients/typescript
```

CI fails when the committed TypeScript schema differs from `docs/openapi.json`.

## Browser session

```ts
import { createBrowserClient, unwrap } from "@datapipette/api-client";

const client = createBrowserClient({ baseUrl: "https://analytics.example.com" });
const dashboards = unwrap(
  await client.GET("/api/v1/dashboards", {
    params: { query: { limit: 50 } },
  }),
);
```

Browser mode sends cookies with `credentials: "include"`. State-changing calls still require the current CSRF cookie value in `X-CSRF-Token`; the client does not read cookies or conceal this security boundary.

## Service token

```ts
import { createServiceTokenClient, unwrap } from "@datapipette/api-client";

const client = createServiceTokenClient({
  baseUrl: "https://analytics.example.com",
  token: process.env.DATAPIPETTE_API_TOKEN!,
});
const dashboards = unwrap(await client.GET("/api/v1/dashboards", {
  params: { query: {} },
}));
```

The token is held in memory and attached only as an `Authorization: Bearer` header. The package never stores or logs it. Use a secret manager in deployed integrations.

The executable [`service-token-dashboard.ts`](examples/service-token-dashboard.ts) lists granted dashboards and queries every saved item independently:

```bash
export DATAPIPETTE_BASE_URL="http://127.0.0.1:8000"
read -rs DATAPIPETTE_API_TOKEN
export DATAPIPETTE_API_TOKEN
npm run example:service-token --prefix clients/typescript
unset DATAPIPETTE_API_TOKEN
```

The token needs `dashboards:read` and an effective dashboard grant. A valid token cannot bypass account status, role, direct/group ACLs, or the saved-item query boundary.

## Errors

`unwrap(...)` returns typed success data or throws `DataPipetteApiError`. The error exposes the safe Problem Details `code`, `title`, `detail`, HTTP `status`, and nullable `requestId`; it does not expose backend exceptions or credentials.
