import { describe, expect, it } from "vitest";
import type { StandardFieldSpec } from "@/lib/ats-field-map";
import {
  applyFillPlan,
  applyReactControlledFillPlan,
  attachFile,
  planStandardFieldFills,
  planStandardFieldFillsChecked,
} from "@/lib/standardFields";
import type { ExtensionPersonalInfo } from "@/lib/types";

// E6 -- the standard-field write path used to trust whatever selector list
// it was handed. The signed map is the only thing deciding those selectors,
// and a valid signature proves a map is AUTHENTIC, not that every selector
// in it parses or points at something safe to write.

const INFO: ExtensionPersonalInfo = {
  name: "Jane Doe",
  email: "jane@example.com",
  phone: "+1-555-0100",
  location: { city: "New York", region: "NY", country: "US" },
  linkedin: "https://linkedin.com/in/jane",
  github: "",
  portfolio: "https://jane.dev",
};

const NAME_FIELD: StandardFieldSpec = {
  field: "name",
  selector: 'input[name="name"]',
  strategy: "direct",
  profileFields: ["name"],
};
const EMAIL_FIELD: StandardFieldSpec = {
  field: "email",
  selector: 'input[name="email"]',
  strategy: "direct",
  profileFields: ["email"],
};

function spec(selector: string, field = "custom"): StandardFieldSpec {
  return { field, selector, strategy: "direct", profileFields: ["linkedin"] };
}

function buildForm(): void {
  document.body.innerHTML = `
    <form>
      <input type="text" name="name" />
      <input type="email" name="email" />
      <input type="text" name="urls[LinkedIn]" />
    </form>`;
}

describe("a selector the browser won't parse costs that field, not the fill", () => {
  const BAD_SELECTORS = ["input[", "###", ":::", "div >", ""];

  for (const bad of BAD_SELECTORS) {
    it(`skips ${JSON.stringify(bad)} and still plans every valid field`, () => {
      buildForm();
      const fields = [spec(bad), NAME_FIELD, EMAIL_FIELD];

      expect(() => planStandardFieldFillsChecked(document, INFO, false, fields)).not.toThrow();
      const { plan, invalidSelectors } = planStandardFieldFillsChecked(document, INFO, false, fields);

      expect(invalidSelectors).toEqual([bad]);
      expect(plan.map((p) => p.selector)).toEqual(['input[name="name"]', 'input[name="email"]']);
    });
  }

  it("the plain planStandardFieldFills wrapper doesn't throw either", () => {
    buildForm();
    expect(() => planStandardFieldFills(document, INFO, false, [spec("input["), NAME_FIELD])).not.toThrow();
    expect(planStandardFieldFills(document, INFO, false, [spec("input["), NAME_FIELD])).toHaveLength(1);
  });

  it("apply steps skip a plan item with a broken selector instead of throwing", () => {
    buildForm();
    const plan = [
      { selector: "input[", value: "x" },
      { selector: 'input[name="name"]', value: "Jane Doe" },
    ];
    expect(() => applyFillPlan(document, plan)).not.toThrow();
    expect(applyFillPlan(document, plan)).toEqual(['input[name="name"]']);
    expect(() => applyReactControlledFillPlan(document, plan)).not.toThrow();
  });

  it("reports nothing for a fully valid map", () => {
    buildForm();
    expect(planStandardFieldFillsChecked(document, INFO, false, [NAME_FIELD]).invalidSelectors).toEqual([]);
  });
});

describe("standard-field writes only ever target a plain text-entry control", () => {
  function buildTraps(): void {
    document.body.innerHTML = `
      <form>
        <select name="eeo[country]"><option value="">--</option><option value="United States">United States</option></select>
        <input type="submit" name="go" value="Submit application" />
        <input type="checkbox" name="consent" />
        <input type="radio" name="choice" value="a" />
        <input type="file" name="upload" />
        <input type="text" name="name" />
      </form>`;
  }

  const TRAPS = [
    'select[name="eeo[country]"]',
    'input[name="go"]',
    'input[name="consent"]',
    'input[name="choice"]',
    'input[name="upload"]',
  ];

  for (const selector of TRAPS) {
    it(`never plans ${selector}, even under forceRefillAll`, () => {
      buildTraps();
      const plan = planStandardFieldFills(document, INFO, true, [spec(selector)]);
      expect(plan).toEqual([]);
    });
  }

  it("never applies a hand-built plan item to a select, submit button or checkbox", () => {
    buildTraps();
    const select = document.querySelector<HTMLSelectElement>('select[name="eeo[country]"]')!;
    const submit = document.querySelector<HTMLInputElement>('input[name="go"]')!;
    const checkbox = document.querySelector<HTMLInputElement>('input[name="consent"]')!;
    const plan = [
      { selector: 'select[name="eeo[country]"]', value: "United States" },
      { selector: 'input[name="go"]', value: "Alice Example" },
      { selector: 'input[name="consent"]', value: "on" },
    ];

    expect(applyFillPlan(document, plan)).toEqual([]);
    expect(applyReactControlledFillPlan(document, plan)).toEqual([]);

    expect(select.value).toBe("");
    expect(submit.value).toBe("Submit application");
    expect(checkbox.value).toBe("on");
    expect(checkbox.checked).toBe(false);
  });

  it("still fills the text input alongside the traps", () => {
    buildTraps();
    const plan = planStandardFieldFills(document, INFO, false, [NAME_FIELD, spec('select[name="eeo[country]"]')]);
    expect(plan.map((p) => p.selector)).toEqual(['input[name="name"]']);
  });

  it("fills a textarea, and email/tel/url inputs", () => {
    document.body.innerHTML = `
      <textarea name="a"></textarea><input type="email" name="b" /><input type="tel" name="c" /><input type="url" name="d" />`;
    const fields = ["a", "b", "c", "d"].map((n) => spec(`[name="${n}"]`, n));
    expect(planStandardFieldFills(document, INFO, false, fields)).toHaveLength(4);
  });
});

