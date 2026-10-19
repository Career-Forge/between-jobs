import { describe, expect, it } from "vitest";
import type { InterviewQuestion, InterviewSession } from "./interviewPracticeTypes";
import { answerTrail, briefTrail, contactsTrail, draftTrail, eventsTrail, failedTrail, hiringResultsTrail, hiringSavedTrail, intelTrail, sessionTrail } from "./researchTrails";
import { buildReceipt, buildTrail, findForbiddenWord, type TrailInput } from "./trail";
import type { ResearchRun } from "./research";

const NOW = new Date("2026-09-22T12:00:00Z");
const RUN: ResearchRun = { id: "0123456789abcdef", company_name: "Northwind Labs", providers_used: ["you_com"], warnings: [], created_at: "2026-09-20T12:00:00Z" };
const SESSION: InterviewSession = { id: "s1234567890", user_id: "u", application_id: "a", company_name: "Northwind", resume_evidence: "", registry_entry_id: null, status: "in_progress", started_at: "2026-09-21T12:00:00Z", completed_at: null };
const QUESTION = { id: "q1abcdefgh", answered_at: "2026-09-21T12:10:00Z", score: 7 } as const;

const words = (input: TrailInput) => buildTrail(input)?.text ?? null;
const receiptText = (rows: Parameters<typeof buildReceipt>[0]) => (buildReceipt(rows)?.rows ?? []).map((row) => ("note" in row ? row.note : `${row.label} ${row.value}`));

describe("a research run's trail", () => {
  it("names who acted, from what and when, only from the run's fields", () => {
    expect(words(intelTrail(RUN, 4, NOW, "UTC").trail)).toBe("Claims written by your model · from You.com results, on your keys · Sep 20");
    expect(words(contactsTrail(RUN, 4, NOW, "UTC").trail)).toBe("People named by your model · from You.com results, on your keys · Sep 20");
    expect(words(eventsTrail(RUN, 4, NOW, "UTC").trail)).toBe("Events picked out by your model · from You.com results, on your keys · Sep 20");
  });

  it("leaves a slot and its separator out when its field is missing: no provider, no readable date", () => {
    const bare = { ...RUN, providers_used: [], created_at: "" };
    expect(words(intelTrail(bare, 1, NOW).trail)).toBe("Claims written by your model · on your keys");
  });

  it("carries the failed searches as its one other glyph, and the model glyph first: two colored glyphs at most", () => {
    const trail = buildTrail(intelTrail({ ...RUN, warnings: ["a", "b"] }, 1, NOW, "UTC").trail)!;
    expect(trail.text).toBe("Claims written by your model · from You.com results, on your keys · Sep 20 · 2 searches failed or skipped");
    expect(trail.glyphs).toEqual(["model", "failed"]);
  });

  it("draws a run with nothing in it as plain code that searched: no model glyph, no claim of results", () => {
    const run = { ...RUN, warnings: ["a", "b", "c", "d", "e", "f", "g"] };
    for (const parts of [intelTrail(run, 0, NOW, "UTC"), contactsTrail(run, 0, NOW, "UTC"), eventsTrail(run, 0, NOW, "UTC")]) {
      const trail = buildTrail(parts.trail)!;
      expect(trail.text).toBe("Searched with You.com · on your keys · Sep 20 · 7 searches failed or skipped");
      expect(trail.glyphs).toEqual(["code", "failed"]);
    }
    expect(receiptText(intelTrail(run, 0, NOW).receipt).join(" ")).not.toContain("Each claim links");
  });

  it("keeps a bare run with nothing in it honest: no provider means no 'searched with' and no keys", () => {
    const bare = { ...RUN, providers_used: [], warnings: ["a"] };
    expect(words(intelTrail(bare, 0, NOW, "UTC").trail)).toBe("Sep 20 · 1 search failed or skipped");
  });

  it("does not put GitHub's results on the person's keys", () => {
    const mixed = { ...RUN, providers_used: ["you_com", "github"] };
    expect(words(contactsTrail(mixed, 2, NOW, "UTC").trail)).toBe("People named by your model · from You.com and GitHub results · Sep 20");
    expect(receiptText(contactsTrail(mixed, 2, NOW).receipt).join(" ")).toContain("GitHub's public member list needs no key");
    expect(words(contactsTrail({ ...RUN, providers_used: ["github"] }, 0, NOW, "UTC").trail)).toBe("Searched with GitHub · Sep 20");
    expect(receiptText(contactsTrail(RUN, 2, NOW).receipt).join(" ")).not.toContain("GitHub");
  });

  it("never names a model, never says verified and never says unknown", () => {
    for (const parts of [intelTrail(RUN, 1, NOW), contactsTrail(RUN, 1, NOW), eventsTrail(RUN, 1, NOW), draftTrail({ created_at: RUN.created_at }, NOW), briefTrail({ id: "b", created_at: RUN.created_at, rubric_warnings: [] }, NOW), answerTrail(QUESTION, NOW, true), sessionTrail(SESSION, [], NOW)]) {
      expect(findForbiddenWord(words(parts.trail) ?? "")).toBeNull();
      expect(words(parts.trail)).not.toMatch(/gpt|claude|gemini|llama|deepseek/i);
    }
  });

  it("builds a receipt from the run's own fields and the way each kind of output was checked", () => {
    const intel = receiptText(intelTrail({ ...RUN, warnings: ["x"] }, 3, NOW).receipt);
    expect(intel).toEqual(expect.arrayContaining(["company Northwind Labs", "searched with You.com", "claims 3", "searches failed or skipped 1", "run 01234567"]));
    expect(receiptText(contactsTrail(RUN, 2, NOW).receipt).join(" ")).toContain("Code checked that each name appears");
    expect(receiptText(eventsTrail(RUN, 2, NOW).receipt).join(" ")).toContain("No one's attendance is guessed.");
  });
});

