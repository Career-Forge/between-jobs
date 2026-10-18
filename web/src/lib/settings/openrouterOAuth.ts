// Connecting OpenRouter in one click: OAuth with PKCE, browser half.
// https://openrouter.ai/docs/guides/overview/auth/oauth
//
//   1. start     make a random verifier and its S256 challenge, remember the verifier and a random
//                state for ten minutes, and send the person to openrouter.ai/auth
//   2. return    OpenRouter sends them back to this site with ?code=...&state=...
//   3. exchange  the code and the verifier go to our API (POST /credentials/openrouter/exchange),
//                which trades them for a key at OpenRouter and stores it encrypted
//
// WHERE THE SECRETS LIVE. The verifier is a secret until the exchange is done, the code until it
// is used. Neither is ever put in a URL other than the redirect parameters the protocol itself
// requires (the challenge and the state go out; the code and the state come back), neither goes to
// localStorage or a log, and the browser never sees the key at all: the API does the exchange.
// The verifier and state wait in sessionStorage (this tab only, gone with it) with a ten-minute
// expiry, the same as the life of the code OpenRouter issues. Once read they are removed, and the
// code and verifier then live only in this module's memory until the exchange has answered.
//
// THE ORDER ON RETURN is fixed and tested: the OAuth parameters come off the address first
// (history.replaceState, before the page or the router has read them), then the stored state is
// read and removed, then the two are compared, and only then is anything sent.
//
// The server holds no state for any of this (src/between_jobs/api/openrouter_oauth.py): OpenRouter
// is what checks that the verifier fits the code, that the code is unused and that it is under ten
// minutes old.

import { normalizePath } from "../publicRoutes";
import { SETTINGS_PATH } from "../settingsPaths";

export const OPENROUTER_AUTH_URL = "https://openrouter.ai/auth";
export const OAUTH_STORAGE_KEY = "between-jobs:openrouter-oauth";
export const OAUTH_TTL_MS = 10 * 60 * 1000;
// What OpenRouter labels the key it creates, so the person can tell it apart in their key list.
export const OAUTH_KEY_LABEL = "Between Jobs";
// The query parameters this flow owns. Anything else on the address (the ?capability=... a setup
// link adds) is left exactly as it was.
export const OAUTH_PARAMS = ["code", "state", "error", "error_description"] as const;

// RFC 7636 asks for 43 to 128 characters of unreserved characters; 48 random bytes make 64 of them.
const VERIFIER_BYTES = 48;
const STATE_BYTES = 32;

// ── the PKCE values ───────────────────────────────────────────────────────

export interface CryptoLike {
  getRandomValues<T extends ArrayBufferView>(array: T): T;
  subtle?: { digest(algorithm: string, data: BufferSource): Promise<ArrayBuffer> };
}

// base64url without padding (RFC 4648 section 5).
export function base64Url(bytes: Uint8Array): string {
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function randomToken(crypto: CryptoLike, byteLength: number): string {
  return base64Url(crypto.getRandomValues(new Uint8Array(byteLength)));
}

export function makeVerifier(crypto: CryptoLike): string {
  return randomToken(crypto, VERIFIER_BYTES);
}

export function makeState(crypto: CryptoLike): string {
  return randomToken(crypto, STATE_BYTES);
}

// BASE64URL(SHA256(ASCII(verifier))), the S256 method of RFC 7636 section 4.2.
export async function challengeFor(verifier: string, crypto: CryptoLike): Promise<string> {
  if (crypto.subtle === undefined) throw new Error("no Web Crypto digest");
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier));
  return base64Url(new Uint8Array(digest));
}

// ── what waits in sessionStorage ──────────────────────────────────────────

export interface PendingConnect {
  verifier: string;
  state: string;
  /** Epoch milliseconds. */
  expiresAt: number;
}

export interface SessionStorageLike {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
}

// The tab's sessionStorage, or null when the browser has none or reaching for it throws (blocked
// site data in some browsers). Never localStorage: this must not outlive the tab.
export function tabStorage(get: () => SessionStorageLike | null | undefined = () => window.sessionStorage): SessionStorageLike | null {
  try {
    return get() ?? null;
  } catch {
    return null;
  }
}

function isPending(value: unknown): value is PendingConnect {
  if (typeof value !== "object" || value === null) return false;
  const pending = value as Partial<PendingConnect>;
  return (
    typeof pending.verifier === "string" &&
    pending.verifier !== "" &&
    typeof pending.state === "string" &&
    pending.state !== "" &&
    typeof pending.expiresAt === "number" &&
    Number.isFinite(pending.expiresAt)
  );
}

