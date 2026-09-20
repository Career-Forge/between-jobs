import { supabase } from "./supabase";

// Thin fetch wrapper for the spine. Attaches the user's Supabase access
// token (verified server-side against JWKS -- auth.py) and prefixes /api,
// which the Vite dev proxy rewrites to the spine's root.
//
// The spine's error shape is Proposal Appendix B's structured contract
// (Sprint 2.6a): `{"error": {code, message, ...}}`. `code` is exposed
// here (not just `message`) so callers can branch on it later --
// SETUP_REQUIRED vs. NOT_FOUND mean different UI, not just different text.

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    message: string,
    public readonly code?: string,
    // The envelope's own `retryable` flag (errors.py): whether asking again can
    // change the outcome. Undefined when the reply carried none.
    public readonly retryable?: boolean,
  ) {
    super(message);
  }
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
  const response = await fetch(`/api${path}`, {
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

  const body = (await response.json().catch(() => null)) as {
    error?: { code?: string; message?: string; retryable?: boolean };
  } | null;
  if (!response.ok) {
    throw new ApiError(
      response.status,
      body?.error?.message ?? `Request failed (${response.status})`,
      body?.error?.code,
      typeof body?.error?.retryable === "boolean" ? body.error.retryable : undefined,
    );
  }
  return body as T;
}

// For binary responses (currently just the PDF download) -- `apiFetch`
// above always calls `response.json()`, which can't handle a PDF body.
// Errors still come back as the same JSON envelope, so those are parsed
// the same way `apiFetch` does.
export async function apiFetchBlob(path: string): Promise<Blob> {
  const token = await accessToken();
  const response = await fetch(`/api${path}`, {
    headers: { Authorization: `Bearer ${token}` },
  });

  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as {
      error?: { code?: string; message?: string };
    } | null;
    throw new ApiError(
      response.status,
      body?.error?.message ?? `Request failed (${response.status})`,
      body?.error?.code,
    );
  }
  return response.blob();
}
