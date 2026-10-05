import { describe, expect, it } from "vitest";
import { ApiError } from "./api";
import {
  PAYLOAD_TOO_LARGE_MESSAGE,
  friendlyApiMessage,
  humanizeWait,
  parseRetryAfterSeconds,
  rateLimitedMessage,
} from "./rateLimitMessage";

describe("humanizeWait", () => {
  it.each([
    [1, "less than a minute"],
    [59, "less than a minute"],
    [60, "about a minute"],
    [61, "about 2 minutes"],
    [700, "about 12 minutes"],
    [3540, "about 59 minutes"],
    [3541, "about an hour"],
    [3599, "about an hour"],
    [3600, "about an hour"],
    [3601, "about 2 hours"],
    [10800, "about 3 hours"],
    [82800, "about 23 hours"],
    [82801, "about a day"],
    [86399, "about a day"],
    [86400, "about a day"],
    [86401, "about 2 days"],
    [3 * 86400, "about 3 days"],
  ])("%i seconds is %s", (seconds, expected) => {
    expect(humanizeWait(seconds)).toBe(expected);
  });

  it("rounds up, so nobody is told to come back a moment too early", () => {
    expect(humanizeWait(11 * 60 + 1)).toBe("about 12 minutes");
    expect(humanizeWait(2 * 3600 + 1)).toBe("about 3 hours");
  });

  it("says less than a minute for anything that is not a positive number", () => {
    expect(humanizeWait(0)).toBe("less than a minute");
    expect(humanizeWait(-5)).toBe("less than a minute");
    expect(humanizeWait(Number.NaN)).toBe("less than a minute");
    expect(humanizeWait(Number.POSITIVE_INFINITY)).toBe("less than a minute");
  });
});

describe("parseRetryAfterSeconds", () => {
  it("reads the envelope's details first", () => {
    expect(parseRetryAfterSeconds({ retry_after_seconds: 725, bucket: "prepare" }, "30")).toBe(725);
  });

  it("rounds a fractional wait up", () => {
    expect(parseRetryAfterSeconds({ retry_after_seconds: 12.2 }, null)).toBe(13);
  });

  it("falls back to the Retry-After header when the body names none", () => {
    expect(parseRetryAfterSeconds({}, "90")).toBe(90);
    expect(parseRetryAfterSeconds(undefined, " 45 ")).toBe(45);
    expect(parseRetryAfterSeconds({ retry_after_seconds: "soon" }, "7")).toBe(7);
  });

  it("accepts 1 second, the server's own clamp for any wait under a second, from either source", () => {
    // Each source on its own, so neither can cover for the other.
    expect(parseRetryAfterSeconds({ retry_after_seconds: 1 }, null)).toBe(1);
    expect(parseRetryAfterSeconds(undefined, "1")).toBe(1);
    expect(parseRetryAfterSeconds({ retry_after_seconds: "soon" }, "1")).toBe(1);
    expect(rateLimitedMessage(parseRetryAfterSeconds({ retry_after_seconds: 1 }, null))).toContain(
      "less than a minute",
    );
  });

  it("is undefined when neither is usable", () => {
    expect(parseRetryAfterSeconds(undefined, undefined)).toBeUndefined();
    expect(parseRetryAfterSeconds(null, null)).toBeUndefined();
    expect(parseRetryAfterSeconds({ retry_after_seconds: 0 }, "0")).toBeUndefined();
    expect(parseRetryAfterSeconds({ retry_after_seconds: -3 }, null)).toBeUndefined();
    expect(parseRetryAfterSeconds({}, "Wed, 21 Oct 2026 07:28:00 GMT")).toBeUndefined();
    expect(parseRetryAfterSeconds({}, "1e3")).toBeUndefined();
    expect(parseRetryAfterSeconds("text", "")).toBeUndefined();
  });
});

describe("rateLimitedMessage", () => {
  it("puts the humanized wait in the sentence", () => {
    expect(rateLimitedMessage(700)).toBe("You are doing that too often. Try again in about 12 minutes.");
    expect(rateLimitedMessage(20)).toBe("You are doing that too often. Try again in less than a minute.");
  });

  it("does not invent a wait it was not given", () => {
    expect(rateLimitedMessage(undefined)).toBe(
      "You are doing that too often. Try again in a little while.",
    );
  });
});

describe("friendlyApiMessage", () => {
  it("explains a rate limit with the wait, whatever wording the server used", () => {
    const error = new ApiError(429, "server wording", "RATE_LIMITED", true, 725);
    expect(friendlyApiMessage(error, "Failed")).toBe(
      "You are doing that too often. Try again in about 13 minutes.",
    );
  });

  it("explains a rate limit that named no wait", () => {
    const error = new ApiError(429, "server wording", "RATE_LIMITED", true);
    expect(friendlyApiMessage(error, "Failed")).toContain("Try again in a little while.");
  });

  it("treats an edge proxy's bare 429 (no envelope, no code) the same way", () => {
    expect(friendlyApiMessage(new ApiError(429, "Request failed (429)", undefined, undefined, 120), "x")).toBe(
      "You are doing that too often. Try again in about 2 minutes.",
    );
  });

  it("does not mistake the provider's throttling for the platform's own limit", () => {
    const error = new ApiError(429, "The provider is throttling you.", "PROVIDER_RATE_LIMITED", true);
    expect(friendlyApiMessage(error, "Failed")).toBe("The provider is throttling you.");
  });

  it("explains an oversized request, by code or by a bare 413", () => {
    // The wording is pinned as a literal (not against the exported constant, which would pass
    // whatever it were changed to), like the 429 wording above.
    const expected = "That is too large to send. Shorten it and try again.";
    expect(PAYLOAD_TOO_LARGE_MESSAGE).toBe(expected);
    expect(friendlyApiMessage(new ApiError(413, "server wording", "PAYLOAD_TOO_LARGE"), "x")).toBe(
      expected,
    );
    expect(friendlyApiMessage(new ApiError(413, "Request failed (413)"), "x")).toBe(expected);
  });

  it("keeps the server's own message for every other API error", () => {
    expect(friendlyApiMessage(new ApiError(409, "Add a key first.", "SETUP_REQUIRED"), "x")).toBe(
      "Add a key first.",
    );
    expect(friendlyApiMessage(new ApiError(500, "Something went wrong.", "INTERNAL_ERROR"), "x")).toBe(
      "Something went wrong.",
    );
  });

  it("keeps a plain Error's message, and uses the fallback for a thrown non-Error", () => {
    expect(friendlyApiMessage(new TypeError("Failed to fetch"), "Failed to load")).toBe("Failed to fetch");
    expect(friendlyApiMessage("boom", "Failed to load")).toBe("Failed to load");
    expect(friendlyApiMessage(undefined, "Failed to load")).toBe("Failed to load");
  });
});