// False when it could not be stored: then the flow must not start, because the return could never
// be checked.
export function savePending(storage: SessionStorageLike | null, pending: PendingConnect): boolean {
  try {
    if (storage === null) return false;
    storage.setItem(OAUTH_STORAGE_KEY, JSON.stringify(pending));
    return storage.getItem(OAUTH_STORAGE_KEY) !== null;
  } catch {
    return false;
  }
}

export type TakenPending = { kind: "found"; pending: PendingConnect } | { kind: "missing" } | { kind: "expired" };

// Reads what a start left and removes it either way: a verifier is single use, and one that is
// expired or unreadable is no use to anyone.
export function takePending(storage: SessionStorageLike | null, now: number): TakenPending {
  let raw: string | null = null;
  try {
    raw = storage?.getItem(OAUTH_STORAGE_KEY) ?? null;
    storage?.removeItem(OAUTH_STORAGE_KEY);
  } catch {
    return { kind: "missing" };
  }
  if (raw === null) return { kind: "missing" };
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return { kind: "missing" };
  }
  if (!isPending(parsed)) return { kind: "missing" };
  return now >= parsed.expiresAt ? { kind: "expired" } : { kind: "found", pending: parsed };
}

export function clearPending(storage: SessionStorageLike | null): void {
  try {
    storage?.removeItem(OAUTH_STORAGE_KEY);
  } catch {
    // Nothing useful to do.
  }
}

// ── step 1: start ─────────────────────────────────────────────────────────

export type StartResult =
  | { kind: "go"; url: string }
  // The browser cannot do the secure part (Web Crypto needs https or localhost) or cannot keep the
  // verifier for the return. The person pastes a key instead.
  | { kind: "unsupported" };

export interface StartDeps {
  crypto: CryptoLike | undefined;
  storage: SessionStorageLike | null;
  /** This site's Settings address, which OpenRouter sends the person back to. */
  callbackUrl: string;
  now: number;
}

export async function startConnect(deps: StartDeps): Promise<StartResult> {
  const { crypto, storage } = deps;
  if (crypto === undefined || crypto.subtle === undefined) return { kind: "unsupported" };
  const verifier = makeVerifier(crypto);
  const state = makeState(crypto);
  let challenge: string;
  try {
    challenge = await challengeFor(verifier, crypto);
  } catch {
    return { kind: "unsupported" };
  }
  if (!savePending(storage, { verifier, state, expiresAt: deps.now + OAUTH_TTL_MS })) return { kind: "unsupported" };
  const params = new URLSearchParams({
    callback_url: deps.callbackUrl,
    code_challenge: challenge,
    code_challenge_method: "S256",
    state,
    key_label: OAUTH_KEY_LABEL,
  });
  return { kind: "go", url: `${OPENROUTER_AUTH_URL}?${params.toString()}` };
}

// ── step 2: the return ────────────────────────────────────────────────────

export interface ReturnParams {
  code: string | null;
  state: string | null;
  error: string | null;
}

export interface ExtractedReturn {
  params: ReturnParams;
  /** The query string with this flow's parameters removed, "?" included, or "" when none is left. */
  remainingSearch: string;
}

// The flow's parameters out of an address's query, or null when it carries none of them.
export function extractReturn(search: string): ExtractedReturn | null {
  const query = new URLSearchParams(search);
  if (!OAUTH_PARAMS.some((name) => query.has(name))) return null;
  const read = (name: string) => {
    const value = query.get(name);
    return value === null || value === "" ? null : value;
  };
  const params: ReturnParams = {
    code: read("code"),
    state: read("state"),
    error: read("error") ?? read("error_description"),
  };
  for (const name of OAUTH_PARAMS) query.delete(name);
  const rest = query.toString();
  return { params, remainingSearch: rest === "" ? "" : `?${rest}` };
}

export type ReturnFailure =
  // OpenRouter sent the person back without a code: they pressed Deny, or the page errored.
  | "denied"
  // A code came back that this tab did not ask for, or for a different start.
  | "state_mismatch"
  // The ten minutes ran out before they came back.
  | "expired"
  // Nothing was waiting: another tab, a cleared session, or a link not made by this flow.
  | "no_pending"
  // The state matched but there was no code.
  | "no_code";

export type ReturnOutcome =
  | { kind: "ready"; code: string; verifier: string }
  | { kind: "failed"; reason: ReturnFailure };

// Compares what came back with what was kept. The stored values are removed whatever the answer.
export function resolveReturn(params: ReturnParams, storage: SessionStorageLike | null, now: number): ReturnOutcome {
  const taken = takePending(storage, now);
  if (taken.kind === "expired") return { kind: "failed", reason: "expired" };
  if (taken.kind === "missing") return { kind: "failed", reason: params.code === null && params.error !== null ? "denied" : "no_pending" };
  if (params.state === null || params.state !== taken.pending.state) return { kind: "failed", reason: "state_mismatch" };
  if (params.code === null) return { kind: "failed", reason: params.error !== null ? "denied" : "no_code" };
  return { kind: "ready", code: params.code, verifier: taken.pending.verifier };
}

