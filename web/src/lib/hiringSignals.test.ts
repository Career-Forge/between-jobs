import { describe, expect, it } from "vitest";
import {
  APPLICATION_MISSING_MESSAGE,
  CACHED_NOTE,
  EMBED_URL_PREFIX,
  FRESHNESS_OPTIONS,
  NOTHING_SHOWN_NOTE,
  NO_POSTS_SEEN_NOTE,
  UNREACHABLE_MESSAGE,
  UNREADABLE_MESSAGE,
  YOU_COM_NO_LINKEDIN_NOTE,
  classifyFailure,
  commentCountLabel,
  countsSummary,
  emptyStateNote,
  failureMessage,
  freshnessLabel,
  isSignalSaved,
  postedAtFromActivityId,
  postedLabel,
  postsSeen,
  providerLabel,
  registryMatchNote,
  relativeAgo,
  removeSave,
  resultHeadline,
  resultSource,
  safePostUrl,
  savedCardKey,
  savedLabel,
  savesByActivity,
  searchCardKey,
  speciesInfo,
  speciesLegendLines,
  upsertSave,
  validateEmbedUrl,
  widerFreshness,
  withoutSearchKeys,
} from "./hiringSignals";
import type { HiringSignal, SavedPost, SignalCounts } from "./hiringSignalsTypes";

// All ids, names and urls in this file are synthetic. The linkedin.com strings
// are inputs to pure validators -- nothing here ever fetches them.
const ID = "7000000000000000001";
const GOOD_EMBED = `${EMBED_URL_PREFIX}${ID}`;
const NOW = new Date("2026-09-19T12:00:00Z");

function ago(ms: number): Date {
  return new Date(NOW.getTime() - ms);
}
const MINUTE = 60_000;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;

function makeSignal(overrides: Partial<HiringSignal> = {}): HiringSignal {
  return {
    activity_id: ID,
    post_url: `https://www.linkedin.com/posts/example-${ID}`,
    embed_url: GOOD_EMBED,
    author_name: "Jane Example",
    posted_at: null,
    age_hint: null,
    species: "unclassified",
    comment_count: null,
    role_match: null,
    registry_match: null,
    saved: false,
    ...overrides,
  };
}

function makeSave(overrides: Partial<SavedPost> = {}): SavedPost {
  return {
    id: "save-1",
    activity_id: ID,
    post_url: `https://www.linkedin.com/feed/update/urn:li:activity:${ID}`,
    embed_url: GOOD_EMBED,
    created_at: "2026-09-18T12:00:00Z",
    ...overrides,
  };
}

function makeCounts(overrides: Partial<SignalCounts> = {}): SignalCounts {
  return {
    raw_hits: 0,
    rejected: 0,
    duplicates: 0,
    off_topic_hidden: 0,
    echoes_hidden: 0,
    job_seekers_hidden: 0,
    too_old_hidden: 0,
    shown: 0,
    ...overrides,
  };
}

