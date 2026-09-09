import { beforeAll, describe, expect, it } from "vitest";
import { KNOWN_PUBLIC_KEYS, verifyAndParseFieldMap } from "@/lib/ats-field-map";
import type { SignedFieldMapResponse } from "@/lib/ats-field-map";

// D4 (browser-extension.md) -- fail-closed signature verification. These
// tests use a real, freshly-generated (never persisted) Ed25519 keypair
// per run, mirroring n8n's own real `miniapp_sign_initdata.py --tampered`
// precedent for proving a verifier actually fails closed under real
// tamper attempts, not just asserting it should. `KNOWN_PUBLIC_KEYS` is
// the same module-level record `background.ts` reads at runtime --
// mutated directly here rather than mocked, since it's a plain exported
// object.

const TEST_KEY_ID = "test-key-2026-09";

function bytesToBase64(bytes: Uint8Array): string {
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary);
}

let privateKey: CryptoKey;

beforeAll(async () => {
  const keyPair = await crypto.subtle.generateKey({ name: "Ed25519" }, true, ["sign", "verify"]);
  privateKey = keyPair.privateKey;
  const publicKeyBytes = new Uint8Array(await crypto.subtle.exportKey("raw", keyPair.publicKey));
  KNOWN_PUBLIC_KEYS[TEST_KEY_ID] = bytesToBase64(publicKeyBytes);
});

async function sign(payloadCanonical: string): Promise<string> {
  const signature = await crypto.subtle.sign(
    { name: "Ed25519" },
    privateKey,
    new TextEncoder().encode(payloadCanonical),
  );
  return bytesToBase64(new Uint8Array(signature));
}

function validPayload(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    ats_type: "lever",
    version: 1,
    schema: "ats-field-map/v1",
    standard_fields: [
      {
        field: "linkedin",
        selector: 'input[name="urls[LinkedIn]"]',
        strategy: "direct",
        profileFields: ["linkedin"],
      },
    ],
    custom_question_prefix: "cards[",
    label_wrapper_selector: ".application-field",
    label_selector: ".application-label",
    cover_letter_label_pattern: "cover letter",
    ...overrides,
  };
}

async function signedResponse(
  payload: Record<string, unknown> = validPayload(),
  responseOverrides: Partial<SignedFieldMapResponse> = {},
): Promise<SignedFieldMapResponse> {
  const payloadCanonical = JSON.stringify(payload);
  return {
    ats_type: payload.ats_type as string,
    version: payload.version as number,
    schema: payload.schema as string,
    payload_canonical: payloadCanonical,
    signature_b64: await sign(payloadCanonical),
    signing_key_id: TEST_KEY_ID,
    ...responseOverrides,
  };
}

describe("verifyAndParseFieldMap", () => {
  it("accepts a genuinely valid, correctly signed payload", async () => {
    const response = await signedResponse();
    const map = await verifyAndParseFieldMap(response);
    expect(map).not.toBeNull();
    expect(map?.custom_question_prefix).toBe("cards[");
  });

  it("rejects a tampered payload -- signature no longer matches the (changed) bytes", async () => {
    const response = await signedResponse();
    const tampered: SignedFieldMapResponse = {
      ...response,
      payload_canonical: response.payload_canonical.replace("cards[", "evil["),
    };
    expect(await verifyAndParseFieldMap(tampered)).toBeNull();
  });

  it("rejects a tampered signature", async () => {
    const response = await signedResponse();
    const sigBytes = Uint8Array.from(atob(response.signature_b64), (c) => c.charCodeAt(0));
    sigBytes[0] = (sigBytes[0] ?? 0) ^ 0xff;
    const tampered: SignedFieldMapResponse = {
      ...response,
      signature_b64: btoa(String.fromCharCode(...sigBytes)),
    };
    expect(await verifyAndParseFieldMap(tampered)).toBeNull();
  });

  it("rejects an unrecognized signing_key_id without even attempting to verify", async () => {
    const response = await signedResponse(validPayload(), { signing_key_id: "some-unregistered-key" });
    expect(await verifyAndParseFieldMap(response)).toBeNull();
  });

  it("rejects an unsupported schema", async () => {
    const payload = validPayload({ schema: "ats-field-map/v2" });
    const response = await signedResponse(payload, { schema: "ats-field-map/v2" });
    expect(await verifyAndParseFieldMap(response)).toBeNull();
  });

  it("rejects a payload whose embedded ats_type doesn't match the response metadata -- the binding check against a mislabeled-but-validly-signed row", async () => {
    const response = await signedResponse();
    // A genuinely valid signature over the real payload, but the OUTER
    // (unsigned) response metadata claims a different ats_type than
    // what's actually inside the signed bytes.
    const mislabeled: SignedFieldMapResponse = { ...response, ats_type: "greenhouse" };
    expect(await verifyAndParseFieldMap(mislabeled)).toBeNull();
  });

  it("rejects a payload whose embedded version doesn't match the response metadata", async () => {
    const response = await signedResponse();
    const mislabeled: SignedFieldMapResponse = { ...response, version: 2 };
    expect(await verifyAndParseFieldMap(mislabeled)).toBeNull();
  });

  it("rejects a validly-signed payload that isn't JSON at all", async () => {
    const payloadCanonical = "not json";
    const response: SignedFieldMapResponse = {
      ats_type: "lever",
      version: 1,
      schema: "ats-field-map/v1",
      payload_canonical: payloadCanonical,
      signature_b64: await sign(payloadCanonical),
      signing_key_id: TEST_KEY_ID,
    };
    expect(await verifyAndParseFieldMap(response)).toBeNull();
  });

  it("rejects a validly-signed payload missing a required field", async () => {
    const payload = validPayload();
    delete payload.custom_question_prefix;
    const response = await signedResponse(payload);
    expect(await verifyAndParseFieldMap(response)).toBeNull();
  });

  it("rejects a validly-signed payload whose standard_fields entry has an invalid strategy", async () => {
    const payload = validPayload({
      standard_fields: [
        { field: "linkedin", selector: "x", strategy: "eval", profileFields: ["linkedin"] },
      ],
    });
    const response = await signedResponse(payload);
    expect(await verifyAndParseFieldMap(response)).toBeNull();
  });

  it("rejects a validly-signed payload whose cover_letter_label_pattern isn't a valid regex -- a maintainer typo, not an attack, but a signature only proves authenticity, not syntactic validity", async () => {
    const payload = validPayload({ cover_letter_label_pattern: "(unbalanced" });
    const response = await signedResponse(payload);
    expect(await verifyAndParseFieldMap(response)).toBeNull();
  });
});