describe("a failed block's trail", () => {
  it("is the red cross and the sentence, with nothing to open", () => {
    const parts = failedTrail("Events couldn't be read");
    const trail = buildTrail(parts.trail)!;
    expect(trail.text).toBe("Events couldn't be read");
    expect(trail.glyphs).toEqual(["failed"]);
    expect(buildReceipt(parts.receipt)).toBeNull();
  });
});

describe("an outreach draft's trail", () => {
  it("says your model drafted it, on your key, with the row's own time and no sending", () => {
    const draft = { created_at: "2026-09-20T12:00:00Z" };
    expect(words(draftTrail(draft, NOW, "UTC").trail)).toBe("Drafted by your model · on your model key · Sep 20");
    const receipt = receiptText(draftTrail(draft, NOW, "UTC").receipt).join(" ");
    expect(receipt).toContain("Nothing is sent.");
    expect(receipt).not.toContain("No time came back");
  });

  it("leaves the time out, and says nothing of it, for a row with no created_at", () => {
    expect(words(draftTrail({}, NOW).trail)).toBe("Drafted by your model · on your model key");
    expect(words(draftTrail({ created_at: "" }, NOW).trail)).toBe("Drafted by your model · on your model key");
    expect(receiptText(draftTrail({}, NOW).receipt).join(" ")).not.toContain("No time came back");
  });
});

describe("practice trails", () => {
  it("gives an answer the time the server stored it, and none for an answer made in this visit", () => {
    expect(words(answerTrail(QUESTION, NOW, true, "UTC").trail)).toBe("Scored by your model · on your model key · Sep 21");
    expect(words(answerTrail(QUESTION, NOW, false, "UTC").trail)).toBe("Scored by your model · on your model key");
  });

  it("leaves a stored time out when it cannot be read", () => {
    expect(words(answerTrail({ ...QUESTION, answered_at: "never" }, NOW, true).trail)).toBe("Scored by your model · on your model key");
  });

  it("says a session's questions are the model's, from the person's own evidence, with the day it started", () => {
    expect(words(sessionTrail(SESSION, [], NOW, "UTC").trail)).toBe("Questions asked by your model · from your resume evidence · Sep 21");
  });

  it("says grounding research is shared across users, and only when a question or the session is grounded", () => {
    const plain = receiptText(sessionTrail(SESSION, [{ grounded_in: null } as InterviewQuestion], NOW).receipt).join(" ");
    expect(plain).not.toContain("shared across users");
    const byQuestion = receiptText(sessionTrail(SESSION, [{ grounded_in: "Two rounds" } as InterviewQuestion], NOW).receipt).join(" ");
    expect(byQuestion).toContain("shared across users");
    expect(byQuestion).toContain("another person's model key");
    expect(receiptText(sessionTrail({ ...SESSION, registry_entry_id: "r1" }, [], NOW).receipt).join(" ")).toContain("shared across users");
  });
});

describe("Your play's trail", () => {
  it("names the model, the day it was built and how many the code flagged", () => {
    expect(words(briefTrail({ id: "b1", created_at: "2026-09-20T12:00:00Z", rubric_warnings: [] }, NOW, "UTC").trail)).toBe("Written by your model · on your model key · Sep 20");
    expect(words(briefTrail({ id: "b1", created_at: "2026-09-20T12:00:00Z", rubric_warnings: ["x"] }, NOW, "UTC").trail)).toBe("Written by your model · on your model key · Sep 20 · 1 flagged");
  });
});

describe("hiring posts trails", () => {
  it("says the person's own search provider found the posts, code tagged them and no model was involved", () => {
    const parts = hiringResultsTrail({ provider: "brave", cached: false, signals: [] });
    expect(words(parts.trail)).toBe("via Brave Search · tagged by code, no model involved");
    expect(buildTrail(parts.trail)!.glyphs).toEqual(["code"]);
  });

  it("names a cached result, and a provider this page does not know as the person's own", () => {
    expect(words(hiringResultsTrail({ provider: null, cached: true, signals: [] }).trail)).toBe("via your search provider · tagged by code, no model involved · cached result");
  });

  it("says the person saved their posts, with the count", () => {
    expect(words(hiringSavedTrail(1).trail)).toBe("You saved these · 1 post");
    expect(words(hiringSavedTrail(3).trail)).toBe("You saved these · 3 posts");
    expect(buildTrail(hiringSavedTrail(1).trail)!.glyphs).toEqual(["you"]);
  });
});
