import { describe, expect, it, vi } from "vitest";
import {
  CONSENT_STORAGE_KEY,
  CONSENT_VERSION,
  consentDecision,
  consentDecisionFromChange,
  readConsentDecision,
} from "@/lib/consent";

// The single decision point. Everything that gates on the consent flag -- the side
// panel, the content script, the service worker -- reaches its answer through these.

describe("consentDecision", () => {
  const cases: Array<[string, unknown, number, "granted" | "needed"]> = [
    ["nothing stored", undefined, 1, "needed"],
    ["null", null, 1, "needed"],
    ["a bare boolean", true, 1, "needed"],
    ["the string 'yes'", "yes", 1, "needed"],
    ["an empty object", {}, 1, "needed"],
    ["a version that is a string", { version: "1" }, 1, "needed"],
    ["a version that is NaN", { version: Number.NaN }, 1, "needed"],
    ["an array", [{ version: 1 }], 1, "needed"],
    ["an older version", { version: 1 }, 2, "needed"],
    ["a newer version than this build knows", { version: 3 }, 2, "needed"],
    ["version 0", { version: 0 }, 1, "needed"],
    ["exactly the current version", { version: 2 }, 2, "granted"],
    ["the current version with extra fields", { version: 1, agreedAt: "2026-10-06" }, 1, "granted"],
  ];
  it.each(cases)("%s -> %s", (_name, stored, current, expected) => {
    expect(consentDecision(stored, current)).toBe(expected);
  });

  it("defaults to this build's own version", () => {
    expect(consentDecision({ version: CONSENT_VERSION })).toBe("granted");
    expect(consentDecision({ version: CONSENT_VERSION + 1 })).toBe("needed");
    expect(consentDecision({ version: CONSENT_VERSION - 1 })).toBe("needed");
  });
});

describe("readConsentDecision", () => {
  const storageHolding = (value: unknown) => ({
    get: vi.fn(async (key: string) => (value === undefined ? {} : { [key]: value })),
  });

  it("asks storage for the consent key and applies the same decision", async () => {
    const storage = storageHolding({ version: CONSENT_VERSION });
    expect(await readConsentDecision(storage)).toBe("granted");
    expect(storage.get).toHaveBeenCalledWith(CONSENT_STORAGE_KEY);
  });

  it("is 'needed' for a missing flag, a stale version and a malformed value", async () => {
    expect(await readConsentDecision(storageHolding(undefined))).toBe("needed");
    expect(await readConsentDecision(storageHolding({ version: CONSENT_VERSION - 1 }))).toBe("needed");
    expect(await readConsentDecision(storageHolding("yes"))).toBe("needed");
  });

  it("a version bump closes the gate for the same stored flag", async () => {
    const storage = storageHolding({ version: 1 });
    expect(await readConsentDecision(storage, 1)).toBe("granted");
    expect(await readConsentDecision(storage, 2)).toBe("needed");
  });

  it("fails closed when the read throws", async () => {
    const storage = {
      get: vi.fn(async () => {
        throw new Error("storage unavailable");
      }),
    };
    expect(await readConsentDecision(storage)).toBe("needed");
  });

  it("fails closed when there is no chrome.storage at all", async () => {
    vi.stubGlobal("chrome", undefined);
    try {
      expect(await readConsentDecision()).toBe("needed");
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it("reads the live value every time, never a remembered one", async () => {
    let stored: unknown = { version: CONSENT_VERSION };
    const storage = { get: async (key: string) => ({ [key]: stored }) };
    expect(await readConsentDecision(storage)).toBe("granted");
    stored = undefined;
    expect(await readConsentDecision(storage)).toBe("needed");
    stored = { version: CONSENT_VERSION };
    expect(await readConsentDecision(storage)).toBe("granted");
  });
});

describe("consentDecisionFromChange", () => {
  it("reports the new decision when the flag itself changed in the local area", () => {
    expect(consentDecisionFromChange({ [CONSENT_STORAGE_KEY]: { newValue: { version: CONSENT_VERSION } } }, "local")).toBe(
      "granted",
    );
    expect(consentDecisionFromChange({ [CONSENT_STORAGE_KEY]: { newValue: undefined } }, "local")).toBe("needed");
    expect(
      consentDecisionFromChange({ [CONSENT_STORAGE_KEY]: { newValue: { version: CONSENT_VERSION + 1 } } }, "local"),
    ).toBe("needed");
  });

  it("says nothing about other keys or other storage areas", () => {
    expect(consentDecisionFromChange({ "fieldMapVersion:lever": { newValue: 3 } }, "local")).toBeNull();
    expect(consentDecisionFromChange({ [CONSENT_STORAGE_KEY]: { newValue: { version: CONSENT_VERSION } } }, "session")).toBeNull();
  });
});
