import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  createBrowserClient,
  createServiceTokenClient,
  DatePipetteApiError,
  unwrap,
} from "../src/index.js";

describe("DatePipette TypeScript client", () => {
  it("sends service tokens only through the Authorization header", async () => {
    let captured: Request | undefined;
    const client = createServiceTokenClient({
      baseUrl: "https://analytics.example.test/",
      token: " dpp_secret ",
      fetch: async (input, init) => {
        captured = new Request(input, init);
        return Response.json({ data: [], meta: { next_cursor: null } });
      },
    });

    const result = await client.GET("/api/v1/dashboards", {
      params: { query: { limit: 10 } },
    });

    assert.deepEqual(unwrap(result).data, []);
    assert.equal(captured?.url, "https://analytics.example.test/api/v1/dashboards?limit=10");
    assert.equal(captured?.headers.get("authorization"), "Bearer dpp_secret");
    assert.equal(captured?.credentials, "same-origin");
  });

  it("uses browser credentials without inventing an Authorization header", async () => {
    let captured: Request | undefined;
    const client = createBrowserClient({
      baseUrl: "https://analytics.example.test",
      fetch: async (input, init) => {
        captured = new Request(input, init);
        return Response.json({ data: [], meta: { next_cursor: null } });
      },
    });

    await client.GET("/api/v1/dashboards", { params: { query: {} } });

    assert.equal(captured?.credentials, "include");
    assert.equal(captured?.headers.has("authorization"), false);
  });

  it("preserves safe problem details and the request identifier", () => {
    const response = Response.json(
      {
        code: "authentication_required",
        title: "Authentication required",
        detail: "A valid browser session or API token is required.",
      },
      { status: 401, headers: { "X-Request-ID": "req-123" } },
    );

    assert.throws(
      () =>
        unwrap({
          response,
          error: {
            code: "authentication_required",
            title: "Authentication required",
            detail: "A valid browser session or API token is required.",
          },
        }),
      (error: unknown) => {
        assert.ok(error instanceof DatePipetteApiError);
        assert.equal(error.status, 401);
        assert.equal(error.code, "authentication_required");
        assert.equal(error.requestId, "req-123");
        return true;
      },
    );
  });

  it("rejects empty configuration values before issuing a request", () => {
    assert.throws(() => createBrowserClient({ baseUrl: " " }), TypeError);
    assert.throws(
      () => createServiceTokenClient({ baseUrl: "https://example.test", token: " " }),
      TypeError,
    );
  });
});
