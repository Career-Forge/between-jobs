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
      // Compared by what each prints, not by name: the runtime may know a zone by an older alias.
      const instant = new Date("2026-09-22T23:30:00Z");
      const options = { dateStyle: "short", timeStyle: "short" } as const;
      expect(new Intl.DateTimeFormat("en-US", options).format(instant)).toBe(new Intl.DateTimeFormat("en-US", { ...options, timeZone: asked }).format(instant));
    }
  });
});
