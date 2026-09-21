import { describe, expect, it } from "vitest";
import type { CanonicalProfile } from "./profileTypes";
import {
  WORK_AUTHORIZATION_INDIA_NOTE,
  WORK_AUTHORIZATION_REGION_EXAMPLES,
  applyWorkAuthorization,
} from "./workAuthorization";

// The exact wording here is the actual content deliverable of this feature
// (work-authorization-status.md) -- these tests pin it verbatim so a future
// edit that softens, "improves," or drops a region can't slip through
// unnoticed. The terminology traps the plan doc calls out by name are
// checked directly, not just implied by the exact-string match.

function regionByName(name: string) {
  const region = WORK_AUTHORIZATION_REGION_EXAMPLES.find((r) => r.region === name);
  if (!region) throw new Error(`no region named ${name}`);
  return region;
}

describe("WORK_AUTHORIZATION_REGION_EXAMPLES", () => {
  it("covers exactly the four regions the plan calls for, in order", () => {
    expect(WORK_AUTHORIZATION_REGION_EXAMPLES.map((r) => r.region)).toEqual([
      "United States",
      "Canada",
      "EU / EEA",
      "India",
    ]);
  });

  it("reproduces the US example sentence verbatim", () => {
    expect(regionByName("United States").sentences).toEqual([
      "I'm on F-1 OPT authorization and will need H-1B sponsorship to continue working in the US after it expires.",
    ]);
  });

  it("reproduces the Canada example sentence verbatim, and it never says 'sponsorship'", () => {
    const [sentence] = regionByName("Canada").sentences;
    expect(sentence).toBe(
      "I hold an open Post-Graduation Work Permit valid until March 2028, so I can work for any Canadian employer with no LMIA needed.",
    );
    // Canada's real axis is open-vs-employer-specific work authorization, not
    // "sponsorship" -- that word means family-class immigration there.
    expect(sentence.toLowerCase()).not.toContain("sponsorship");
  });

  it("reproduces the EU / EEA example sentence verbatim, and it never mentions the UK", () => {
    const [sentence] = regionByName("EU / EEA").sentences;
    expect(sentence).toBe(
      "I'm an Indian citizen currently on an EU Blue Card tied to my employer in Munich, Germany -- it can transfer to a new employer without restarting the process.",
    );
    expect(sentence.toLowerCase()).not.toContain("uk");
    expect(sentence.toLowerCase()).not.toContain("united kingdom");
  });

  it("reproduces both real India situations verbatim, never collapsed into one", () => {
    expect(regionByName("India").sentences).toEqual([
      "I'm an Indian citizen based in India -- no work authorization is needed for Indian roles, including at multinational employers.",
      "I hold an OCI card and can work for any private employer in India without sponsorship, though I can't hold Indian government positions.",
    ]);
  });

  it("never uses 'E-Visa' or mentions NRI status anywhere", () => {
    const all = WORK_AUTHORIZATION_REGION_EXAMPLES.flatMap((r) => r.sentences).join(" ");
    expect(all.toLowerCase()).not.toContain("e-visa");
    expect(all.toLowerCase()).not.toContain("nri");
  });
});

describe("WORK_AUTHORIZATION_INDIA_NOTE", () => {
  it("names the real condition instead of reassuring an India-based user it doesn't apply", () => {
    expect(WORK_AUTHORIZATION_INDIA_NOTE.length).toBeGreaterThan(0);
    expect(WORK_AUTHORIZATION_INDIA_NOTE.toLowerCase()).not.toContain("usually doesn't apply");
    expect(WORK_AUTHORIZATION_INDIA_NOTE.toLowerCase()).not.toContain("nri");
    expect(WORK_AUTHORIZATION_INDIA_NOTE.toLowerCase()).not.toContain("e-visa");
  });

  it("mentions both that Indian employers often skip it and that multinationals often don't", () => {
    const lower = WORK_AUTHORIZATION_INDIA_NOTE.toLowerCase();
    expect(lower).toContain("multinational");
    expect(lower).toMatch(/india|indian/);
  });
});

describe("applyWorkAuthorization", () => {
  function sampleProfile(): CanonicalProfile {
    return {
      personal: {
        name: "Asha Verma",
        headline: "Backend Engineer",
        work_authorization: "",
      },
      experience: [
        {
          title: "Engineer",
          company: "Example Corp",
          start_date: "2022-01",
          end_date: "present",
        },
      ],
      skills: { programming: ["Python"], ai_ml: [], data_mlops: [], cloud_devops: [], tools: [], other: [] },
    };
  }

  it("sets only personal.work_authorization, leaving every other field's identity untouched", () => {
    const profile = sampleProfile();
    const result = applyWorkAuthorization(profile, "a new self-report");

    expect(result.personal.work_authorization).toBe("a new self-report");
    expect(result.personal.name).toBe(profile.personal.name);
    expect(result.personal.headline).toBe(profile.personal.headline);
    // Every field this mutation doesn't touch keeps its exact object
    // identity -- proof nothing else in the profile moved.
    expect(result.experience).toBe(profile.experience);
    expect(result.skills).toBe(profile.skills);
    expect(result).not.toBe(profile);
    expect(result.personal).not.toBe(profile.personal);
  });

  it("works from an empty/undefined starting value with no crash", () => {
    const profile = sampleProfile();
    delete (profile.personal as { work_authorization?: string }).work_authorization;
    const result = applyWorkAuthorization(profile, "");
    expect(result.personal.work_authorization).toBe("");
  });
});
