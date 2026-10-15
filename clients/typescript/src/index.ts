import createClient, { type Client } from "openapi-fetch";

import type { components, paths } from "./generated/schema.js";

export type DatePipetteClient = Client<paths>;
export type ProblemDetails = components["schemas"]["ProblemDetails"];

export interface ClientOptions {
  baseUrl: string;
  fetch?: typeof globalThis.fetch;
}

export interface ServiceTokenClientOptions extends ClientOptions {
  token: string;
}

export interface DatePipetteResponse<T> {
  data?: T;
  error?: unknown;
  response: Response;
}

export class DatePipetteApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly title: string;
  readonly detail: string;
  readonly requestId: string | null;
  readonly retryAfterSeconds: number | null;
  readonly problem: ProblemDetails | null;

  constructor(response: Response, error: unknown) {
    const problem = isProblemDetails(error) ? error : null;
    const title = problem?.title ?? "DatePipette API request failed";
    const detail = problem?.detail ?? `The API returned HTTP ${response.status}.`;
    super(`${title}: ${detail}`);
    this.name = "DatePipetteApiError";
    this.status = response.status;
    this.code = problem?.code ?? "unexpected_api_response";
    this.title = title;
    this.detail = detail;
    this.requestId = response.headers.get("x-request-id");
    this.retryAfterSeconds = parseRetryAfter(response.headers.get("retry-after"));
    this.problem = problem;
  }
}

function parseRetryAfter(value: string | null): number | null {
  if (value === null || !/^\d+$/.test(value)) {
    return null;
  }
  const seconds = Number(value);
  return Number.isSafeInteger(seconds) && seconds > 0 ? seconds : null;
}

export function createBrowserClient(options: ClientOptions): DatePipetteClient {
  return createClient<paths>({
    baseUrl: normalizeBaseUrl(options.baseUrl),
    credentials: "include",
    ...(options.fetch ? { fetch: options.fetch } : {}),
  });
}

export function createServiceTokenClient(
  options: ServiceTokenClientOptions,
): DatePipetteClient {
  const token = options.token.trim();
  if (!token) {
    throw new TypeError("A non-empty DatePipette API token is required.");
  }
  return createClient<paths>({
    baseUrl: normalizeBaseUrl(options.baseUrl),
    headers: { Authorization: `Bearer ${token}` },
    ...(options.fetch ? { fetch: options.fetch } : {}),
  });
}

export function unwrap<T>(result: DatePipetteResponse<T>): T {
  if (result.response.ok) {
    return result.data as T;
  }
  throw new DatePipetteApiError(result.response, result.error);
}

function normalizeBaseUrl(value: string): string {
  const normalized = value.trim().replace(/\/+$/, "");
  if (!normalized) {
    throw new TypeError("A non-empty DatePipette API base URL is required.");
  }
  return normalized;
}

function isProblemDetails(value: unknown): value is ProblemDetails {
  if (typeof value !== "object" || value === null) {
    return false;
  }
  const candidate = value as Partial<Record<keyof ProblemDetails, unknown>>;
  return (
    typeof candidate.code === "string" &&
    typeof candidate.title === "string" &&
    typeof candidate.detail === "string"
  );
}

export type { components, operations, paths } from "./generated/schema.js";
