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
    expect(map?.ats_type === "lever" ? map.custom_question_prefix : undefined).toBe("cards[");
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

// E4/E5 -- Greenhouse's and Ashby's own map shape is deliberately bare
// (just `standard_fields`, per each engine's own top-of-file note: their
// custom-question extraction is self-contained and never published as a
// signed map in this repo). These tests exist so the verification
// plumbing this task generalizes actually works correctly for BOTH new
// ats_types, even though no real map is ever curated/signed here.
describe("verifyAndParseFieldMap (Greenhouse/Ashby)", () => {
  function bareValidPayload(atsType: "greenhouse" | "ashby", overrides: Record<string, unknown> = {}) {
    return {
      ats_type: atsType,
      version: 1,
      schema: "ats-field-map/v1",
      standard_fields: [
        { field: "email", selector: "#email", strategy: "direct", profileFields: ["email"] },
      ],
      ...overrides,
    };
  }

  it("accepts a genuinely valid, correctly signed bare Greenhouse map", async () => {
    const response = await signedResponse(bareValidPayload("greenhouse"));
    const map = await verifyAndParseFieldMap(response);
    expect(map).not.toBeNull();
    expect(map?.ats_type).toBe("greenhouse");
  });

  it("accepts a genuinely valid, correctly signed bare Ashby map", async () => {
    const response = await signedResponse(bareValidPayload("ashby"));
    const map = await verifyAndParseFieldMap(response);
    expect(map).not.toBeNull();
    expect(map?.ats_type).toBe("ashby");
  });

  it("accepts the new firstNameWord/lastNameWord strategies E4 introduced for Greenhouse's split name fields", async () => {
    const payload = bareValidPayload("greenhouse", {
      standard_fields: [
        { field: "first_name", selector: "#first_name", strategy: "firstNameWord", profileFields: ["name"] },
        { field: "last_name", selector: "#last_name", strategy: "lastNameWord", profileFields: ["name"] },
      ],
    });
    const response = await signedResponse(payload);
    expect(await verifyAndParseFieldMap(response)).not.toBeNull();
  });

  it("rejects a bare Greenhouse/Ashby payload with an invalid standard_fields entry, same as Lever's own validation", async () => {
    const payload = bareValidPayload("greenhouse", {
      standard_fields: [{ field: "x", selector: "x", strategy: "eval", profileFields: [] }],
    });
    const response = await signedResponse(payload);
    expect(await verifyAndParseFieldMap(response)).toBeNull();
  });

  it("rejects a payload whose embedded ats_type doesn't match the response metadata, even between two ats_types that both use the bare shape", async () => {
    const response = await signedResponse(bareValidPayload("greenhouse"));
    const mislabeled = { ...response, ats_type: "ashby" };
    expect(await verifyAndParseFieldMap(mislabeled)).toBeNull();
  });

  it("rejects an ats_type this build doesn't recognize at all", async () => {
    const payload = bareValidPayload("greenhouse", { ats_type: "workday" });
    const payloadCanonical = JSON.stringify(payload);
    const response: SignedFieldMapResponse = {
      ats_type: "workday",
      version: 1,
      schema: "ats-field-map/v1",
      payload_canonical: payloadCanonical,
      signature_b64: await sign(payloadCanonical),
      signing_key_id: TEST_KEY_ID,
    };
    expect(await verifyAndParseFieldMap(response)).toBeNull();
  });
});

// E6 -- two gaps in what a validly-signed payload was allowed to say.
describe("verifyAndParseFieldMap -- E6 hardening", () => {
  it("rejects a signed, Lever-SHAPED payload for an ats_type this build has never heard of (it used to fall through to the Lever branch and come back as a 'valid' map)", async () => {
    const response = await signedResponse(validPayload({ ats_type: "workday" }));
    expect(response.ats_type).toBe("workday");
    expect(await verifyAndParseFieldMap(response)).toBeNull();
  });

  // The publishing script writes an integer starting at 1; the background
  // ratchet compares versions numerically, so a non-integer must never
  // reach storage. A signed string version is the worst case: '10' < 5 is
  // false, so it would be accepted, stored as a string, and then read back
  // as "no floor" -- silently resetting the rollback protection.
  for (const [name, version] of [
    ["a float", 1.5],
    ["a negative", -3],
    ["zero", 0],
    ["a numeric string", "10"],
    ["a boolean", true],
    ["a beyond-safe-integer value", 1e21],
  ] as const) {
    it(`rejects a validly-signed payload whose version is ${name}`, async () => {
      const response = await signedResponse(validPayload({ version }), {
        version: version as unknown as number,
      });
      expect(await verifyAndParseFieldMap(response)).toBeNull();
    });
  }

  it("still accepts an ordinary positive-integer version", async () => {
    const response = await signedResponse(validPayload({ version: 7 }));
    expect((await verifyAndParseFieldMap(response))?.version).toBe(7);
  });
});
