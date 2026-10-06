import { describe, expect, it } from "vitest";
import { PRIVACY_PATH, TERMS_PATH } from "../lib/publicRoutes";
import { columnsOfTable } from "../testing/migrationColumns";
import { OPERATOR_ACCESS_LIMIT, PRIVACY, PROCESSORS, type Block, type Inline } from "./legal";
import { PRIVACY_EMAIL } from "./site";
import { TESTER_AGREEMENT, TESTER_AGREEMENT_VERSION } from "./testerAgreement";

// The Tester Agreement is typed data (testerAgreement.ts), like the Privacy Policy and the Terms
// (legal.ts, pinned by legal.test.ts). This pins its structure, that nothing unfinished is in it,
// that it links only where it should, and that what it says about the database and about the
// Privacy Policy is what those say. The two halves that need the enrollment page and the API are
// in enrollmentWiring.test.ts and tests/test_tester_enrollment.py.

const migrationSources = import.meta.glob("../../../supabase/migrations/*.sql", {
  query: "?raw",
  import: "default",
  eager: true,
}) as Record<string, string>;

const ENROLLMENT_MIGRATION = Object.entries(migrationSources).find(([path]) =>
  path.endsWith("_create_product_events_and_tester_enrollments.sql"),
)?.[1];

function inlineText(part: Inline): string {
  if (typeof part === "string") return part;
  if ("email" in part) return part.email;
  return part.text;
}

function blockTexts(block: Block): string[] {
  switch (block.kind) {
    case "p":
      return [block.inline.map(inlineText).join("")];
    case "h3":
      return [block.text];
    case "ul":
      return block.items.map((item) => item.map(inlineText).join(""));
    case "defs":
      return block.items.flatMap((item) => [item.term, item.detail.map(inlineText).join("")]);
  }
}

function blockInlines(block: Block): Inline[] {
  switch (block.kind) {
    case "p":
      return [...block.inline];
    case "h3":
      return [];
    case "ul":
      return block.items.flatMap((item) => [...item]);
    case "defs":
      return block.items.flatMap((item) => [...item.detail]);
  }
}

const TEXTS = TESTER_AGREEMENT.sections.flatMap((section) => [
  section.heading,
  ...section.blocks.flatMap(blockTexts),
]);
const TEXT = TEXTS.join("\n");
const INLINES = TESTER_AGREEMENT.sections.flatMap((section) => section.blocks.flatMap(blockInlines));

function sectionText(id: string): string {
  const section = TESTER_AGREEMENT.sections.find((candidate) => candidate.id === id);
  if (section === undefined) throw new Error(`no section ${id}`);
  return section.blocks.flatMap(blockTexts).join("\n");
}

describe("the agreement's version", () => {
  it("is the date the text was written, and fits the column that records it", () => {
    expect(TESTER_AGREEMENT_VERSION).toBe("2026-10-06");
    expect(TESTER_AGREEMENT_VERSION).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    // tester_enrollments.consent_version: not empty, at most 40 characters.
    expect(ENROLLMENT_MIGRATION).toBeDefined();
    expect(ENROLLMENT_MIGRATION).toContain("char_length(consent_version) <= 40");
    expect(TESTER_AGREEMENT_VERSION.length).toBeLessThanOrEqual(40);
  });
});

describe("the sections", () => {
  it("are exactly these, in this order", () => {
    expect(TESTER_AGREEMENT.title).toBe("Tester Agreement");
    expect(TESTER_AGREEMENT.sections.map((section) => section.id)).toEqual([
      "what-this-is",
      "what-we-record",
      "sponsorship",
      "who-can-see",
      "gmail",
      "deleting",
      "leaving",
      "ai-keys",
      "no-promises",
      "changes",
    ]);
  });

  it("each has a plain heading, a unique id and some content", () => {
    const ids = new Set<string>();
    for (const section of TESTER_AGREEMENT.sections) {
      expect(section.id).toMatch(/^[a-z]+(-[a-z]+)*$/);
      expect(ids.has(section.id), `duplicate id ${section.id}`).toBe(false);
      ids.add(section.id);
      expect(section.heading.trim().length).toBeGreaterThan(0);
      expect(section.heading.length).toBeLessThanOrEqual(60);
      expect(section.blocks.length).toBeGreaterThan(0);
      for (const block of section.blocks) {
        if (block.kind === "ul") expect(block.items.length).toBeGreaterThan(0);
      }
    }
  });

  // A person is asked to read all of it before joining. The cap was raised from 900 to 1100 on
  // purpose, once, when a review found sentences that said more than the code does (withdrawing
  // and the background work, who reads what, what deletion does not reach) and each had to be
  // corrected by saying more. It is a cap, not a target: shorten before raising it again.
  it("is short: a person is asked to read all of it before joining", () => {
    expect(TEXT.split(/\s+/).length).toBeLessThan(1100);
  });
});