export interface BrowserLike {
  location: { search: string; pathname: string; hash: string };
  history: { state: unknown; replaceState(data: unknown, unused: string, url?: string | URL | null): void };
  sessionStorage?: SessionStorageLike;
}

// Run once, in main.tsx, before the router or any page has read the address. Returns what the page
// should do, or null when this visit is not a return from OpenRouter.
export function captureReturn(browser: BrowserLike, now: number, storage: SessionStorageLike | null): ReturnOutcome | null {
  // Only the Settings address is a return from this flow. Another page's own ?error=... (a dead
  // password-reset link lands on "/" with one) is not ours to take off the address.
  if (normalizePath(browser.location.pathname) !== SETTINGS_PATH) return null;
  const extracted = extractReturn(browser.location.search);
  if (extracted === null) return null;
  // First: take the parameters off the address, so nothing that renders next can see, copy or share them.
  const { pathname, hash } = browser.location;
  browser.history.replaceState(browser.history.state, "", `${pathname}${extracted.remainingSearch}${hash}`);
  return resolveReturn(extracted.params, storage, now);
}

// ── step 3: the exchange, as a small store the page subscribes to ─────────

export type ConnectFailure =
  | { kind: ReturnFailure }
  // OpenRouter, or the API, refused the code. Asking again with the same one cannot work.
  | { kind: "rejected"; error: unknown }
  // The exchange did not complete and may succeed if repeated: the network, a busy provider.
  | { kind: "retryable"; error: unknown };

export type ConnectState<T> =
  | { phase: "idle" }
  | { phase: "exchanging" }
  | { phase: "connected"; credential: T }
  | { phase: "failed"; failure: ConnectFailure };

export type Exchange<T> = (code: string, verifier: string) => Promise<T>;

export interface ConnectStore<T> {
  /** Hand it the outcome of `captureReturn`. */
  receive(outcome: ReturnOutcome | null): void;
  /** Runs the exchange if one is waiting. Safe to call twice (React runs effects twice in development). */
  begin(exchange: Exchange<T>, isRetryable: (error: unknown) => boolean): void;
  /** Runs it again after a retryable failure, from memory. */
  retry(): void;
  /** The person has seen the result. */
  acknowledge(): void;
  getSnapshot(): ConnectState<T>;
  subscribe(listener: () => void): () => void;
}

export function createConnectStore<T>(): ConnectStore<T> {
  let state: ConnectState<T> = { phase: "idle" };
  // Held in memory only, and dropped as soon as the exchange is over.
  let waiting: { code: string; verifier: string } | null = null;
  let lastExchange: Exchange<T> | null = null;
  let lastRetryable: ((error: unknown) => boolean) | null = null;
  const listeners = new Set<() => void>();

  const set = (next: ConnectState<T>) => {
    state = next;
    listeners.forEach((listener) => listener());
  };

  function run(exchange: Exchange<T>, isRetryable: (error: unknown) => boolean): void {
    if (waiting === null) return;
    const { code, verifier } = waiting;
    lastExchange = exchange;
    lastRetryable = isRetryable;
    set({ phase: "exchanging" });
    exchange(code, verifier).then(
      (credential) => {
        waiting = null;
        set({ phase: "connected", credential });
      },
      (error: unknown) => {
        if (isRetryable(error)) {
          set({ phase: "failed", failure: { kind: "retryable", error } });
        } else {
          waiting = null;
          set({ phase: "failed", failure: { kind: "rejected", error } });
        }
      },
    );
  }

  return {
    receive(outcome) {
      if (outcome === null) return;
      if (outcome.kind === "failed") {
        waiting = null;
        set({ phase: "failed", failure: { kind: outcome.reason } });
        return;
      }
      waiting = { code: outcome.code, verifier: outcome.verifier };
      set({ phase: "idle" });
    },
    begin(exchange, isRetryable) {
      if (state.phase !== "idle") return;
      run(exchange, isRetryable);
    },
    retry() {
      if (state.phase !== "failed" || state.failure.kind !== "retryable" || lastExchange === null || lastRetryable === null) return;
      run(lastExchange, lastRetryable);
    },
    acknowledge() {
      waiting = null;
      if (state.phase !== "exchanging") set({ phase: "idle" });
    },
    getSnapshot: () => state,
    subscribe(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
  };
}

// The one store the app uses. main.tsx feeds it; the Settings page reads it.
export const connectStore: ConnectStore<unknown> = createConnectStore<unknown>();
