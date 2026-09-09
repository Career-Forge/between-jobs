/**
 * browser-extension.md E3c -- the genuinely ATS-idiosyncratic part of a
 * field map (never the generic name/email/phone/resume defaults, which
 * ship open-source and unsigned directly in lib/lever.ts's
 * `GENERIC_FIELD_DEFAULTS`, independent of this file entirely). Per this
 * project's own CLAUDE.md ("Hosted-service internals... ATS selector
 * maps... belong to the hosted services, not this repo"), the actual
 * curated content never lands in this file or anywhere else in this
 * repo -- only this verification code does. The real data is signed
 * offline by scripts/sign_and_publish_ats_field_map.py and fetched at
 * runtime from GET /extension/field-maps/{ats_type}.
 *
 * D4 (browser-extension.md): signature-verification failure is
 * FAIL CLOSED -- `verifyAndParseFieldMap` returns `null` for every
 * failure mode (network/parse error upstream, unrecognized signing key,
 * unsupported schema, a bad signature, or a structurally invalid
 * payload) and callers must treat `null` as "refuse to use this ATS's
 * curated data," never fall back to a cached or bundled copy. There is
 * deliberately no such fallback anywhere in this codebase for this data
 * to fall back TO.
 */

export type FieldStrategy = "direct" | "fallback" | "joinNonEmpty";

/** One declarative fill behavior -- NOT a closure. `STANDARD_FIELDS`'
 * pre-E3c `getValue` functions couldn't ship as signed JSON (functions
 * aren't data), so this vocabulary reproduces the same three behaviors
 * (a plain field read, a first-non-empty-of-several fallback, and a
 * joined multi-field composite) without ever transmitting or `eval`ing
 * code -- satisfying D4's "only signed data" requirement literally. */
export interface StandardFieldSpec {
  field: string;
  selector: string;
  strategy: FieldStrategy;
  /** Dotted paths into ExtensionPersonalInfo, e.g. "location.city". */
  profileFields: string[];
  /** Only meaningful for "joinNonEmpty"; defaults to ", " if omitted. */
  separator?: string;
}

const SUPPORTED_SCHEMA = "ats-field-map/v1";

/** The verified, parsed shape this extension knows how to consume.
 * `standard_fields` here carries ONLY the Lever-idiosyncratic entries
 * (location, LinkedIn, portfolio-or-GitHub) -- the generic ones are
 * merged in separately by the caller from `GENERIC_FIELD_DEFAULTS`. */
export interface LeverFieldMap {
  ats_type: string;
  version: number;
  schema: string;
  standard_fields: StandardFieldSpec[];
  custom_question_prefix: string;
  label_wrapper_selector: string;
  label_selector: string;
  cover_letter_label_pattern: string;
}

/** The raw, not-yet-verified shape GET /extension/field-maps/{ats_type}
 * returns. `payload_canonical` is the exact byte-for-byte string the
 * backend signed -- verified and JSON.parse'd only after the signature
 * check passes, never re-serialized by this extension or the backend
 * first (that would risk silently invalidating the signature via a
 * JSON key-order/whitespace mismatch). */
export interface SignedFieldMapResponse {
  ats_type: string;
  version: number;
  schema: string;
  payload_canonical: string;
  signature_b64: string;
  signing_key_id: string;
}

/** Ed25519 public keys, 32 raw bytes each, base64-encoded -- no secrecy
 * requirement on this half of the keypair, it's meant to be committed.
 * The matching private key exists ONLY in a maintainer's own offline
 * signing environment (scripts/sign_and_publish_ats_field_map.py),
 * never in this repo, never in the running backend. Empty until a real
 * keypair is generated and a map is published: until then every fetch
 * fails closed by construction (no `signing_key_id` will ever match),
 * which is the correct, honest state for a repo that has no real
 * published map yet, rather than shipping a placeholder key nothing
 * signs against. */
export const KNOWN_PUBLIC_KEYS: Record<string, string> = {};