describe("nothing unfinished is in the text", () => {
  it("has no placeholder, marker or draft label", () => {
    expect(TEXT).not.toMatch(/TODO|FIXME|TBD|XXX|lorem|ipsum|placeholder|MAINTAINER/i);
    expect(TEXT).not.toMatch(/\bDRAFT\b/);
    expect(TEXT).not.toMatch(/[[\]]/);
    expect(TEXT).not.toMatch(/\{\{|\}\}|<[A-Za-z/]/);
  });

  it("has no em dash (the project writes ' -- ')", () => {
    expect(TEXT).not.toContain("—");
  });
});

describe("every link", () => {
  it("is an internal route, an https address or one of the contact addresses", () => {
    expect(INLINES.some((part) => typeof part !== "string")).toBe(true);
    for (const part of INLINES) {
      if (typeof part === "string" || "email" in part) continue;
      if ("to" in part) {
        expect([PRIVACY_PATH, TERMS_PATH], part.to).toContain(part.to);
      } else {
        const url = new URL(part.href);
        expect(url.protocol, part.href).toBe("https:");
      }
      expect(part.text.trim().length).toBeGreaterThan(0);
      expect(part.text.toLowerCase()).not.toMatch(/^(here|click|link|this|read more)\b/);
    }
  });

  it("names the Terms and the Privacy Policy, and writes no address by hand", () => {
    const targets = new Set(
      INLINES.filter((part) => typeof part !== "string" && "to" in part).map((part) => (part as { to: string }).to),
    );
    expect(targets).toEqual(new Set([PRIVACY_PATH, TERMS_PATH]));
    const emails = INLINES.filter((part) => typeof part !== "string" && "email" in part).map(
      (part) => (part as { email: string }).email,
    );
    expect(emails).toEqual([PRIVACY_EMAIL]);
    for (const part of INLINES) {
      if (typeof part === "string") expect(part).not.toMatch(/[\w.+-]+@[\w-]+\.[\w.-]+/);
    }
  });
});

