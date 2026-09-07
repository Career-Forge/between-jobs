import { getSupabaseClient } from "./supabase";

// Mirrors web/src/lib/api.ts's own shape (same error envelope, same
// Bearer-token attach-per-call pattern) -- the extension talks to the
// exact same FastAPI backend, just without a dev-proxy rewrite to hide
// behind, so it needs the real base URL.

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    message: string,
    public readonly code?: string,
  ) {
    super(message);
  }
}

async function accessToken(): Promise<string> {
  const { data } = await getSupabaseClient().auth.getSession();
  const token = data.session?.access_token;
  if (!token) {
    throw new ApiError(401, "Not signed in.");
  }
  return token;
}

export async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const token = await accessToken();
  const response = await fetch(`${import.meta.env.WXT_API_BASE_URL}${path}`, {
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
    error?: { code?: string; message?: string };
  } | null;
  if (!response.ok) {
    throw new ApiError(
      response.status,
      body?.error?.message ?? `Request failed (${response.status})`,
      body?.error?.code,
    );
  }
  return body as T;
}

export async function apiFetchBlob(path: string): Promise<Blob> {
  const token = await accessToken();
  const response = await fetch(`${import.meta.env.WXT_API_BASE_URL}${path}`, {
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
