import type { ApiErrorCode } from "./apiErrorCodes";

// How a 429 RATE_LIMITED (the platform's own per-user limit) and a 413
// PAYLOAD_TOO_LARGE reach the person. Pure functions, so every wording and every
// rounding is a test, and the components that show an action's error call
// `friendlyApiMessage` instead of each rewording the same sentence.
//
// The wait is rounded UP, like the server's own wording (describe_wait in
// src/between_jobs/api/rate_limits.py -- keep the two in step): telling someone
// "about 12 minutes" when it is 11.2 is fine, telling them "11" and sending them
// back a few seconds early is a second refusal.

const MINUTE = 60;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;

// "less than a minute", "about 12 minutes", "about an hour", "about 3 hours",
// "about 2 days".
export function humanizeWait(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < MINUTE) {
    return "less than a minute";
  }
  if (seconds < HOUR) {
    const minutes = Math.ceil(seconds / MINUTE);
    if (minutes < 60) {
      return minutes === 1 ? "about a minute" : `about ${minutes} minutes`;
    }
    return "about an hour"; // 59 minutes and a bit rounds up to the hour, not "60 minutes"
  }
  if (seconds < DAY) {
    const hours = Math.ceil(seconds / HOUR);
    if (hours < 24) {
      return hours === 1 ? "about an hour" : `about ${hours} hours`;
    }
    return "about a day";
  }
  const days = Math.ceil(seconds / DAY);
  return days === 1 ? "about a day" : `about ${days} days`;
}

// The wait an error asks for, in whole seconds, or undefined when it names none.
// The envelope's `details.retry_after_seconds` is read first; the Retry-After
// header (delay-seconds form only -- the HTTP-date form is never sent) is the
// fallback for a reply whose body could not be read.
export function parseRetryAfterSeconds(
  details: unknown,
  headerValue: string | null | undefined,
): number | undefined {
  if (typeof details === "object" && details !== null) {
    const fromBody = (details as { retry_after_seconds?: unknown }).retry_after_seconds;
    if (typeof fromBody === "number" && Number.isFinite(fromBody) && fromBody >= 1) {
      return Math.ceil(fromBody);
    }
  }
  if (typeof headerValue === "string" && /^\d+$/.test(headerValue.trim())) {
    const fromHeader = Number(headerValue.trim());
    if (fromHeader >= 1) {
      return fromHeader;
    }
  }
  return undefined;
}

export function rateLimitedMessage(retryAfterSeconds: number | undefined): string {
  const wait =
    retryAfterSeconds === undefined ? "a little while" : humanizeWait(retryAfterSeconds);
  return `You are doing that too often. Try again in ${wait}.`;
}

export const PAYLOAD_TOO_LARGE_MESSAGE =
  "That is too large to send. Shorten it and try again.";

// What `ApiError` (api.ts) carries, read structurally so this module needs no
// import of it (which would pull in the Supabase client).
interface ApiErrorLike {
  code?: ApiErrorCode;
  status?: number;
  retryAfterSeconds?: number;
  message: string;
}

function looksLikeApiError(error: unknown): error is Error & ApiErrorLike {
  return error instanceof Error && ("code" in error || "status" in error);
}

// The text to show for a failed action. A rate limit and an oversized request
// get their own plain wording -- by code, or, for a reply that is not the API's
// envelope (an edge proxy's own 429 or 413 has no code), by status; anything
// else keeps the server's own message (an Error with no code, such as a failed
// fetch, keeps its message too), and a thrown value that is not an Error at all
// gets `fallback`. PROVIDER_RATE_LIMITED (an upstream provider throttled the
// server) is a different thing and keeps its own message.
export function friendlyApiMessage(error: unknown, fallback: string): string {
  if (looksLikeApiError(error)) {
    if (error.code === "RATE_LIMITED" || (error.code === undefined && error.status === 429)) {
      return rateLimitedMessage(error.retryAfterSeconds);
    }
    if (error.code === "PAYLOAD_TOO_LARGE" || (error.code === undefined && error.status === 413)) {
      return PAYLOAD_TOO_LARGE_MESSAGE;
    }
  }
  return error instanceof Error ? error.message : fallback;
}
