import { describe, expect, it } from "vitest";
import { ApiError } from "./api";
import { LINK_CHECK_DISABLED, UNREADABLE_WORDS } from "./linkCheck";
import { checkApplicationLink, linkCheckPath, type LinkCheckFetcher } from "./linkCheckClient";

// The fetcher is a recording fake: nothing here touches a network.

const APP = "6b1f0c1e-0000-4000-8000-0000000000aa";

interface Call {
  path: string;
  init: RequestInit | undefined;
}

function fakeFetcher(reply: unknown): { fetcher: LinkCheckFetcher; calls: Call[] } {
  const calls: Call[] = [];
  const fetcher = (async (path: string, init?: RequestInit) => {
    calls.push({ path, init });
    if (reply instanceof Error) throw reply;
    return reply;
  }) as LinkCheckFetcher;
  return { fetcher, calls };
}

describe("what goes over the wire", () => {
  it("is one POST to the application's check-link, with no body", async () => {
    const { fetcher, calls } = fakeFetcher({ state: "gone", reason: "absent", ats: "lever", checked_at: "2026-11-04T14:32:05Z" });
    await checkApplicationLink(fetcher, APP);
    expect(calls).toHaveLength(1);
    expect(calls[0].path).toBe(`/applications/${APP}/check-link`);
    expect(calls[0].init).toEqual({ method: "POST" });
    expect(calls[0].init).not.toHaveProperty("body");
  });

  it("encodes the id like any path segment, so it cannot name another route", () => {
    expect(linkCheckPath("a/b?c")).toBe("/applications/a%2Fb%3Fc/check-link");
    expect(linkCheckPath("../x")).toBe("/applications/..%2Fx/check-link");
  });
});

describe("what comes back", () => {
  it("is the answer, stamped with this page's clock", async () => {
    const { fetcher } = fakeFetcher({ state: "still_listed", reason: "listed", ats: "greenhouse", checked_at: "2026-11-04T14:32:05Z" });
    expect(await checkApplicationLink(fetcher, APP, () => 42)).toEqual({
      kind: "answered",
      result: { state: "still_listed", reason: "listed", checked_at: "2026-11-04T14:32:05Z" },
      receivedAt: 42,
    });
  });

  it.each([[null], [undefined], [{}], [{ state: "closed" }], ["gone"], [[]]])("says it could not read %j, and does not call it an answer", async (reply) => {
    const { fetcher } = fakeFetcher(reply);
    expect(await checkApplicationLink(fetcher, APP)).toEqual({ kind: "unanswered", words: UNREADABLE_WORDS });
  });

  it("does not throw for a failed request: the words come back instead", async () => {
    const limited = await checkApplicationLink(fakeFetcher(new ApiError(429, "x", "RATE_LIMITED", true, 3600)).fetcher, APP);
    expect(limited).toEqual({
      kind: "unanswered",
      words: "The check didn't go through. You are doing that too often. Try again in about an hour. Open the posting to see for yourself.",
    });

    const off = new ApiError(503, "x", "PROVIDER_UNAVAILABLE", true, undefined, undefined, undefined, undefined, LINK_CHECK_DISABLED);
    expect(await checkApplicationLink(fakeFetcher(off).fetcher, APP)).toEqual({
      kind: "unanswered",
      words: "Checking is switched off right now. Open the posting to see for yourself.",
    });

    const unreachable = await checkApplicationLink(fakeFetcher(new TypeError("Failed to fetch")).fetcher, APP);
    expect(unreachable.kind).toBe("unanswered");
  });

  it("makes no second request for a failure or an unreadable reply: nothing retries", async () => {
    for (const reply of [new ApiError(500, "down"), { state: "nope" }]) {
      const { fetcher, calls } = fakeFetcher(reply);
      await checkApplicationLink(fetcher, APP);
      expect(calls).toHaveLength(1);
    }
  });
});