describe("validateEmbedUrl", () => {
  it("accepts exactly the official embed address for a digit-only activity id", () => {
    expect(EMBED_URL_PREFIX).toBe("https://www.linkedin.com/embed/feed/update/urn:li:activity:");
    expect(validateEmbedUrl(GOOD_EMBED)).toBe(GOOD_EMBED);
    expect(validateEmbedUrl(`${EMBED_URL_PREFIX}1`)).toBe(`${EMBED_URL_PREFIX}1`);
    expect(validateEmbedUrl(`${EMBED_URL_PREFIX}${"9".repeat(25)}`)).not.toBeNull();
  });

  const rejected: [string, string][] = [
    ["http instead of https", `http://www.linkedin.com/embed/feed/update/urn:li:activity:${ID}`],
    ["a bare linkedin.com host", `https://linkedin.com/embed/feed/update/urn:li:activity:${ID}`],
    ["a country sub-domain", `https://es.linkedin.com/embed/feed/update/urn:li:activity:${ID}`],
    [
      "a lookalike host",
      `https://www.linkedin.com.example.com/embed/feed/update/urn:li:activity:${ID}`,
    ],
    [
      "a userinfo trick pointing elsewhere",
      `https://www.linkedin.com@example.com/embed/feed/update/urn:li:activity:${ID}`,
    ],
    ["the non-embed post path", `https://www.linkedin.com/feed/update/urn:li:activity:${ID}`],
    [
      "another urn kind",
      `https://www.linkedin.com/embed/feed/update/urn:li:ugcPost:${ID}`,
    ],
    ["a query string", `${GOOD_EMBED}?rcm=abc`],
    ["a fragment", `${GOOD_EMBED}#x`],
    ["a trailing slash", `${GOOD_EMBED}/`],
    ["a trailing newline", `${GOOD_EMBED}\n`],
    ["a trailing space", `${GOOD_EMBED} `],
    ["a leading space", ` ${GOOD_EMBED}`],
    ["no id at all", EMBED_URL_PREFIX],
    ["letters in the id", `${EMBED_URL_PREFIX}abc123`],
    ["full-width digits", `${EMBED_URL_PREFIX}１２３`],
    ["Arabic-Indic digits", `${EMBED_URL_PREFIX}٣٤٥`],
    ["a 26-digit id", `${EMBED_URL_PREFIX}${"9".repeat(26)}`],
    ["a percent-encoded id", `${EMBED_URL_PREFIX}7000%30`],
    ["an upper-case scheme", GOOD_EMBED.replace("https", "HTTPS")],
    ["an upper-case host", GOOD_EMBED.replace("www.linkedin.com", "WWW.LINKEDIN.COM")],
    ["a javascript: url", "javascript:alert(1)"],
    ["a data: url", "data:text/html,<script>alert(1)</script>"],
    ["an empty string", ""],
  ];
  it.each(rejected)("rejects %s", (_name, url) => {
    expect(validateEmbedUrl(url)).toBeNull();
  });

  it("rejects anything that is not a string", () => {
    for (const value of [null, undefined, 123, {}, [], true]) {
      expect(validateEmbedUrl(value)).toBeNull();
    }
  });

  it("when given the post's own id, also requires the url to point at that same post", () => {
    expect(validateEmbedUrl(GOOD_EMBED, ID)).toBe(GOOD_EMBED);
    expect(validateEmbedUrl(GOOD_EMBED, "7000000000000000002")).toBeNull();
    expect(validateEmbedUrl(GOOD_EMBED, "not-an-id")).toBeNull();
    expect(validateEmbedUrl(GOOD_EMBED, "")).toBeNull();
  });
});

describe("safePostUrl", () => {
  it("accepts an https url on linkedin.com or any of its sub-domains", () => {
    const url = `https://www.linkedin.com/feed/update/urn:li:activity:${ID}`;
    expect(safePostUrl(url)).toBe(url);
    expect(safePostUrl("https://linkedin.com/posts/x")).toBe("https://linkedin.com/posts/x");
    expect(safePostUrl("https://es.linkedin.com/posts/x")).toBe("https://es.linkedin.com/posts/x");
  });

  it("drops the query string and fragment, which can carry a member-identifying token", () => {
    expect(safePostUrl("https://www.linkedin.com/posts/x?rcm=abc123&utm_source=s#frag")).toBe(
      "https://www.linkedin.com/posts/x",
    );
  });

  const rejected: [string, unknown][] = [
    ["http", "http://www.linkedin.com/posts/x"],
    ["a javascript: url", "javascript:alert(1)"],
    ["a data: url", "data:text/html,hi"],
    ["another host", "https://www.example.com/posts/x"],
    ["a lookalike that ends in the name", "https://notlinkedin.com/posts/x"],
    ["a lookalike prefix", "https://linkedin.com.example.com/posts/x"],
    ["a hyphenated lookalike", "https://evil-linkedin.com/posts/x"],
    ["linkedin.com only in the path", "https://www.example.com/www.linkedin.com/posts/x"],
    ["linkedin.com only in the query", "https://www.example.com/?u=www.linkedin.com"],
    ["credentials in the url", "https://user:pw@www.linkedin.com/posts/x"],
    ["a userinfo trick", "https://www.linkedin.com@example.com/posts/x"],
    ["a non-default port", "https://www.linkedin.com:8443/posts/x"],
    ["a protocol-relative url", "//www.linkedin.com/posts/x"],
    ["a relative path", "/posts/x"],
    ["not a url at all", "not a url"],
    ["an empty string", ""],
    ["null", null],
    ["a number", 42],
  ];
  it.each(rejected)("rejects %s", (_name, url) => {
    expect(safePostUrl(url)).toBeNull();
  });
});