describe("attachFile only ever targets a file input", () => {
  const bytes = new TextEncoder().encode("%PDF-1.4 fake").buffer;

  it.each([
    ['<input type="text" name="resume" />'],
    ['<select name="resume"></select>'],
    ['<input type="checkbox" name="resume" />'],
  ])("refuses %s and dispatches no change event", (html) => {
    document.body.innerHTML = html;
    const target = document.body.firstElementChild as HTMLInputElement;
    let changed = false;
    target.addEventListener("change", () => {
      changed = true;
    });

    expect(() => attachFile(target, bytes, "resume.pdf", "application/pdf")).toThrow(/file input/);
    expect(changed).toBe(false);
  });
});

// ---- a part the profile can't honestly supply is left empty and reported -----------------

describe("the first/last-name boxes: a part that cannot be known is left empty and reported", () => {
  const FIRST: StandardFieldSpec = { field: "first_name", selector: "#first_name", strategy: "firstNameWord", profileFields: ["name"] };
  const LAST: StandardFieldSpec = { field: "last_name", selector: "#last_name", strategy: "lastNameWord", profileFields: ["name"] };

  function buildNameForm(): void {
    document.body.innerHTML = `<input type="text" id="first_name" /><input type="text" id="last_name" />`;
  }
  const plan = (name: string, force = false) =>
    planStandardFieldFillsChecked(document, { ...INFO, name }, force, [FIRST, LAST]);

  it("an ordinary name fills both and reports nothing", () => {
    buildNameForm();
    const result = plan("Jane Doe");
    expect(result.plan).toEqual([
      { selector: "#first_name", value: "Jane" },
      { selector: "#last_name", value: "Doe" },
    ]);
    expect(result.skipped).toEqual([]);
  });

  it("a one-word name fills the first box and says the last name is unknown", () => {
    buildNameForm();
    const result = plan("Madonna");
    expect(result.plan).toEqual([{ selector: "#first_name", value: "Madonna" }]);
    expect(result.skipped).toEqual([{ field: "Last name", reason: "your profile name has only one part" }]);
  });

  it("a name whose word order the spelling doesn't show fills neither box and says so for both", () => {
    buildNameForm();
    const result = plan("山田 太郎");
    expect(result.plan).toEqual([]);
    expect(result.skipped.map((s) => s.field)).toEqual(["First name", "Last name"]);
    expect(result.skipped[0]?.reason).toMatch(/couldn't tell which part/);
  });

  it("a box that already holds text is the person's: nothing is planned and nothing is reported", () => {
    buildNameForm();
    document.querySelector<HTMLInputElement>("#last_name")!.value = "Typed by hand";
    const result = plan("Madonna");
    expect(result.plan).toEqual([{ selector: "#first_name", value: "Madonna" }]);
    expect(result.skipped).toEqual([]);
  });

  it("a form without the boxes reports nothing", () => {
    document.body.innerHTML = `<input type="text" id="email" />`;
    expect(plan("Madonna").skipped).toEqual([]);
  });

  it("an empty profile name is not a skipped part -- there is simply nothing to fill", () => {
    buildNameForm();
    const result = plan("");
    expect(result.plan).toEqual([]);
    expect(result.skipped).toEqual([]);
  });
});

// ---- a "portfolio or GitHub" box: the label and each link's host decide ---------------------

describe("a 'portfolio or GitHub' box is filled by what the form's label asks for", () => {
  const LINK_BOX: StandardFieldSpec = {
    field: "portfolio",
    selector: 'input[name="urls[Other]"]',
    strategy: "fallback",
    profileFields: ["portfolio", "github"],
  };
  const BOTH = { ...INFO, portfolio: "https://jane.dev", github: "https://github.com/jane" };

  function buildLinkBox(): void {
    document.body.innerHTML = `<input type="text" name="urls[Other]" />`;
  }
  const labelled = (label: string | null) => ({ labelFor: () => label });
  const planFor = (info: ExtensionPersonalInfo, label: string | null | "none") =>
    planStandardFieldFillsChecked(document, info, false, [LINK_BOX], label === "none" ? {} : labelled(label));

  it("a box the organisation labelled 'GitHub URL' gets the GitHub link, not the portfolio the map prefers", () => {
    buildLinkBox();
    const result = planFor(BOTH, "GitHub URL");
    expect(result.plan).toEqual([{ selector: 'input[name="urls[Other]"]', value: "https://github.com/jane" }]);
  });

  it("...whichever profile field the GitHub address was typed into", () => {
    buildLinkBox();
    const swapped = { ...INFO, portfolio: "https://github.com/jane", github: "https://jane.dev" };
    expect(planFor(swapped, "GitHub URL").plan[0]?.value).toBe("https://github.com/jane");
  });

  it("'GitHub URL' with no GitHub link in the profile: left empty, and the person is told", () => {
    buildLinkBox();
    const result = planFor({ ...INFO, portfolio: "https://jane.dev", github: "" }, "GitHub URL");
    expect(result.plan).toEqual([]);
    expect(result.skipped).toEqual([
      { field: "GitHub link", reason: "the form asks for a GitHub link and your profile has none" },
    ]);
  });

  it("a box labelled 'Portfolio URL' or 'Other website' gets the real website", () => {
    buildLinkBox();
    expect(planFor(BOTH, "Portfolio URL").plan[0]?.value).toBe("https://jane.dev");
    expect(planFor(BOTH, "Other website").plan[0]?.value).toBe("https://jane.dev");
  });

  it("a box whose label names both, neither, or can't be read keeps the map's own preference", () => {
    buildLinkBox();
    expect(planFor(BOTH, "Other (portfolio, GitHub etc)").plan[0]?.value).toBe("https://jane.dev");
    expect(planFor(BOTH, "Link").plan[0]?.value).toBe("https://jane.dev");
    expect(planFor(BOTH, null).plan[0]?.value).toBe("https://jane.dev");
    expect(planFor(BOTH, "none").plan[0]?.value).toBe("https://jane.dev"); // no label reader at all
  });

  it("a label reader that throws (a malformed selector in a signed map) counts as no label", () => {
    buildLinkBox();
    const result = planStandardFieldFillsChecked(document, BOTH, false, [LINK_BOX], {
      labelFor: () => {
        throw new SyntaxError("bad selector");
      },
    });
    expect(result.plan[0]?.value).toBe("https://jane.dev");
  });

  it("a spec that is not a portfolio-or-GitHub choice is untouched by any label", () => {
    buildLinkBox();
    const direct: StandardFieldSpec = { ...LINK_BOX, strategy: "direct", profileFields: ["portfolio"] };
    const result = planStandardFieldFillsChecked(document, BOTH, false, [direct], labelled("GitHub URL"));
    expect(result.plan[0]?.value).toBe("https://jane.dev");
  });

  // Each clause of "this is a portfolio-or-GitHub choice" is pinned: a spec that differs from it
  // in any one way is read by its own strategy, whatever the label says.
  describe("only exactly a portfolio-or-GitHub fallback is a link choice", () => {
    const withLinks = { ...INFO, portfolio: "https://jane.dev", github: "", linkedin: "https://linkedin.com/in/jane" };
    const only = (spec: StandardFieldSpec) => planStandardFieldFillsChecked(document, withLinks, false, [spec], labelled("GitHub URL"));

    it("a direct spec over both fields reads its first field", () => {
      buildLinkBox();
      const result = only({ ...LINK_BOX, strategy: "direct", profileFields: ["portfolio", "github"] });
      expect(result.plan[0]?.value).toBe("https://jane.dev");
      expect(result.skipped).toEqual([]);
    });

    it("a fallback over two fields that are not portfolio and GitHub is an ordinary fallback", () => {
      buildLinkBox();
      expect(only({ ...LINK_BOX, profileFields: ["portfolio", "linkedin"] }).plan[0]?.value).toBe("https://jane.dev");
      expect(only({ ...LINK_BOX, profileFields: ["github", "linkedin"] }).plan[0]?.value).toBe("https://linkedin.com/in/jane");
      expect(only({ ...LINK_BOX, profileFields: ["portfolio", "portfolio"] }).plan[0]?.value).toBe("https://jane.dev");
    });

    it("a fallback over portfolio, GitHub and a third field is an ordinary fallback", () => {
      buildLinkBox();
      const result = only({ ...LINK_BOX, profileFields: ["portfolio", "github", "linkedin"] });
      expect(result.plan[0]?.value).toBe("https://jane.dev");
      expect(result.skipped).toEqual([]);
    });

    it("a fallback over portfolio alone is an ordinary fallback", () => {
      buildLinkBox();
      expect(only({ ...LINK_BOX, profileFields: ["portfolio"] }).plan[0]?.value).toBe("https://jane.dev");
    });
  });

  it("D5: a box that already holds text is not read, planned or reported", () => {
    buildLinkBox();
    document.querySelector<HTMLInputElement>("input")!.value = "https://already.example";
    const result = planFor({ ...INFO, portfolio: "https://jane.dev", github: "" }, "GitHub URL");
    expect(result.plan).toEqual([]);
    expect(result.skipped).toEqual([]);
  });
});
