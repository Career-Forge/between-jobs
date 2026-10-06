// The error codes the API sends in its envelope (`{"error": {"code": ...}}`),
// mirroring `ErrorCode` in src/between_jobs/api/errors.py. The server list is
// the source of truth: a Python test (tests/test_errors.py) fails when this
// array and that Literal drift apart, so adding a code means editing both.
//
// RATE_LIMITED is the platform's own per-user limit (429, with `retry_after_seconds`
// in the envelope's details and a Retry-After header). It is not
// PROVIDER_RATE_LIMITED, which says an upstream provider throttled the server. One RATE_LIMITED is
// the server's own capacity and not the person's count: every resume-file reader being busy, which
// says so in `details.reason` ("readers_busy", ApiError.reason).
// PAYLOAD_TOO_LARGE is a request body over the size cap (413).
// UNSUPPORTED_MEDIA_TYPE is an uploaded file the route does not read (415): a resume that
// is not a PDF or a DOCX.
// ENROLLMENT_REQUIRED is a 403 from a server that has switched the tester programme on: the
// person has not accepted the current tester agreement, and the costly feature they asked for
// needs that first (the enrollment page is where they do it). api.ts reports one to the shell's
// gate (components/EnrollmentGate.tsx), which asks again where the person stands and puts the
// enrollment page where the feature was; friendlyApiMessage words it for the page that asked.
export const KNOWN_API_ERROR_CODES = [
  "AUTH_REQUIRED",
  "FORBIDDEN",
  "SETUP_REQUIRED",
  "INVALID_INPUT",
  "NOT_FOUND",
  "STALE_REFERENCE",
  "AMBIGUOUS_REFERENCE",
  "PROVIDER_REJECTED",
  "PROVIDER_RATE_LIMITED",
  "PROVIDER_UNAVAILABLE",
  "POLICY_REVIEW_REQUIRED",
  "INSUFFICIENT_EVIDENCE",
  "RUN_CANCELLED",
  "RUN_FAILED",
  "CONFLICT",
  "INTERNAL_ERROR",
  "FEATURE_DISABLED",
  "RATE_LIMITED",
  "PAYLOAD_TOO_LARGE",
  "UNSUPPORTED_MEDIA_TYPE",
  "ENROLLMENT_REQUIRED",
] as const;

export type KnownApiErrorCode = (typeof KNOWN_API_ERROR_CODES)[number];

// A code from a server newer than this bundle is still a string: the union
// documents the codes this app knows, it does not reject the ones it does not.
export type ApiErrorCode = KnownApiErrorCode | (string & Record<never, never>);

export function isKnownApiErrorCode(code: unknown): code is KnownApiErrorCode {
  return typeof code === "string" && (KNOWN_API_ERROR_CODES as readonly string[]).includes(code);
}