describe("speciesInfo", () => {
  it("labels 'unclassified' neutrally and says plainly it is not a confirmed hiring call", () => {
    const info = speciesInfo("unclassified");
    expect(info.label).toBe("Unclassified");
    expect(info.badgeClass).toBe("bj-badge-muted");
    expect(info.description).toContain("not confirmed");
  });

  it("gives each template-matched species its own label, in the gold evidence tone", () => {
    expect(speciesInfo("ats_echo").label).toBe("Job listing share");
    expect(speciesInfo("referral_offer").label).toBe("Referral offer");
    expect(speciesInfo("hiring_drive").label).toBe("Hiring drive");
    for (const species of ["ats_echo", "referral_offer", "hiring_drive"]) {
      expect(speciesInfo(species).badgeClass).toBe("bj-badge-gold");
    }
  });

  it("falls back to the neutral entry for a species this page has never heard of", () => {
    expect(speciesInfo("something_new")).toEqual(speciesInfo("unclassified"));
    expect(speciesInfo("")).toEqual(speciesInfo("unclassified"));
  });

  it("never names the source site or claims anyone is actively hiring", () => {
    for (const species of ["unclassified", "ats_echo", "referral_offer", "hiring_drive"]) {
      const { label, description } = speciesInfo(species);
      expect(`${label} ${description}`).not.toMatch(/linkedin|actively hiring/i);
    }
  });
});

describe("freshness labels", () => {
  it("offers 24 hours, 3 days and 7 days, in that order", () => {
    expect(FRESHNESS_OPTIONS).toEqual([
      { value: "day", label: "24 hours" },
      { value: "3days", label: "3 days" },
      { value: "week", label: "7 days" },
    ]);
  });

  it("phrases each window for a sentence", () => {
    expect(freshnessLabel("day")).toBe("last 24 hours");
    expect(freshnessLabel("3days")).toBe("last 3 days");
    expect(freshnessLabel("week")).toBe("last 7 days");
  });

  it("suggests the next wider window, and none once already at the widest", () => {
    expect(widerFreshness("day")).toBe("3days");
    expect(widerFreshness("3days")).toBe("week");
    expect(widerFreshness("week")).toBeNull();
  });
});

describe("providerLabel", () => {
  it("names each known provider and does not guess for an unknown one", () => {
    expect(providerLabel("you_com")).toBe("You.com");
    expect(providerLabel("brave")).toBe("Brave Search");
    expect(providerLabel("serper")).toBe("Serper");
    expect(providerLabel("firecrawl")).toBe("Firecrawl");
    expect(providerLabel(null)).toBe("your search provider");
  });
});

describe("relativeAgo", () => {
  it("reads 'just now' inside the first minute", () => {
    expect(relativeAgo(ago(0), NOW)).toBe("just now");
    expect(relativeAgo(ago(59_000), NOW)).toBe("just now");
  });

  it("counts minutes, hours and days, with correct singular forms", () => {
    expect(relativeAgo(ago(MINUTE), NOW)).toBe("1 minute ago");
    expect(relativeAgo(ago(45 * MINUTE), NOW)).toBe("45 minutes ago");
    expect(relativeAgo(ago(HOUR), NOW)).toBe("1 hour ago");
    expect(relativeAgo(ago(23 * HOUR), NOW)).toBe("23 hours ago");
    expect(relativeAgo(ago(DAY), NOW)).toBe("1 day ago");
    expect(relativeAgo(ago(6 * DAY + 5 * HOUR), NOW)).toBe("6 days ago");
  });

  it("switches to weeks, months and years for older posts (saved posts can be old)", () => {
    expect(relativeAgo(ago(14 * DAY), NOW)).toBe("2 weeks ago");
    expect(relativeAgo(ago(59 * DAY), NOW)).toBe("8 weeks ago");
    expect(relativeAgo(ago(60 * DAY), NOW)).toBe("2 months ago");
    expect(relativeAgo(ago(364 * DAY), NOW)).toBe("12 months ago");
    expect(relativeAgo(ago(365 * DAY), NOW)).toBe("1 year ago");
    expect(relativeAgo(ago(800 * DAY), NOW)).toBe("2 years ago");
  });

  it("tolerates a few minutes of clock skew but not a timestamp far in the future", () => {
    expect(relativeAgo(new Date(NOW.getTime() + 4 * MINUTE), NOW)).toBe("just now");
    expect(relativeAgo(new Date(NOW.getTime() + 6 * MINUTE), NOW)).toBeNull();
    expect(relativeAgo(new Date(NOW.getTime() + 3 * DAY), NOW)).toBeNull();
  });

  it("is null for an invalid date", () => {
    expect(relativeAgo(new Date("not a date"), NOW)).toBeNull();
  });
});

