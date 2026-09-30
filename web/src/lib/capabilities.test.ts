import { describe, expect, it } from "vitest";
import {
  CAPABILITIES_PATH,
  type CapabilitiesFetcher,
  isTelegramAvailable,
  loadCapabilities,
  parseCapabilities,
} from "./capabilities";

describe("parseCapabilities", () => {
  it("reads the telegram flag", () => {
    expect(parseCapabilities({ telegram: true })).toEqual({ telegram: true });
    expect(parseCapabilities({ telegram: false })).toEqual({ telegram: false });
  });

  it("keeps only the flags it knows", () => {
    expect(parseCapabilities({ telegram: true, somethingNew: 1 })).toEqual({ telegram: true });
  });

  it.each([
    ["null", null],
    ["undefined", undefined],
    ["a string", "true"],
    ["an array", [true]],
    ["an empty object", {}],
    ["a string flag", { telegram: "true" }],
    ["a numeric flag", { telegram: 1 }],
    ["a null flag", { telegram: null }],
  ])("does not guess from %s", (_label, body) => {
    expect(parseCapabilities(body)).toBeNull();
  });
});

describe("loadCapabilities", () => {
  it("asks the capabilities route and returns what it says", async () => {
    const asked: string[] = [];
    const fetcher: CapabilitiesFetcher = async <T>(path: string) => {
      asked.push(path);
      return { telegram: false } as T;
    };
    expect(await loadCapabilities(fetcher)).toEqual({
      kind: "ready",
      capabilities: { telegram: false },
    });
    expect(asked).toEqual([CAPABILITIES_PATH]);
  });

  it("is unavailable when the request fails", async () => {
    const fetcher: CapabilitiesFetcher = async () => {
      throw new Error("network down");
    };
    expect(await loadCapabilities(fetcher)).toEqual({ kind: "unavailable" });
  });

  it("is unavailable when the answer is not the expected shape", async () => {
    const fetcher: CapabilitiesFetcher = async <T>() => ({ telegram: "yes" }) as T;
    expect(await loadCapabilities(fetcher)).toEqual({ kind: "unavailable" });
  });
});

describe("isTelegramAvailable", () => {
  it("shows the card only for a definite yes", () => {
    expect(isTelegramAvailable({ kind: "ready", capabilities: { telegram: true } })).toBe(true);
  });

  it("hides it when the server has no bot", () => {
    expect(isTelegramAvailable({ kind: "ready", capabilities: { telegram: false } })).toBe(false);
  });

  it("hides it while the answer is unknown, and when asking failed", () => {
    expect(isTelegramAvailable({ kind: "checking" })).toBe(false);
    expect(isTelegramAvailable({ kind: "unavailable" })).toBe(false);
  });
});
