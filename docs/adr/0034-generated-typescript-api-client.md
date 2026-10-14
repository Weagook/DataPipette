# ADR-0034: Generated TypeScript API client

## Status

Accepted — 2026-10-14

## Context

The official frontend and external integrations must use the same public API without maintaining handwritten request and response types. The committed OpenAPI document already detects backend contract drift, but it does not by itself prove that a TypeScript consumer can compile, authenticate, handle safe failures, or follow the dashboard integration flow.

## Decision

- Maintain an independently buildable package at `clients/typescript`. It is reusable by the future React application and external TypeScript integrations without selecting the frontend workspace or design system.
- Generate TypeScript schema, operation, and path types deterministically from the committed `docs/openapi.json` with `openapi-typescript`. Commit the generated file for review and verify regeneration byte-for-byte in CI.
- Use `openapi-fetch` as the small typed transport rather than generating a large endpoint-specific class layer. The OpenAPI paths remain the only request/response DTO source of truth.
- Expose separate browser-cookie and service-token constructors. Browser mode uses included credentials and leaves CSRF headers explicit. Bearer mode requires a non-empty caller-supplied token and does not fall back to cookies.
- Preserve API Problem Details and `X-Request-ID` in a stable client error. Do not log, persist, embed, or recover bearer plaintext.
- Pin an npm lockfile and verify generation, strict type checking, runtime tests, build output, and package contents in a dedicated CI workflow. Publishing to npm is a separate future decision.

## Consequences

- API changes that compile in Python but break a TypeScript consumer fail CI in the same pull request.
- The frontend can begin from a reviewed typed transport instead of inventing a second contract layer.
- Consumers call OpenAPI path strings directly; higher-level workflow helpers may be added later only when they encode stable cross-call behavior rather than duplicate endpoints.
- Generated-file reviews can be large after broad API changes, but the diff makes client compatibility explicit.
- The standalone package currently uses npm; this does not decide the package manager or workspace layout of the future frontend.

## Alternatives considered

- **Handwritten DTOs and endpoint methods:** rejected because they drift silently and duplicate the public contract.
- **Generate a large class client:** rejected for the initial slice because it adds generator-specific runtime abstractions without improving authorization or workflow semantics.
- **Generate only during local builds:** rejected because uncommitted output prevents contract review and makes downstream builds depend on generator availability.
- **Start the React frontend simultaneously:** rejected because client generation is an independent compatibility boundary and the first UI workflow still requires separate product and design decisions.