describe("postedLabel", () => {
  it("prefers the decoded post time", () => {
    expect(postedLabel(ago(3 * DAY).toISOString(), "9 days ago", NOW)).toBe("Posted 3 days ago");
  });

  it("falls back to the index's own stamp and says that is where it came from", () => {
    expect(postedLabel(null, "3 days ago", NOW)).toBe("Posted 3 days ago (per search index)");
    expect(postedLabel(null, "  5 hours ago  ", NOW)).toBe("Posted 5 hours ago (per search index)");
  });

  it("says 'time unknown' when there is nothing to go on", () => {
    expect(postedLabel(null, null, NOW)).toBe("Posted time unknown");
    expect(postedLabel(null, "   ", NOW)).toBe("Posted time unknown");
  });

  it("does not trust an unreadable or implausible posted_at, and falls through", () => {
    expect(postedLabel("not a date", null, NOW)).toBe("Posted time unknown");
    expect(postedLabel("not a date", "1 day ago", NOW)).toBe("Posted 1 day ago (per search index)");
    const future = new Date(NOW.getTime() + 3 * DAY).toISOString();
    expect(postedLabel(future, null, NOW)).toBe("Posted time unknown");
  });
});

describe("savedLabel", () => {
  it("reads how long ago the post was saved", () => {
    expect(savedLabel(ago(2 * DAY).toISOString(), NOW)).toBe("Saved 2 days ago");
    expect(savedLabel(ago(10 * 1000).toISOString(), NOW)).toBe("Saved just now");
  });

  it("is a plain 'Saved' when the stored date cannot be read", () => {
    expect(savedLabel("", NOW)).toBe("Saved");
    expect(savedLabel("garbage", NOW)).toBe("Saved");
    expect(savedLabel(new Date(NOW.getTime() + 3 * DAY).toISOString(), NOW)).toBe("Saved");
  });
});

describe("postedAtFromActivityId", () => {
  // Same vectors as the server's tests: id >> 22 is the creation time in
  // epoch milliseconds, and the low 22 bits do not matter.
  function idFor(ms: number, lowBits = 0n): string {
    return ((BigInt(ms) << 22n) | lowBits).toString();
  }

  it("decodes the creation time from the id's high bits", () => {
    expect(postedAtFromActivityId(idFor(1_700_000_000_000))?.toISOString()).toBe(
      "2023-11-14T22:13:20.000Z",
    );
    expect(postedAtFromActivityId((1_700_000_000_000n << 22n).toString())?.getTime()).toBe(
      1_700_000_000_000,
    );
  });

  it("ignores the low 22 bits", () => {
    const withLow = postedAtFromActivityId(idFor(1_700_000_000_000, 0x3fffffn));
    expect(withLow?.getTime()).toBe(1_700_000_000_000);
  });

  it("is unknown outside the 2003-2100 plausibility band, and inclusive at its edges", () => {
    const first = 1_041_379_200_000;
    const last = 4_102_444_800_000;
    expect(postedAtFromActivityId(idFor(first))?.getTime()).toBe(first);
    expect(postedAtFromActivityId(idFor(first - 1))).toBeNull();
    expect(postedAtFromActivityId(idFor(last))?.getTime()).toBe(last);
    expect(postedAtFromActivityId(idFor(last + 1))).toBeNull();
  });

  it("is unknown for anything that is not a digit-only id", () => {
    for (const bad of ["", "abc", "12 34", "-5", "1.5", "１２", "9".repeat(26)]) {
      expect(postedAtFromActivityId(bad)).toBeNull();
    }
  });
});

