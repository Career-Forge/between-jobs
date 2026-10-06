import { describe, expect, it } from "vitest";
import {
  analyzeQuestion,
  asJurisdiction,
  classifyQuestionIntent,
  isEligibilityQuestion,
  isJurisdictionSensitive,
  memoryTagsForLookup,
  memoryTagsForSave,
  type QuestionIntent,
} from "@/lib/questionIntent";

describe("classifyQuestionIntent", () => {
  const positives: Array<[QuestionIntent, string[]]> = [
    [
      "why_this_company",
      [
        "Why do you want to work at Acme?",
        "Why do you want to work here?",
        "why do you want to work for Acme Corp?",
        "Why do you want to work with us?",
        "Why do you want to join Stripe?",
        "Why would you like to work at this company?",
        "Why are you interested in working at Datadog?",
        "Why are you interested in joining our team?",
        "Please tell us why you want to work at Acme",
        "Why us?",
        "Why here?",
        "Why this company?",
        "What interests you about working at Acme?",
        "What excites you about our company?",
        "What draws you to our company?",
        "Why do you want to work at Acme? ✱",
        "Ｗｈｙ do you want to work here?", // full-width letters are folded (NFKC) before matching
      ],
    ],
    [
      "tell_us_about_yourself",
      ["Tell us about yourself", "Tell us a little about yourself.", "Please describe yourself", "A brief introduction"],
    ],
    [
      "how_did_you_hear",
      ["How did you hear about us?", "How did you hear about this job?", "Where did you first hear about this role?"],
    ],
    [
      "willing_to_relocate",
      ["Are you willing to relocate?", "Would you be open to relocate?", "Are you open to relocation?"],
    ],
    [
      "work_authorization",
      [
        "Are you legally authorized to work in the United States?",
        "Are you authorised to work in the UK?",
        "Do you have the legal right to work in Canada?",
        "Are you eligible to work in Germany?",
        "What is your work authorization status?",
        "Are you authorized to work in the US without restrictions?",
        "Are you authorized to work in the country where this job is located?",
        "Are you authorized to work here?",
        "Please confirm that you are legally authorized to work in the United States",
        "Do you have the right to work in the UK?",
        "Work authorization",
        "Ａｒｅ you ａｕｔｈｏｒｉｚｅｄ to work in the US?", // full-width letters are folded (NFKC) before matching
      ],
    ],
    [
      "visa_sponsorship",
      [
        "Will you now or in the future require sponsorship?",
        "Do you require visa sponsorship?",
        "Will you need a visa to work for us?",
        "Do you require employment visa sponsorship to work in the US?",
        "Do you now, or will you in the future, require sponsorship for an employment visa?",
        "Will you now or in the future require sponsorship for employment visa status (e.g., H-1B visa status)?",
        "Is sponsorship required?",
        "Do you require sponsorship to work in the United States?",
        "Are you going to require visa sponsorship?",
        "Sponsorship required",
      ],
    ],
  ];

  for (const [intent, labels] of positives) {
    it.each(labels)(`${intent}: %s`, (label) => {
      expect(classifyQuestionIntent(label)).toBe(intent);
    });
  }

  it("two wordings of the same question, at two companies, get the same intent", () => {
    expect(classifyQuestionIntent("Why do you want to work at Acme?")).toBe(
      classifyQuestionIntent("Why do you want to work here?"),
    );
  });

  const none: string[] = [
    "",
    "   ",
    "Why do you want to work in a startup environment?",
    "Why do you want to work for a company like ours?",
    "Why do you want to work in the US?",
    "Why are you interested in this role?",
    "What is your greatest strength?",
    "What are your salary expectations?",
    "When can you start?",
    "How many years of experience do you have with Python?",
    "Are you willing to relocate to New York?",
    "Link to your GitHub profile",
    "Tell us about yourself and why you want to work at Acme",
    "Cover letter",
    // Both asks in one question: one stored answer cannot stand for both.
    "Are you legally authorized to work in the US and will you require sponsorship?",
    // A paragraph is not a question.
    `Why do you want to work at Acme? ${"x ".repeat(100)}`,
    // "Why this company" is about the company, not a topic, a skill or a way of working.
    "What draws you to Acme?", // a bare name after "to" cannot be told from a topic
    "What interests you about machine learning?",
    "What excites you about data engineering?",
    "What excites you about AI?",
    "What draws you to engineering?",
    "What interests you most about fintech?",
    "Why do you want to work with Python?",
    "Why do you want to work with children?",
    "Why do you want to work for startups?",
    "Why do you want to work for remote-first companies?",
    "Why do you want to work at night?",
    // Work eligibility is one short question about the candidate, not any sentence that contains the
    // words.
    "Are you permitted to work from home on Fridays?",
    "Are you eligible to work the overnight on-call shift?",
    "Are you eligible to work in a hybrid arrangement?",
    "Is your current employer authorized to work with government clients?",
    "Do you have experience selling corporate sponsorship packages?",
    "Do you have experience managing corporate sponsorship deals?",
    "Have you worked on event sponsorship?",
    "What is your experience with sponsorship sales?",
    "Do you have experience with H1B sponsorship paperwork?",
    "Are you comfortable with our sponsorship of local sports teams?",
    "Are you open to relocating, and would you need relocation sponsorship?",
    "Do you require sponsorship for your conference travel?",
    "Do you need a visa credit card?",
    "Have you ever been denied sponsorship?",
    "Does your current employer offer sponsorship?",
    "Will you provide sponsorship letters?",
    // A second ask, or a request for something else, riding on the question.
    "Are you authorized to work in the US and are you over 18?",
    "Are you authorized to work in the US and do you have a criminal record?",
    "Are you authorized to work in the US? Describe your most recent salary and compensation history.",
    "Are you authorized to work in the US? Please paste your passport number.",
    "Are you authorized to work in the US. Please paste your passport number",
    "Have you applied before? Are you authorized to work in the US?",
    "Will you require sponsorship? If yes, please enter your SSN",
    "Do you have a referral? Will you now or in the future require sponsorship?",
    // The question has to open like one: a note, a policy line or another request in front of it is not.
    "Note: do you have the legal right to work in Canada?",
    "Describe your work authorization",
    "Our new hires all need work authorization",
    // More than one country, or a place that is not one.
    "Are you authorized to work in the US and in Canada?",
    "Are you authorized to work in Atlantis?",
    "Are you authorized to work in the EU?",
  ];
  it.each(none)("%j has no intent", (label) => {
    expect(classifyQuestionIntent(label)).toBeNull();
  });

  // The guards, each pinned on its own (a regression in one must not hide behind the others).
  it("a label over the length cap has no intent even when it opens like a real work-eligibility question", () => {
    const short = "Are you authorized to work in the United States?";
    expect(classifyQuestionIntent(short)).toBe("work_authorization");
    const long = `${short} ${"Please explain in as much detail as you can. ".repeat(6)}`;
    expect(long.length).toBeGreaterThan(160);
    expect(classifyQuestionIntent(long)).toBeNull();
    expect(classifyQuestionIntent(`Do you require sponsorship? ${"x ".repeat(100)}`)).toBeNull();
    expect(classifyQuestionIntent(`Why do you want to work at Acme? ${"Please be specific. ".repeat(10)}`)).toBeNull();
  });

  it("the cap is exactly 160 characters of normalized text", () => {
    // A sponsorship question padded inside its own parenthetical, so only the length changes.
    const withPad = (pad: number) =>
      `will you now or in the future require an employment visa sponsorship now or in the future for an employment visa status (${"x".repeat(pad)}) in the united states`;
    expect(withPad(17)).toHaveLength(160);
    expect(classifyQuestionIntent(withPad(17))).toBe("visa_sponsorship");
    expect(withPad(18)).toHaveLength(161);
    expect(classifyQuestionIntent(withPad(18))).toBeNull();
    expect(withPad(16).length).toBeLessThan(160);
    expect(classifyQuestionIntent(withPad(16))).toBe("visa_sponsorship");
  });

  it.each([
    "Our company only hires people authorized to work in the country",
    "Candidates must have the legal right to work here",
    "We verify work authorization for every new hire",
    "Describe your experience with corporate sponsorship programs",
    "Our policy is that we do not offer visa sponsorship",
    "We need a visa-free candidate",
  ])("a statement that merely mentions work eligibility is not the question: %s", (label) => {
    expect(classifyQuestionIntent(label)).toBeNull();
  });

  it("full-width and other compatibility forms classify like the plain ones", () => {
    expect(classifyQuestionIntent("Ｗｈｙ do you want to work here?")).toBe("why_this_company");
    expect(classifyQuestionIntent("Ａｒｅ you ａｕｔｈｏｒｉｚｅｄ to work in the US?")).toBe("work_authorization");
  });

  it("a company is called by name only when it is written like a name", () => {
    expect(classifyQuestionIntent("Why do you want to work at Acme?")).toBe("why_this_company");
    expect(classifyQuestionIntent("Why do you want to work at acme?")).toBeNull();
    expect(classifyQuestionIntent("Why do you want to work at 3M?")).toBe("why_this_company");
    expect(classifyQuestionIntent("Why do you want to work at startups?")).toBeNull();
  });

  it("is not fooled into an intent by words that merely sit inside a longer, different question", () => {
    expect(classifyQuestionIntent("Describe a project where you were responsible for work authorization checks")).toBeNull();
  });

  it("stays fast on a hostile label (a bounded pattern set, and a length cap before any pattern runs)", () => {
    const hostile = [
      `why do you want to work at ${"a ".repeat(2000)}`,
      `authorized ${"to work ".repeat(1000)}`,
      "a".repeat(50_000),
      `${"sponsorship ".repeat(500)}`,
    ];
    const start = performance.now();
    for (const label of hostile) classifyQuestionIntent(label);
    expect(performance.now() - start).toBeLessThan(250);
  });

  it("is deterministic and takes only the label: the same text twice is the same answer", () => {
    expect(classifyQuestionIntent("Do you require visa sponsorship?")).toBe("visa_sponsorship");
    expect(classifyQuestionIntent("Do you require visa sponsorship?")).toBe("visa_sponsorship");
  });
});

