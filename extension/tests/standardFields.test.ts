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
