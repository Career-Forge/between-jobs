import { describe, expect, it } from "vitest";
import { ApiError } from "./api";
import {
  CANT_TELL_REASON_WORDS,
  CANT_TELL_SUGGESTION,
  GONE_WORDS,
  LINK_CHECK_DISABLED,
  STILL_LISTED_WORDS,
  UNKNOWN_REASON_WORDS,
  UNREADABLE_WORDS,
  cantTellWords,
  checkedWords,
  failureWords,
  isLinkCheckDisabled,
  linkCheckLine,
  parseLinkCheck,
  type LinkCheckEntry,
  type LinkCheckResult,
} from "./linkCheck";
import { findForbiddenWord, formatTrailTime } from "./trail";

// The words each reason is shown with, typed out here rather than imported, so that a change to one is a change to a test.

const ROBOTS = "We don't check this site because its rules ask automated tools to stay out";
const NO_ANSWER = "The company's site did not answer in time";
const EXPECTED_REASON_WORDS: ReadonlyArray<readonly [string, string]> = [
  ["board_missing_or_moved", "The company's board is not where we expected it, so we can't say about this job"],
  ["empty_listing", "The company's board returned no jobs right now"],
  ["signals_disagree", "The company's board and the job page disagree, so we won't guess"],
  ["blocked", "The company's site declined the check"],
  ["rate_limited", "The company's site asked us to slow down; try again later"],
  ["robots_disallowed", ROBOTS],
  ["robots_unavailable", ROBOTS],
  ["not_covered", "We don't check this company's job platform"],
  ["denylisted", "We don't check this site"],
  ["company_page", "This link is on the company's own site, not a job platform we can read"],
  ["no_link", "This job has no link saved"],
  ["unlisted_direct_link", "This job is only reachable by direct link, so absence from the board means nothing"],
  ["timeout", NO_ANSWER],
  ["network", NO_ANSWER],
  ["link_check_disabled", "Checking is switched off right now"],
];

const SUGGESTION = "Open the posting to see for yourself.";

const answered = (result: Partial<LinkCheckResult>, receivedAt = 1_000_000): LinkCheckEntry => ({
  kind: "answered",
  result: { state: "cant_tell", reason: "", checked_at: "2026-11-04T14:32:05Z", ...result },
  receivedAt,
});

describe("the three answers", () => {
  it("says a listed posting is on the company's own board, and nothing stronger", () => {
    expect(STILL_LISTED_WORDS).toBe("Still on the company's own board.");
  });

  it("says a gone posting is no longer on the board, that it usually closed, and that it is not a rejection", () => {
    expect(GONE_WORDS).toBe(
      "No longer on the company's own board. That usually means the posting closed. It does not mean you were rejected.",
    );
  });

  it("ends every can't-tell with the same suggestion", () => {
    expect(CANT_TELL_SUGGESTION).toBe(SUGGESTION);
  });
});

describe("the reasons a check can't tell", () => {
  it.each(EXPECTED_REASON_WORDS)("words %s as its own sentence, then the suggestion", (reason, words) => {
    expect(cantTellWords(reason)).toBe(`${words}. ${SUGGESTION}`);
  });

  it("has words for exactly the reasons listed above, no more and no fewer", () => {
    expect([...CANT_TELL_REASON_WORDS.keys()].sort()).toEqual(EXPECTED_REASON_WORDS.map(([reason]) => reason).sort());
  });

  it("says a reason it does not know as one it cannot use, without repeating the code", () => {
    for (const reason of ["", "something_new", "http_502", "constructor", "__proto__", "toString"]) {
      const words = cantTellWords(reason);
      expect(words).toBe(`${UNKNOWN_REASON_WORDS}. ${SUGGESTION}`);
      if (reason !== "") expect(words).not.toContain(reason);
    }
  });

  it("is the same words for the two reasons that mean the same thing", () => {
    expect(cantTellWords("robots_disallowed")).toBe(cantTellWords("robots_unavailable"));
    expect(cantTellWords("timeout")).toBe(cantTellWords("network"));
  });
});

