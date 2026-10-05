import { describe, expect, it } from "vitest";
import { KNOWN_API_ERROR_CODES, isKnownApiErrorCode } from "./apiErrorCodes";

describe("KNOWN_API_ERROR_CODES", () => {
  it("includes the platform's own limit and size codes, distinct from the provider's", () => {
    expect(KNOWN_API_ERROR_CODES).toContain("RATE_LIMITED");
    expect(KNOWN_API_ERROR_CODES).toContain("PAYLOAD_TOO_LARGE");
    expect(KNOWN_API_ERROR_CODES).toContain("PROVIDER_RATE_LIMITED");
  });

  it("lists each code once", () => {
    expect(new Set(KNOWN_API_ERROR_CODES).size).toBe(KNOWN_API_ERROR_CODES.length);
  });

  it("recognises a known code and nothing else", () => {
    expect(isKnownApiErrorCode("RATE_LIMITED")).toBe(true);
    expect(isKnownApiErrorCode("NOT_A_CODE")).toBe(false);
    expect(isKnownApiErrorCode(undefined)).toBe(false);
    expect(isKnownApiErrorCode(429)).toBe(false);
  });
});
