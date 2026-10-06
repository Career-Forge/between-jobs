import type { ApiErrorCode } from "./apiErrorCodes";
import { buildApiUrl, resolveApiBase } from "./apiUrl";
import { isEnrollmentRefusal } from "./enrollment";
import { parseRetryAfterSeconds } from "./rateLimitMessage";
import { parseSetupFields } from "./setupRequired";
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
    // For a 409 SETUP_REQUIRED: where the fix is (`settings_path`, an app route -- not yet
    // checked, see setupRequired.ts before it goes into a link), which capability needed
    // it, and what is missing. Undefined when the reply carried none.
    public readonly settingsPath?: string,
    public readonly capability?: string,
    public readonly missing?: readonly string[],
    // The envelope's `details.reason`, when the server names one: what kind of refusal this is
    // where the code alone does not say (a RATE_LIMITED that is the server's own capacity, not
    // the person's request count). Undefined otherwise.
    public readonly reason?: string,
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

// `details.reason` when it is text, else undefined: details is the server's, but still read as
// untrusted.
function reasonOf(details: unknown): string | undefined {
  if (typeof details !== "object" || details === null) return undefined;
  const reason = (details as { reason?: unknown }).reason;
  return typeof reason === "string" && reason !== "" ? reason : undefined;
}

function apiErrorFrom(response: Response, body: ErrorBody | null): ApiError {
  const setup = parseSetupFields(body?.error);
  return new ApiError(
    response.status,
    body?.error?.message ?? `Request failed (${response.status})`,
    body?.error?.code,
    typeof body?.error?.retryable === "boolean" ? body.error.retryable : undefined,
    parseRetryAfterSeconds(body?.error?.details, response.headers.get("Retry-After")),
    setup.settingsPath,
    setup.capability,
    setup.missing,
    reasonOf(body?.error?.details),
  );
}

// What to do when the server refuses a request for enrollment (403 ENROLLMENT_REQUIRED): one
// listener, the signed-in shell's gate, which asks again where the person stands and puts the
// enrollment page where the feature was (components/EnrollmentGate.tsx). Without it a tab that was
// enrolled when it opened -- and has since withdrawn, or met a newer agreement, or a server that
// switched the programme on -- would keep being refused for the rest of the visit with no way to
// the page that fixes it. The error is still thrown, so the page that asked shows its own message.
let enrollmentRefusalListener: (() => void) | null = null;

export function setEnrollmentRefusalListener(listener: (() => void) | null): void {
  enrollmentRefusalListener = listener;
}

// The error for a failed reply, after letting the listener know when it was a refusal for
// enrollment. The listener only starts work, and its own failure must not replace the error the
// caller is waiting for.
function failedWith(response: Response, body: ErrorBody | null): ApiError {
  const error = apiErrorFrom(response, body);
  if (isEnrollmentRefusal(error)) {
    try {
      enrollmentRefusalListener?.();
    } catch {
      // not the caller's problem
    }
  }
  return error;
}

async function accessToken(): Promise<string> {
  const { data } = await supabase.auth.getSession();
  const token = data.session?.access_token;
  if (!token) {
    throw new ApiError(401, "Not signed in.");
  }
  return token;
}

// The one place a JSON-answering request is sent: the token, the API base, the 204 and the
// error envelope. `contentType` is the request body's, which is the only thing the JSON calls and
// the raw-bytes call (`apiFetchBytes`) disagree about.
async function send<T>(path: string, init: RequestInit | undefined, contentType: string): Promise<T> {
  const token = await accessToken();
  const response = await fetch(buildApiUrl(API_BASE, path), {
    ...init,
    headers: {
      "Content-Type": contentType,
      Authorization: `Bearer ${token}`,
      ...init?.headers,
    },
  });

  if (response.status === 204) {
    return undefined as T;
  }

  const body = (await response.json().catch(() => null)) as ErrorBody | null;
  if (!response.ok) {
    throw failedWith(response, body);
  }
  return body as T;
}

export function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  return send<T>(path, init, "application/json");
}

// A POST whose body is raw bytes (a file) rather than JSON, answered in JSON like every other
// call. `contentType` is the caller's to choose and is sent exactly as given: it must come from
// what the bytes are, not from what a browser or a file name says (see lib/resumeImportFile.ts).
export function apiFetchBytes<T>(path: string, body: BodyInit, contentType: string): Promise<T> {
  return send<T>(path, { method: "POST", body }, contentType);
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
    throw failedWith(response, body);
  }
  return response.blob();
}