describe("jurisdiction sensitivity", () => {
  it("only work-eligibility intents are tied to a country", () => {
    expect(isJurisdictionSensitive("work_authorization")).toBe(true);
    expect(isJurisdictionSensitive("visa_sponsorship")).toBe(true);
    expect(isJurisdictionSensitive("why_this_company")).toBe(false);
    expect(isJurisdictionSensitive("willing_to_relocate")).toBe(false);
  });

  it.each([
    ["US", "US"],
    ["GB", "GB"],
    ["us", null],
    ["USA", null],
    ["", null],
    [null, null],
    [undefined, null],
    [42, null],
  ])("asJurisdiction(%j) -> %j", (value, expected) => {
    expect(asJurisdiction(value)).toBe(expected);
  });
});

describe("the country a work-eligibility question names", () => {
  it.each([
    ["Are you legally authorized to work in the United States?", "work_authorization", "US"],
    ["Are you authorized to work in the U.S.?", "work_authorization", "US"],
    ["Are you authorised to work in the UK?", "work_authorization", "GB"],
    ["Are you legally authorized to work in Germany?", "work_authorization", "DE"],
    ["Do you have the legal right to work in Canada?", "work_authorization", "CA"],
    ["Are you eligible to work in India?", "work_authorization", "IN"],
    ["Do you require sponsorship to work in the United States?", "visa_sponsorship", "US"],
    ["Do you require visa sponsorship to work in Germany?", "visa_sponsorship", "DE"],
  ] as const)("%s names %s", (label, intent, country) => {
    expect(analyzeQuestion(label)).toEqual({ intent, namedCountry: country });
  });

  it.each([
    "Are you authorized to work here?",
    "Do you require visa sponsorship?",
    "Are you authorized to work in the country where this job is located?",
    "Work authorization",
  ])("%s names none: the job's country applies", (label) => {
    expect(analyzeQuestion(label)?.namedCountry).toBeNull();
  });

  it("'Georgia' is the country or the US state, which the words do not say: not a named country", () => {
    expect(analyzeQuestion("Are you authorized to work in Georgia?")).toBeNull();
    // ...but it is still about work eligibility, so its answer is tied to the job's country, or not kept.
    expect(memoryTagsForSave("Are you authorized to work in Georgia?", "US")).toEqual({ jurisdiction: "US" });
    expect(memoryTagsForSave("Are you authorized to work in Georgia?", null)).toBeNull();
  });

  it("two countries, or a place that is not one, is not a classified question", () => {
    expect(analyzeQuestion("Are you authorized to work in the US and in Canada?")).toBeNull();
    expect(analyzeQuestion("Are you authorized to work in the EU?")).toBeNull();
  });

  it("the pronoun 'us' is not the United States", () => {
    expect(analyzeQuestion("Will you need a visa to work for us?")).toEqual({ intent: "visa_sponsorship", namedCountry: null });
    expect(analyzeQuestion("Why do you want to work with us?")).toEqual({ intent: "why_this_company", namedCountry: null });
  });
});

