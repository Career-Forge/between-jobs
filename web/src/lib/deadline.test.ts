import { afterEach, describe, expect, it, vi } from "vitest";
import { deadlineSignal, isTimeoutError } from "./deadline";

describe("deadlineSignal", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("is a signal that aborts after the time given, with a TimeoutError", async () => {
    const signal = deadlineSignal(10);
    expect(signal).toBeInstanceOf(AbortSignal);
    expect(signal?.aborted).toBe(false);
    await new Promise((resolve) => setTimeout(resolve, 40));
    expect(signal?.aborted).toBe(true);
    expect(isTimeoutError(signal?.reason)).toBe(true);
  });

  it("is undefined where the platform has no AbortSignal.timeout: the request then has no deadline, as before", () => {
    vi.stubGlobal("AbortSignal", {});
    expect(deadlineSignal(1000)).toBeUndefined();
    vi.stubGlobal("AbortSignal", undefined);
    expect(deadlineSignal(1000)).toBeUndefined();
  });
});

describe("isTimeoutError", () => {
  it("is true for a request that timed out or was aborted, whether or not it is an Error", () => {
    expect(isTimeoutError(new DOMException("x", "TimeoutError"))).toBe(true);
    expect(isTimeoutError(new DOMException("x", "AbortError"))).toBe(true);
    expect(isTimeoutError({ name: "TimeoutError" })).toBe(true);
  });

  it("is false for everything else", () => {
    for (const other of [new Error("x"), new TypeError("Failed to fetch"), "TimeoutError", null, undefined, 5, {}]) {
      expect(isTimeoutError(other)).toBe(false);
    }
  });
});