describe("words nobody should be told", () => {
  const everything = [
    STILL_LISTED_WORDS,
    GONE_WORDS,
    ...[...CANT_TELL_REASON_WORDS.keys(), "unheard_of"].map(cantTellWords),
    UNREADABLE_WORDS,
    failureWords(new ApiError(429, "slow down", "RATE_LIMITED", true, 725)),
    failureWords(new ApiError(503, "off", "PROVIDER_UNAVAILABLE", true, undefined, undefined, undefined, undefined, LINK_CHECK_DISABLED)),
    failureWords(new ApiError(500, "Request failed (500)")),
    failureWords(new TypeError("Failed to fetch")),
  ];

  it("never says verified, or any other word the trail line refuses", () => {
    for (const words of everything) expect(findForbiddenWord(words), words).toBeNull();
  });

  it("never says a job was filled", () => {
    for (const words of everything) expect(words, words).not.toMatch(/\bfill(?:ed|s)?\b/i);
  });

  it("says rejected once, to say that a gone posting does not mean it", () => {
    const saying = everything.filter((words) => /reject/i.test(words));
    expect(saying).toEqual([GONE_WORDS]);
    expect(GONE_WORDS).toContain("It does not mean you were rejected.");
  });

  it("ends every line that is not an answer with the suggestion", () => {
    for (const words of everything.filter((words) => words !== STILL_LISTED_WORDS && words !== GONE_WORDS)) {
      expect(words.endsWith(SUGGESTION), words).toBe(true);
    }
  });
});

describe("parseLinkCheck", () => {
  it.each(["still_listed", "gone", "cant_tell"] as const)("reads a %s reply", (state) => {
    expect(parseLinkCheck({ state, reason: "x", ats: "greenhouse", checked_at: "2026-11-04T14:32:05Z" })).toEqual({
      state,
      reason: "x",
      checked_at: "2026-11-04T14:32:05Z",
    });
  });

  it("keeps a reply that left a reason or a time out, and labels what is missing", () => {
    expect(parseLinkCheck({ state: "gone" })).toEqual({ state: "gone", reason: "", checked_at: null });
    expect(parseLinkCheck({ state: "still_listed", reason: null, checked_at: 5 })).toEqual({ state: "still_listed", reason: "", checked_at: null });
  });

  it.each([null, undefined, "gone", 7, [], [{ state: "gone" }], {}, { state: "closed" }, { state: "GONE" }, { state: null }, { reason: "gone" }])(
    "does not read %j as an answer",
    (body) => {
      expect(parseLinkCheck(body)).toBeNull();
    },
  );
});

describe("a press that got no answer", () => {
  it("words the person's own rate limit the way every other action does, then the suggestion", () => {
    expect(failureWords(new ApiError(429, "server wording", "RATE_LIMITED", true, 725))).toBe(
      `The check didn't go through. You are doing that too often. Try again in about 13 minutes. ${SUGGESTION}`,
    );
    expect(failureWords(new ApiError(429, "Request failed (429)"))).toBe(
      `The check didn't go through. You are doing that too often. Try again in a little while. ${SUGGESTION}`,
    );
  });

  it("says checking is switched off for the 503 that names the kill switch, by reason or by code", () => {
    const off = `Checking is switched off right now. ${SUGGESTION}`;
    expect(failureWords(new ApiError(503, "x", "PROVIDER_UNAVAILABLE", true, undefined, undefined, undefined, undefined, LINK_CHECK_DISABLED))).toBe(off);
    expect(failureWords(new ApiError(503, "x", LINK_CHECK_DISABLED))).toBe(off);
  });

  it("does not call another 503 'switched off': a proxy's or a deploy's is a plain failure in its own words", () => {
    const plain = new ApiError(503, "Service Unavailable");
    expect(isLinkCheckDisabled(plain)).toBe(false);
    expect(failureWords(plain)).toBe(`The check didn't go through. Service Unavailable. ${SUGGESTION}`);
  });

  it("does not take the reason for the kill switch from a status that is not 503", () => {
    expect(isLinkCheckDisabled(new ApiError(404, "x", undefined, undefined, undefined, undefined, undefined, undefined, LINK_CHECK_DISABLED))).toBe(false);
    expect(isLinkCheckDisabled("link_check_disabled")).toBe(false);
    expect(isLinkCheckDisabled(null)).toBe(false);
  });

  it("keeps any other failure's own words, with the full stop it may have lacked", () => {
    expect(failureWords(new ApiError(500, "Request failed (500)"))).toBe(`The check didn't go through. Request failed (500). ${SUGGESTION}`);
    expect(failureWords(new ApiError(404, "Application not found."))).toBe(`The check didn't go through. Application not found. ${SUGGESTION}`);
    expect(failureWords(new TypeError("Failed to fetch"))).toBe(`The check didn't go through. Failed to fetch. ${SUGGESTION}`);
  });

  it("falls back to a plain sentence when the failure has no words, and for a thrown value that is not an Error", () => {
    expect(failureWords(new ApiError(500, ""))).toBe(`The check didn't go through. ${SUGGESTION}`);
    expect(failureWords("boom")).toBe(`The check didn't go through. Something went wrong. ${SUGGESTION}`);
  });

  it("says a reply it cannot read in fixed words", () => {
    expect(UNREADABLE_WORDS).toBe(`The check didn't go through. The answer could not be read. ${SUGGESTION}`);
  });
});