describe("small labels", () => {
  it("counts comments with the right singular/plural and shows nothing for null", () => {
    expect(commentCountLabel(null)).toBeNull();
    expect(commentCountLabel(0)).toBe("0 comments");
    expect(commentCountLabel(1)).toBe("1 comment");
    expect(commentCountLabel(1234)).toBe("1,234 comments");
  });

  it("words each registry match state, and says nothing when it does not apply", () => {
    expect(registryMatchNote(null)).toBeNull();
    expect(registryMatchNote("matched")).toBe("Matches a job listing we already track.");
    expect(registryMatchNote("possible")).toBe("May match a job listing we already track.");
    // "unmatched" means we DO track the company: the server sends nothing (null)
    // when it cannot tie the page to a company, so this never claims more.
    expect(registryMatchNote("unmatched")).toBe("We track this company, but not this listing yet.");
  });

  it("gives the empty result a headline that claims nothing about the company, and counts otherwise", () => {
    expect(resultHeadline(0)).toBe("No posts to show for this company");
    expect(resultHeadline(0, 0)).toBe("No posts to show for this company");
    expect(resultHeadline(0)).not.toMatch(/no recent|hiring/i);
    expect(resultHeadline(1)).toBe("1 post found");
    expect(resultHeadline(4)).toBe("4 posts found");
  });

  it("counts posts, never 'hiring posts': an unclassified post is not confirmed as a hiring call", () => {
    for (const n of [1, 2, 13]) expect(resultHeadline(n)).not.toMatch(/hiring/i);
  });

  it("does not say 'no posts found' when entries came back that this page could not display", () => {
    expect(resultHeadline(0, 2)).toBe("This page could not display the posts the search found");
    expect(resultHeadline(0, 2)).not.toMatch(/no posts/i);
    // With something on screen the counts sentence carries the missing ones.
    expect(resultHeadline(3, 2)).toBe("3 posts found");
  });

  it("joins the query label and provider, leaving out a missing label", () => {
    expect(resultSource("Acme -- software engineer -- last 7 days", "you_com")).toBe(
      "Acme -- software engineer -- last 7 days -- via You.com",
    );
    expect(resultSource("", "brave")).toBe("via Brave Search");
    expect(resultSource("Acme", null)).toBe("Acme -- via your search provider");
  });

  it("has a cached note that does not claim a specific age, and says what may be missing", () => {
    expect(CACHED_NOTE).toContain("cached");
    expect(CACHED_NOTE).not.toMatch(/\d/);
    expect(CACHED_NOTE).toContain("newest posts may not be included");
    expect(CACHED_NOTE).not.toContain("slightly");
  });
});

describe("countsSummary", () => {
  const base = { unreadable: 0, freshness: "week" } as const;

  it("is null when the server's counts were unreadable, rather than a partial breakdown", () => {
    expect(countsSummary({ ...base, counts: null, shown: 3 })).toBeNull();
  });

  it("says so plainly when the search returned nothing", () => {
    expect(countsSummary({ ...base, counts: makeCounts(), shown: 0 })).toBe(
      "The search returned no results.",
    );
  });

  it("says everything was shown when nothing was hidden", () => {
    const counts = makeCounts({ raw_hits: 5, shown: 5 });
    expect(countsSummary({ ...base, counts, shown: 5 })).toBe(
      "The search returned 5 results; all shown.",
    );
    expect(countsSummary({ ...base, counts: makeCounts({ raw_hits: 1, shown: 1 }), shown: 1 })).toBe(
      "The search returned 1 result; all shown.",
    );
  });

  it("names every hidden category and why, so a short list is never silent", () => {
    const counts = makeCounts({
      raw_hits: 30,
      off_topic_hidden: 9,
      duplicates: 3,
      echoes_hidden: 4,
      job_seekers_hidden: 2,
      too_old_hidden: 5,
      rejected: 1,
      shown: 6,
    });
    expect(countsSummary({ ...base, counts, shown: 6 })).toBe(
      "The search returned 30 results; 6 shown. Not shown: 9 not about this company, " +
        "3 duplicates, 4 automatic job-listing shares that repeat listings we already track, " +
        "2 possible job-seeker posts, 5 older than the last 7 days and 1 that was not a usable post link.",
    );
  });

  it("says the hidden posts were not about the COMPANY -- role match never hides anything", () => {
    // Regression: the sentence used to say "did not match this role", while the
    // server hides a post for the company only and shows role mismatches.
    const counts = makeCounts({ raw_hits: 10, off_topic_hidden: 10, shown: 0 });
    const text = countsSummary({ ...base, counts, shown: 0 }) ?? "";
    expect(text).toContain("10 not about this company");
    expect(text).not.toMatch(/role/i);
  });

  it("agrees the verb with the count for the categories that have a verb", () => {
    const one = countsSummary({
      ...base,
      counts: makeCounts({ raw_hits: 4, echoes_hidden: 1, rejected: 1, shown: 2 }),
      shown: 2,
    });
    expect(one).toContain("1 automatic job-listing share that repeats a listing we already track");
    expect(one).toContain("1 that was not a usable post link");
    expect(one).not.toMatch(/1 [^,.]*that (repeat|were) /);
    const many = countsSummary({
      ...base,
      counts: makeCounts({ raw_hits: 9, echoes_hidden: 2, rejected: 2, shown: 5 }),
      shown: 5,
    });
    expect(many).toContain("2 automatic job-listing shares that repeat listings we already track");
    expect(many).toContain("2 that were not usable post links");
  });

  it("uses singular forms, and only lists the categories that are non-zero", () => {
    const counts = makeCounts({ raw_hits: 4, duplicates: 1, job_seekers_hidden: 1, shown: 2 });
    expect(countsSummary({ ...base, counts, shown: 2 })).toBe(
      "The search returned 4 results; 2 shown. Not shown: 1 duplicate and 1 possible job-seeker post.",
    );
  });

  it("names the selected window in the 'too old' reason", () => {
    const counts = makeCounts({ raw_hits: 3, too_old_hidden: 3, shown: 0 });
    expect(countsSummary({ ...base, freshness: "day", counts, shown: 0 })).toBe(
      "The search returned 3 results; 0 shown. Not shown: 3 older than the last 24 hours.",
    );
    expect(countsSummary({ ...base, freshness: "3days", counts, shown: 0 })).toContain(
      "older than the last 3 days",
    );
  });

  it("owns up to entries this page had to leave out", () => {
    const counts = makeCounts({ raw_hits: 4, shown: 3 });
    expect(countsSummary({ ...base, counts, shown: 2, unreadable: 1 })).toBe(
      "The search returned 4 results; 2 shown. Not shown: 1 that this page could not display.",
    );
  });

  it("uses the number of cards actually on screen, not the server's own 'shown'", () => {
    const counts = makeCounts({ raw_hits: 5, shown: 5 });
    expect(countsSummary({ ...base, counts, shown: 4, unreadable: 1 })).toContain("; 4 shown.");
  });

  it("does not claim 'all shown' when raw hits exist but nothing is on screen", () => {
    const counts = makeCounts({ raw_hits: 2 });
    expect(countsSummary({ ...base, counts, shown: 0 })).toBe(
      "The search returned 2 results; none shown.",
    );
  });
});