describe("what the agreement says", () => {
  it("points at the Privacy Policy for the processors and does not repeat its list", () => {
    expect(sectionText("what-we-record")).toContain("listed in the Privacy Policy under 'Who else handles your data'");
    expect(sectionText("what-we-record")).toContain("Joining the programme adds none");
    for (const processor of PROCESSORS) {
      const escaped = processor.name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
      expect(TEXT, processor.name).not.toMatch(new RegExp(`\\b${escaped}`));
    }
  });

  it("names every kind of thing it records, in the words the migration's columns suggest", () => {
    const recorded = sectionText("what-we-record");
    for (const words of [
      "your role",
      "your seniority",
      "optional sponsorship question",
      "which version of this agreement you accepted and when",
      "the day your record was first made",
      "when you withdrew",
      "Product usage events",
    ]) {
      expect(recorded, words).toContain(words);
    }
  });

  // What the agreement says it records, one entry for every column the table has. The Privacy
  // Policy is held to the same columns (legal.test.ts), so the document a person reads before
  // joining cannot be one column short of the one it points to.
  const COLUMN_WORDS: Record<string, string> = {
    role_cohort: "your role",
    seniority: "your seniority",
    needs_sponsorship: "your answer to the optional sponsorship question",
    consent_version: "which version of this agreement you accepted",
    consented_at: "accepted and when",
    withdrawn_at: "when you withdrew",
    created_at: "the day your record was first made",
  };
  // Tied to the account, which the section's own introduction says.
  const TECHNICAL_COLUMNS = ["user_id"];

  it("says it records every column tester_enrollments has, and a new column fails until it does", () => {
    const columns = columnsOfTable(migrationSources, "tester_enrollments");
    expect(
      [...columns].sort(),
      "tester_enrollments has a column the Tester Agreement does not say it records (or the agreement names one the table no longer has): update 'What we record about you' in testerAgreement.ts and COLUMN_WORDS here, and the Privacy Policy's 'Tester programme' paragraph",
    ).toEqual([...TECHNICAL_COLUMNS, ...Object.keys(COLUMN_WORDS)].sort());
    const recorded = sectionText("what-we-record");
    for (const [column, words] of Object.entries(COLUMN_WORDS)) {
      expect(recorded, `${column} must be described as "${words}"`).toContain(words);
    }
  });

  it("names the seniority bands the database allows", () => {
    expect(ENROLLMENT_MIGRATION).toContain("'new_grad', 'early_career', 'mid', 'senior', 'lead_plus'");
    const recorded = sectionText("what-we-record");
    for (const band of ["new graduate", "early career", "mid-level", "senior", "lead or above"]) {
      expect(recorded, band).toContain(band);
    }
  });

  it("says the sponsorship question is optional, never read as a no, never used by the product, and deleted with the programme data", () => {
    const sponsorship = sectionText("sponsorship");
    expect(sponsorship).toContain("Answering is optional");
    expect(sponsorship).toContain("Prefer not to say");
    expect(sponsorship).toContain("We never read that as a 'no'");
    expect(sponsorship).toContain("The product never uses your answer");
    expect(sponsorship).toContain("deleted with the rest of the programme data");
    // The column's own comment is where each of those is promised.
    expect(ENROLLMENT_MIGRATION).toContain("optional (null = not asked or declined)");
    expect(ENROLLMENT_MIGRATION).toContain("never used for any product logic");
    expect(ENROLLMENT_MIGRATION).toContain("deleted with the rest of the cohort data 30 days after the program ends");
    expect(ENROLLMENT_MIGRATION).toContain("it is never read as \"no\"");
  });

  it("says deletion is immediate through the Profile page or within 7 days by email, as the Privacy Policy does", () => {
    const deleting = sectionText("deleting");
    expect(deleting).toContain("'Delete my account' on the Profile page removes your account and everything tied to it straight away");
    expect(deleting).toContain("We will do it within 7 days.");
    const policy = PRIVACY.sections.find((section) => section.id === "keeping-deleting")?.blocks.flatMap(blockTexts).join("\n");
    expect(policy).toContain("immediately and for good");
    expect(policy).toContain("We will do it within 7 days.");
  });

  it("points at what deletion does not reach, which the Privacy Policy lists, so 'straight away' is not read as 'from backups too'", () => {
    const deleting = sectionText("deleting");
    expect(deleting).toContain("What a deletion does not reach, such as database backups until they expire, is listed in the Privacy Policy under 'Keeping and deleting your data'");
    expect(deleting).toContain("straight away");
    const deletingInlines = TESTER_AGREEMENT.sections
      .find((section) => section.id === "deleting")
      ?.blocks.flatMap(blockInlines);
    expect(deletingInlines).toContainEqual({ to: PRIVACY_PATH, text: "Privacy Policy" });
    // What the pointer promises is in the policy, under the heading the agreement names.
    const policySection = PRIVACY.sections.find((section) => section.id === "keeping-deleting");
    expect(policySection?.heading).toBe("Keeping and deleting your data");
    const policyTexts = policySection?.blocks.flatMap(blockTexts).join("\n");
    expect(policyTexts).toContain("What is not removed when you delete your account");
    expect(policyTexts).toContain("Backups. If our database provider keeps backups, deleted data stays in them until they expire.");
  });

  it("says programme data goes 30 days after the programme ends unless you agree to keep it, and that the account stays", () => {
    const deleting = sectionText("deleting");
    expect(deleting).toContain("30 days after the programme ends, unless you have agreed that we may keep them");
    expect(deleting).toContain("Deleting them does not delete your account");
  });

  it("says the operator can technically read it, and when it would, in exactly the Privacy Policy's words", () => {
    // A tester must not be told a weaker limit than every other user, so the sentence is one
    // constant used by both documents. It is pinned here as a literal too: changing it changes
    // both documents, and means a new version of each.
    const LIMIT =
      "We would look at it only to fix a problem you ask us to help with, to investigate abuse or a security problem, or because the law requires it.";
    expect(OPERATOR_ACCESS_LIMIT).toBe(LIMIT);

    const access = sectionText("who-can-see");
    expect(access).toContain("can technically read everything stored about you");
    expect(access).toContain("We do not read a tester's data as a matter of routine");
    expect(access).toContain(LIMIT);
    const policy = PRIVACY.sections.find((section) => section.id === "operator-access")?.blocks.flatMap(blockTexts).join("\n");
    expect(policy).toContain("We do not read your data as a matter of routine");
    expect(policy).toContain(LIMIT);
    // the looser wording this replaced is not back
    expect(access).not.toContain("when you ask us to");
  });

  it("says what the programme adds to that limit, because the operator's own cohort reports list testers by name and open a flagged resume", () => {
    // One report lists the testers who stopped on a setup step and have not got past it, to
    // contact them; another lists every resume a tester flagged as containing something they never
    // did, to open it against their profile. Neither is a count, so the agreement must not say the
    // reports are counts "not about named people", nor that testers' data is only ever counted.
    const access = sectionText("who-can-see");
    expect(access).toContain("stopped at a setup step and have not got past it");
    expect(access).toContain("contact you to offer help");
    expect(access).toContain("contains something you never did");
    expect(access).toContain("open it and compare it with your profile");
    expect(access).toContain("Those counts involve no resume, job or answer text");
    expect(TEXT).not.toContain("not about named people");
    const recorded = sectionText("what-we-record");
    expect(recorded).toContain("Those reports are counts by role and level");
    expect(recorded).toContain("'Who can see it' says when we look at one tester's records");
  });

  it("says Gmail is optional", () => {
    expect(sectionText("gmail")).toContain("You never have to connect Gmail to be a tester");
  });

  it("says what withdrawing does and does not do", () => {
    const leaving = sectionText("leaving");
    expect(leaving).toContain("marks your enrollment as withdrawn");
    expect(leaving).toContain("It does not delete your account or your data");
    expect(leaving).toContain("the same usage records it keeps for every account");
    // the one count of a withdrawn person that remains (the operator's roster report counts them)
    expect(leaving).toContain("we leave your usage out of the programme's counts, apart from a count of how many people withdrew, by role");
    expect(leaving).not.toContain("leave you out of the programme's reports");
  });

  it("says withdrawing stops them STARTING the costly features, not the background work that runs on their key", () => {
    // The saved-search matcher and the Gmail reply checker run on a schedule and ask nothing about
    // enrollment (api/tester_enrollment.py; tests/test_tester_enrollment_gate.py fails the day one
    // does). So the sentence must not say that withdrawing stops "using" those, and must say how
    // to stop them.
    const leaving = sectionText("leaving");
    expect(leaving).toContain("withdrawing also stops you starting them until you join again");
    expect(leaving).not.toContain("stops you using them");
    expect(leaving).toContain("It does not stop what already runs in the background");
    expect(leaving).toContain("saved searches, which use your AI key");
    expect(leaving).toContain("Gmail reply checking");
    expect(leaving).toContain("Pause or delete a saved search, or disconnect Gmail, on the Integrations page to stop them");
  });

  it("labels the AI key paragraph as applying only if one is given", () => {
    const keys = TESTER_AGREEMENT.sections.find((section) => section.id === "ai-keys");
    expect(keys?.heading).toBe("An AI key from us, if you are given one");
    expect(sectionText("ai-keys")).toContain("This part applies only if we give you an AI key");
    expect(sectionText("ai-keys")).toContain("spending cap");
    expect(sectionText("ai-keys")).toContain("revoke it at any time");
  });

  it("promises no result", () => {
    expect(sectionText("no-promises")).toContain("We make no promise that it will work for you");
    expect(sectionText("no-promises")).toContain("an interview or a job");
  });

  it("says the version is on the page and is the one a record keeps", () => {
    expect(sectionText("changes")).toContain("your enrollment record keeps the version you accepted");
  });
});