describe("what is sent when saving an answer", () => {
  it("a universal answer sends its intent and NO jurisdiction, so it is offered on any company's form", () => {
    expect(memoryTagsForSave("Why do you want to work at Acme?", "US")).toEqual({ canonicalIntent: "why_this_company" });
    expect(memoryTagsForSave("Why do you want to work at Acme?", null)).toEqual({ canonicalIntent: "why_this_company" });
  });

  it("a work-eligibility answer is tagged with the job's country", () => {
    expect(memoryTagsForSave("Are you authorized to work here?", "US")).toEqual({
      canonicalIntent: "work_authorization",
      jurisdiction: "US",
    });
    expect(memoryTagsForSave("Do you require visa sponsorship?", "GB")).toEqual({
      canonicalIntent: "visa_sponsorship",
      jurisdiction: "GB",
    });
  });

  it("...or with the country the question itself names, whatever the job's country is", () => {
    expect(memoryTagsForSave("Are you legally authorized to work in the United States?", "US")).toEqual({
      canonicalIntent: "work_authorization",
      jurisdiction: "US",
    });
    // On a US posting, a question about Germany is an answer about Germany: never filed under the US.
    expect(memoryTagsForSave("Are you legally authorized to work in Germany?", "US")).toEqual({
      canonicalIntent: "work_authorization",
      jurisdiction: "DE",
    });
    // ...and it needs no country from the posting at all.
    expect(memoryTagsForSave("Are you legally authorized to work in Germany?", null)).toEqual({
      canonicalIntent: "work_authorization",
      jurisdiction: "DE",
    });
  });

  it("a work-eligibility answer with no country at all is NOT remembered -- unknown is not a country", () => {
    expect(memoryTagsForSave("Are you authorized to work here?", null)).toBeNull();
    expect(memoryTagsForSave("Do you require visa sponsorship?", null)).toBeNull();
    expect(memoryTagsForSave("Are you authorized to work in the country where this job is located?", null)).toBeNull();
  });

  it("a label that is about work eligibility but not a recognised question is still tied to a country", () => {
    const compound = "Are you authorized to work in the US and are you over 18?";
    expect(memoryTagsForSave(compound, "US")).toEqual({ jurisdiction: "US" }); // exact wording only, and tagged
    expect(memoryTagsForSave(compound, null)).toBeNull();
    expect(memoryTagsForSave("Please describe your work authorization status in detail", "US")).toEqual({ jurisdiction: "US" });
  });

  it("a question with no intent sends neither tag", () => {
    expect(memoryTagsForSave("What is your greatest strength?", "US")).toEqual({});
    // Not about work eligibility, whatever the words look like:
    expect(memoryTagsForSave("Are you permitted to work from home on Fridays?", "US")).toEqual({});
    expect(memoryTagsForSave("Why do you want to work with Python?", "US")).toEqual({});
  });

  it("a topical question never takes an intent that would later serve a real one", () => {
    expect(memoryTagsForSave("What excites you about AI?", "US")).toEqual({});
    expect(memoryTagsForSave("Do you have experience managing corporate sponsorship deals?", "US")).toEqual({
      jurisdiction: "US",
    });
  });
});