describe("saved-post list helpers", () => {
  it("puts a new save on top (newest first) and never grows a list on a repeat", () => {
    const older = makeSave({ id: "a", activity_id: "111" });
    const newer = makeSave({ id: "b", activity_id: "222" });
    expect(upsertSave([older], newer).map((s) => s.id)).toEqual(["b", "a"]);

    // The same save again (POST is idempotent and returns the existing row).
    expect(upsertSave([newer, older], older).map((s) => s.id)).toEqual(["b", "a"]);
    // The same post under a different row id replaces rather than duplicates.
    const replaced = upsertSave([older], makeSave({ id: "z", activity_id: "111" }));
    expect(replaced.map((s) => s.id)).toEqual(["z"]);
  });

  it("does not mutate the list it was given", () => {
    const original = [makeSave({ id: "a", activity_id: "111" })];
    upsertSave(original, makeSave({ id: "b", activity_id: "222" }));
    removeSave(original, "a");
    expect(original.map((s) => s.id)).toEqual(["a"]);
  });

  it("removes exactly one row by id", () => {
    const saves = [makeSave({ id: "a", activity_id: "1" }), makeSave({ id: "b", activity_id: "2" })];
    expect(removeSave(saves, "a").map((s) => s.id)).toEqual(["b"]);
    expect(removeSave(saves, "missing")).toHaveLength(2);
  });

  it("indexes saves by activity id so a result card can find its own row", () => {
    const byActivity = savesByActivity([makeSave({ id: "a", activity_id: "111" })]);
    expect(byActivity.get("111")?.id).toBe("a");
    expect(byActivity.get("999")).toBeUndefined();
  });

  it("treats the loaded saved list as the source of truth for a card's Saved state", () => {
    const signal = makeSignal({ saved: true });
    const empty = savesByActivity([]);
    const has = savesByActivity([makeSave({ activity_id: ID })]);

    // Loaded: a removed post stays removed even though the search said saved.
    expect(isSignalSaved(signal, empty, true)).toBe(false);
    expect(isSignalSaved(signal, has, true)).toBe(true);
    // Not loaded (or failed to): the server's own flag is the best there is.
    expect(isSignalSaved(signal, empty, false)).toBe(true);
    expect(isSignalSaved(makeSignal({ saved: false }), empty, false)).toBe(false);
  });
});