function base64ToBytes(b64: string): Uint8Array<ArrayBuffer> {
  const binary = atob(b64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return bytes;
}

function isStandardFieldSpec(value: unknown): value is StandardFieldSpec {
  if (typeof value !== "object" || value === null) return false;
  const v = value as Record<string, unknown>;
  return (
    typeof v.field === "string" &&
    typeof v.selector === "string" &&
    (v.strategy === "direct" || v.strategy === "fallback" || v.strategy === "joinNonEmpty") &&
    Array.isArray(v.profileFields) &&
    v.profileFields.every((f) => typeof f === "string") &&
    (v.separator === undefined || typeof v.separator === "string")
  );
}

/** Structural validation AFTER the signature already passed -- a valid
 * signature proves authenticity (this is genuinely what was signed), not
 * that this build understands every field in it. Also re-checks
 * `ats_type`/`version`/`schema` embedded in the signed payload against
 * the same fields on the outer (unsigned) HTTP response metadata: the
 * signing script binds these INSIDE what it signs specifically so a
 * compromised intermediary can't serve an old, validly-signed payload
 * mislabeled under a different version/ats_type than the one actually
 * signed -- this is the extension's own half of that same defense-in-
 * depth check, the same "re-validate what the caller claims" precedent
 * `fillCustomTextAnswer` already established for this codebase. */
function parseVerifiedFieldMap(
  payloadCanonical: string,
  response: SignedFieldMapResponse,
): LeverFieldMap | null {
  let parsed: unknown;
  try {
    parsed = JSON.parse(payloadCanonical);
  } catch {
    return null;
  }
  if (typeof parsed !== "object" || parsed === null) return null;
  const p = parsed as Record<string, unknown>;
  if (p.ats_type !== response.ats_type || p.version !== response.version || p.schema !== response.schema) {
    return null;
  }
  if (
    typeof p.custom_question_prefix !== "string" ||
    typeof p.label_wrapper_selector !== "string" ||
    typeof p.label_selector !== "string" ||
    typeof p.cover_letter_label_pattern !== "string" ||
    !Array.isArray(p.standard_fields) ||
    !p.standard_fields.every(isStandardFieldSpec)
  ) {
    return null;
  }
  // Adversarially-confirmed gap: a passed signature proves this string
  // was genuinely signed, not that it compiles as a regex -- `RegExp`
  // construction doesn't need a DOM, so it's checked here rather than
  // left entirely to a try/catch at the actual usage site (lib/lever.ts
  // runs in a content script, which DOES have a DOM, but this earlier,
  // DOM-independent check means a malformed pattern is rejected as part
  // of verification itself -- the same "refuse to use it" outcome as
  // every other verification failure, and independently testable in
  // isolation). CSS-selector syntax (label_wrapper_selector/
  // label_selector/custom_question_prefix/each standard_fields[].selector)
  // has no DOM-independent equivalent to check here -- a service worker
  // has no `document` to validate against -- so those are instead
  // guarded by a try/catch around their actual usage in content.ts,
  // which degrades to the same fail-closed outcome.
  try {
    new RegExp(p.cover_letter_label_pattern, "i");
  } catch {
    return null;
  }
  return parsed as LeverFieldMap;
}

/**
 * The one place a signed field map is verified. Re-imports the public
 * key fresh on every call rather than caching a `CryptoKey` across
 * service-worker wake cycles -- `crypto.subtle.importKey`/`verify` are
 * sub-millisecond, and an MV3 service worker can be killed and restarted
 * at any time regardless, so there's no reliable module-level cache to
 * fight for here in the first place.
 */
export async function verifyAndParseFieldMap(
  response: SignedFieldMapResponse,
): Promise<LeverFieldMap | null> {
  if (response.schema !== SUPPORTED_SCHEMA) return null;
  const publicKeyB64 = KNOWN_PUBLIC_KEYS[response.signing_key_id];
  if (publicKeyB64 === undefined) return null;

  try {
    const key = await crypto.subtle.importKey(
      "raw",
      base64ToBytes(publicKeyB64),
      { name: "Ed25519" },
      false,
      ["verify"],
    );
    const payloadBytes = new TextEncoder().encode(response.payload_canonical);
    const signatureBytes = base64ToBytes(response.signature_b64);
    const valid = await crypto.subtle.verify({ name: "Ed25519" }, key, signatureBytes, payloadBytes);
    if (!valid) return null;
  } catch {
    return null;
  }

  return parseVerifiedFieldMap(response.payload_canonical, response);
}
