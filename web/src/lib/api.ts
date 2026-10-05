import type { ApiErrorCode } from "./apiErrorCodes";
import { buildApiUrl, resolveApiBase } from "./apiUrl";
import { parseRetryAfterSeconds } from "./rateLimitMessage";
import { supabase } from "./supabase";

// The API's origin: VITE_API_BASE_URL in a deployed build (scripts/apiBaseGuard.ts
// refuses to build without it), the dev proxy's /api otherwise -- see apiUrl.ts.
const API_BASE = resolveApiBase(import.meta.env.VITE_API_BASE_URL as string | undefined);

// Thin fetch wrapper for the spine. Attaches the user's Supabase access
// token (verified server-side against JWKS -- auth.py) and prefixes the API
// base: /api in development, where the Vite dev proxy rewrites it to the
// spine's root, or the deployed API's origin (apiUrl.ts).
//
// The spine's error shape is Proposal Appendix B's structured contract
// (Sprint 2.6a): `{"error": {code, message, ...}}`. `code` is exposed
// here (not just `message`) so callers can branch on it later --
// SETUP_REQUIRED vs. NOT_FOUND mean different UI, not just different text.

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    message: string,
    public readonly code?: ApiErrorCode,
    // The envelope's own `retryable` flag (errors.py): whether asking again can
    // change the outcome. Undefined when the reply carried none.
    public readonly retryable?: boolean,
    // For a 429 RATE_LIMITED: the whole seconds the server asks the caller to wait
    // (the envelope's details, or the Retry-After header). Undefined otherwise.
    public readonly retryAfterSeconds?: number,
  ) {
    super(message);
  }
}

interface ErrorBody {
  error?: {
    code?: string;
    message?: string;
    retryable?: boolean;
    details?: unknown;
  };
}

function apiErrorFrom(response: Response, body: ErrorBody | null): ApiError {
  return new ApiError(
    response.status,
    body?.error?.message ?? `Request failed (${response.status})`,
    body?.error?.code,
    typeof body?.error?.retryable === "boolean" ? body.error.retryable : undefined,
    parseRetryAfterSeconds(body?.error?.details, response.headers.get("Retry-After")),
  );
}

async function accessToken(): Promise<string> {
  const { data } = await supabase.auth.getSession();
  const token = data.session?.access_token;
  if (!token) {
    throw new ApiError(401, "Not signed in.");
  }
  return token;
}

export async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const token = await accessToken();
  const response = await fetch(buildApiUrl(API_BASE, path), {
    ...init,
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
      ...init?.headers,
    },
  });

  if (response.status === 204) {
    return undefined as T;
  }

  const body = (await response.json().catch(() => null)) as ErrorBody | null;
  if (!response.ok) {
    throw apiErrorFrom(response, body);
  }
  return body as T;
}

// For binary responses (currently just the PDF download) -- `apiFetch`
// above always calls `response.json()`, which can't handle a PDF body.
// Errors still come back as the same JSON envelope, so those are parsed
// the same way `apiFetch` does.
export async function apiFetchBlob(path: string): Promise<Blob> {
  const token = await accessToken();
  const response = await fetch(buildApiUrl(API_BASE, path), {
    headers: { Authorization: `Bearer ${token}` },
  });

  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as ErrorBody | null;
    throw apiErrorFrom(response, body);
  }
  return response.blob();
}