describe("when it was checked", () => {
  // The clock time is the reader's own (as the band's why line writes it), so the tests ask for it rather than
  // assuming a zone. The check and the answer's arrival are a minute apart, so the two can be told apart.
  const clock = (instant: string) => formatTrailTime(instant) as string;
  const result: LinkCheckResult = { state: "still_listed", reason: "", checked_at: "2026-11-04T14:31:58Z" };
  const received = Date.UTC(2026, 10, 4, 14, 32, 7);

  it("says just now for the first minute, by this page's own clock", () => {
    expect(checkedWords(result, received, received)).toBe("Checked just now.");
    expect(checkedWords(result, received, received + 59_999)).toBe("Checked just now.");
  });

  it("then gives the clock time of the check, which stays true however long the page is open", () => {
    const then = `Checked at ${clock("2026-11-04T14:31:58Z")}.`;
    expect(then).toMatch(/^Checked at \d\d:\d\d\.$/);
    expect(checkedWords(result, received, received + 60_000)).toBe(then);
    expect(checkedWords(result, received, received + 6 * 3_600_000)).toBe(then);
  });

  it("falls back to when this page got the answer when the server's time cannot be read", () => {
    const arrived = `Checked at ${clock("2026-11-04T14:32:07Z")}.`;
    expect(checkedWords({ ...result, checked_at: null }, received, received + 120_000)).toBe(arrived);
    expect(checkedWords({ ...result, checked_at: "yesterday" }, received, received + 120_000)).toBe(arrived);
  });
});

describe("the line a card draws", () => {
  const NOW = 1_000_000;

  it("has nothing to say before a press or while the request is out", () => {
    expect(linkCheckLine(undefined, NOW)).toBeNull();
    expect(linkCheckLine({ kind: "pending" }, NOW)).toBeNull();
  });

  it("says when a listed posting was checked, and makes no claim about time for the other answers", () => {
    expect(linkCheckLine(answered({ state: "still_listed" }, NOW), NOW)).toEqual({
      state: "still_listed",
      text: STILL_LISTED_WORDS,
      when: "Checked just now.",
    });
    expect(linkCheckLine(answered({ state: "gone", reason: "absent" }, NOW), NOW + 5_000_000)).toEqual({ state: "gone", text: GONE_WORDS, when: null });
    expect(linkCheckLine(answered({ state: "cant_tell", reason: "blocked" }, NOW), NOW)).toEqual({
      state: "cant_tell",
      text: `The company's site declined the check. ${SUGGESTION}`,
      when: null,
    });
  });

  it("words a press that got no answer as it was worded, and labels it as unanswered", () => {
    expect(linkCheckLine({ kind: "unanswered", words: "No answer." }, NOW)).toEqual({ state: "unanswered", text: "No answer.", when: null });
  });
});