describe("what is sent when looking an answer up", () => {
  it("sends the intent and the country together when both are known", () => {
    expect(memoryTagsForLookup("Why do you want to work here?", "US")).toEqual({
      canonicalIntent: "why_this_company",
      jurisdiction: "US",
    });
  });

  it("sends only what is known", () => {
    expect(memoryTagsForLookup("Why do you want to work here?", null)).toEqual({ canonicalIntent: "why_this_company" });
    expect(memoryTagsForLookup("What is your greatest strength?", "US")).toEqual({ jurisdiction: "US" });
    expect(memoryTagsForLookup("What is your greatest strength?", null)).toEqual({});
  });

  it("a work-eligibility question is looked up under the country it names, else the job's", () => {
    expect(memoryTagsForLookup("Are you legally authorized to work in Germany?", "US")).toEqual({
      canonicalIntent: "work_authorization",
      jurisdiction: "DE",
    });
    expect(memoryTagsForLookup("Are you authorized to work here?", "US")).toEqual({
      canonicalIntent: "work_authorization",
      jurisdiction: "US",
    });
    expect(memoryTagsForLookup("Are you legally authorized to work in the United States?", "US")).toEqual({
      canonicalIntent: "work_authorization",
      jurisdiction: "US",
    });
  });

  it("with no country anywhere, only the exact wording is asked for -- no answer is found by alias", () => {
    expect(memoryTagsForLookup("Are you authorized to work here?", null)).toEqual({});
    expect(memoryTagsForLookup("Do you require visa sponsorship?", null)).toEqual({});
  });

  it("a compound work-eligibility label has no intent, so nothing is offered to it by alias", () => {
    const compound = "Are you authorized to work in the US and are you over 18?";
    expect(memoryTagsForLookup(compound, "US")).toEqual({ jurisdiction: "US" });
  });
});

describe("isEligibilityQuestion", () => {
  it.each([
    "Are you authorized to work here?",
    "Do you require visa sponsorship?",
    "Are you authorized to work in the US and are you over 18?",
    "Please describe your work authorization status in detail",
    "Do you have experience selling corporate sponsorship packages?",
    "What type of visa do you hold?",
    "Please describe your immigration status",
    "Do you hold the right to work in the UK or any other country?",
    "Do you hold a work permit?",
  ])("%s", (label) => expect(isEligibilityQuestion(label)).toBe(true));

  it.each([
    "Why do you want to work at Acme?",
    "Why do you want to work at Visa?", // a recognised question of another kind, whatever its words
    "What is your greatest strength?",
    "Are you permitted to work from home on Fridays?",
    "",
  ])("%j is not", (label) => expect(isEligibilityQuestion(label)).toBe(false));

  it("stays fast on a very long label", () => {
    const start = performance.now();
    isEligibilityQuestion("authorized to work ".repeat(100_000));
    expect(performance.now() - start).toBeLessThan(250);
  });
});
