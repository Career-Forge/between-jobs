import { describe, expect, it } from "vitest";

// The suite's zone is pinned by src/testing/pinTimeZone.ts (vite.config.ts: test.globalSetup), so a
// date a page writes does not depend on the machine the tests run on.

// @types/node is not installed in this package; only the environment is read.
declare const process: { env: Record<string, string | undefined> };

describe("the suite's time zone", () => {
  it("is UTC unless TEST_TZ asked for another", () => {
    const asked = process.env.TEST_TZ;
    if (asked === undefined) {
      expect(new Date("2026-09-22T05:00:00Z").getTimezoneOffset()).toBe(0);
      expect(new Date("2026-09-22T05:00:00Z").getDate()).toBe(22);
    } else {
      expect(Intl.DateTimeFormat().resolvedOptions().timeZone).toBe(asked);
    }
  });
});