describe("classifyFailure", () => {
  class FakeApiError extends Error {
    constructor(
      public readonly status: number,
      message: string,
      public readonly code?: string,
    ) {
      super(message);
    }
  }
  const unreachable = "Could not reach the server.";

  it("recognises the switched-off feature so the panel can render nothing", () => {
    expect(classifyFailure(new FakeApiError(404, "off", "FEATURE_DISABLED"), unreachable)).toEqual({
      kind: "disabled",
    });
  });

  it("separates setup-required and not-found, keeping the server's message", () => {
    expect(
      classifyFailure(new FakeApiError(409, "Add a search key.", "SETUP_REQUIRED"), unreachable),
    ).toEqual({ kind: "setup_required", message: "Add a search key." });
    expect(
      classifyFailure(new FakeApiError(404, "No such application.", "NOT_FOUND"), unreachable),
    ).toEqual({ kind: "not_found", message: "No such application." });
  });

  it("does not confuse a plain 404 or a missing code with the disabled feature", () => {
    expect(classifyFailure(new FakeApiError(404, "Request failed (404)"), unreachable)).toEqual({
      kind: "error",
      message: "Request failed (404)",
      retryable: true,
    });
  });

  it("shows the server's message for a provider outage and any other coded error, retryable", () => {
    for (const code of ["PROVIDER_UNAVAILABLE", "PROVIDER_RATE_LIMITED", "INTERNAL_ERROR", "WHATEVER"]) {
      expect(classifyFailure(new FakeApiError(503, "Try later.", code), unreachable)).toEqual({
        kind: "error",
        message: "Try later.",
        retryable: true,
      });
    }
  });

  it("does not offer a retry for a request the server rejected as invalid -- it would fail the same way", () => {
    // The server's code is INVALID_INPUT (422); the design's contract calls it
    // INVALID_REQUEST. Either, or any 422, is not retryable.
    for (const [status, code] of [
      [422, "INVALID_INPUT"],
      [422, "INVALID_REQUEST"],
      [400, "INVALID_INPUT"],
      [422, undefined],
    ] as const) {
      expect(
        classifyFailure(new FakeApiError(status, "This application has no company name.", code), unreachable),
      ).toEqual({
        kind: "error",
        message: "This application has no company name.",
        retryable: false,
      });
    }
  });

  it("replaces a raw network error with the friendly message, and lets the user retry", () => {
    const expected = { kind: "error", message: unreachable, retryable: true };
    expect(classifyFailure(new TypeError("Failed to fetch"), unreachable)).toEqual(expected);
    expect(classifyFailure("boom", unreachable)).toEqual(expected);
    expect(classifyFailure(undefined, unreachable)).toEqual(expected);
  });
});

describe("failureMessage and fixed wording", () => {
  it("swaps the server's technical not-found text for plain language, and keeps every other message", () => {
    expect(failureMessage({ kind: "not_found", message: "no application found for id 'x'" })).toBe(
      APPLICATION_MISSING_MESSAGE,
    );
    expect(failureMessage({ kind: "setup_required", message: "Connect a provider." })).toBe(
      "Connect a provider.",
    );
    expect(failureMessage({ kind: "error", message: "Try later.", retryable: true })).toBe("Try later.");
  });

  it("never says 'try again' in a fixed message: the failure view has a button that does", () => {
    for (const message of [UNREACHABLE_MESSAGE, UNREADABLE_MESSAGE]) {
      expect(message).not.toMatch(/try again/i);
    }
  });

  it("keeps the fixed messages plain and free of the source site's name", () => {
    for (const message of [UNREACHABLE_MESSAGE, UNREADABLE_MESSAGE, APPLICATION_MISSING_MESSAGE]) {
      expect(message).not.toMatch(/linkedin/i);
      expect(message).not.toContain("\u2014"); // no em dash
    }
  });
});

describe("card keys", () => {
  it("keeps the results and saved-list namespaces separate", () => {
    expect(searchCardKey("123")).toBe("search:123");
    expect(savedCardKey("abc")).toBe("saved:abc");
    expect(searchCardKey("123")).not.toBe(savedCardKey("123"));
  });

  it("closes only the results' embeds when a new search starts", () => {
    const open = new Set([searchCardKey("1"), searchCardKey("2"), savedCardKey("s1")]);
    expect([...withoutSearchKeys(open)]).toEqual(["saved:s1"]);
    expect(open.size).toBe(3);
  });
});


describe("what an empty result says", () => {
  const seenNothing = makeCounts({ raw_hits: 0 });
  const onlyNonPosts = makeCounts({ raw_hits: 4, rejected: 4 });
  const postsButNoneShown = makeCounts({ raw_hits: 10, off_topic_hidden: 10 });

  it("counts the posts the index returned as raw hits less the unusable links", () => {
    expect(postsSeen(null)).toBeNull();
    expect(postsSeen(seenNothing)).toBe(0);
    expect(postsSeen(onlyNonPosts)).toBe(0);
    expect(postsSeen(postsButNoneShown)).toBe(10);
  });

  it("tells a You.com user that its index has no LinkedIn posts, instead of calling nothing an answer", () => {
    for (const counts of [seenNothing, onlyNonPosts]) {
      expect(emptyStateNote({ provider: "you_com", counts, unreadable: 0 })).toBe(
        YOU_COM_NO_LINKEDIN_NOTE,
      );
    }
    expect(YOU_COM_NO_LINKEDIN_NOTE).toMatch(/does not appear to include/);
    expect(YOU_COM_NO_LINKEDIN_NOTE).toMatch(/says nothing about this company/);
  });

  it("says a provider that returned no LinkedIn posts at all tells us little", () => {
    for (const provider of ["firecrawl", "brave", "serper", null] as const) {
      expect(emptyStateNote({ provider, counts: seenNothing, unreadable: 0 })).toBe(
        NO_POSTS_SEEN_NOTE,
      );
    }
  });

  it("says posts came back but none could be shown, when they did", () => {
    for (const provider of ["you_com", "firecrawl", null] as const) {
      expect(emptyStateNote({ provider, counts: postsButNoneShown, unreadable: 0 })).toBe(
        NOTHING_SHOWN_NOTE,
      );
    }
    // unreadable counts: nothing to say about what the index saw
    expect(emptyStateNote({ provider: "firecrawl", counts: null, unreadable: 0 })).toBe(
      NOTHING_SHOWN_NOTE,
    );
  });

  it("says nothing when entries came back that this page could not display", () => {
    expect(emptyStateNote({ provider: "you_com", counts: seenNothing, unreadable: 2 })).toBeNull();
  });

  it("never states that an empty result is an answer about the company", () => {
    for (const note of [YOU_COM_NO_LINKEDIN_NOTE, NO_POSTS_SEEN_NOTE, NOTHING_SHOWN_NOTE]) {
      expect(note).not.toMatch(/real answer/i);
      expect(note).not.toMatch(/linkedin\.com/i);
    }
    expect(NOTHING_SHOWN_NOTE).toContain("does not mean nobody is hiring");
  });
});

describe("speciesLegendLines", () => {
  it("has a visible line for each non-default species on screen, once", () => {
    const signals = [
      makeSignal({ activity_id: "1", species: "ats_echo" }),
      makeSignal({ activity_id: "2", species: "ats_echo" }),
      makeSignal({ activity_id: "3", species: "unclassified" }),
      makeSignal({ activity_id: "4", species: "referral_offer" }),
    ];
    const lines = speciesLegendLines(signals);
    expect(lines.map((l) => l.species)).toEqual(["ats_echo", "referral_offer"]);
    expect(lines[0]).toEqual({
      species: "ats_echo",
      label: "Job listing share",
      description: speciesInfo("ats_echo").description,
    });
    expect(lines[0].description).toMatch(/not a personal hiring call/);
  });

  it("has none when only unclassified posts are shown (they have their own legend)", () => {
    expect(speciesLegendLines([makeSignal()])).toEqual([]);
    expect(speciesLegendLines([])).toEqual([]);
  });
});

describe("classifyFailure honors the server's retryable flag", () => {
  class ApiErrorLike extends Error {
    constructor(
      public readonly status: number,
      message: string,
      public readonly code?: string,
      public readonly retryable?: boolean,
    ) {
      super(message);
    }
  }
  const unreachable = "Could not reach the server.";

  it("does not offer a retry for a 500 the server says can never succeed", () => {
    // INTERNAL_ERROR, retryable false: an application whose job snapshot is missing
    expect(
      classifyFailure(
        new ApiErrorLike(500, "This application's job snapshot is missing.", "INTERNAL_ERROR", false),
        unreachable,
      ),
    ).toEqual({
      kind: "error",
      message: "This application's job snapshot is missing.",
      retryable: false,
    });
  });

  it("offers a retry when the server says it can help, whatever the status", () => {
    expect(
      classifyFailure(new ApiErrorLike(503, "Busy.", "PROVIDER_UNAVAILABLE", true), unreachable),
    ).toEqual({ kind: "error", message: "Busy.", retryable: true });
    expect(classifyFailure(new ApiErrorLike(422, "x", "INVALID_INPUT", true), unreachable)).toEqual({
      kind: "error",
      message: "x",
      retryable: true,
    });
  });

  it("falls back to the status when the reply carried no flag", () => {
    expect(classifyFailure(new ApiErrorLike(422, "x", "INVALID_INPUT"), unreachable)).toMatchObject({
      retryable: false,
    });
    expect(classifyFailure(new ApiErrorLike(500, "x"), unreachable)).toMatchObject({
      retryable: true,
    });
  });
});
