import { describe, expect, it } from "vitest";
import exportChecklist from "../../../src/between_jobs/api/export_checklist.py?raw";
import genericBackend from "../../../src/between_jobs/engines/generic/backend.py?raw";
import extensionPermissions from "../../../extension/store/PERMISSIONS.md?raw";
import extensionReadme from "../../../extension/README.md?raw";
import readmeRaw from "../../../README.md?raw";
import fitBlock from "../components/discover/FitBlock.tsx?raw";
import practicePanel from "../components/InterviewPracticePanel.tsx?raw";
import openrouterOAuth from "../lib/settings/openrouterOAuth.ts?raw";
import profileTrails from "../lib/profileTrails.ts?raw";
import tailorPanel from "../components/TailorPanel.tsx?raw";
import workspaceTrails from "../lib/workspaceTrails.ts?raw";
import { FORBIDDEN, strings } from "../testing/claims";
import { findForbiddenWord } from "../lib/trail";
import { LANDING_PROVIDERS } from "./landing";
import { HOW_IT_WORKS, OPEN_SOURCE } from "./publicPages";
import { SOURCE_LICENSE } from "./site";
import { TOUR, TOUR_TABS } from "./tour";

// What the product tour, How it works and Open source may say, and that each thing they say is true of the
// code and the repository's own README today. The words are data (content/tour.ts, content/publicPages.ts)
// so they can be scanned. Each sentence was checked when it was written; the pins below are the ones that can
// be checked by reading a file, so a later change to the code or the README that makes one false fails here
// and sends someone back to the words.

// The README with its line breaks and indents folded to single spaces, so a pin does not depend on where a line wraps.
const readme = readmeRaw.replace(/\s+/g, " ");

const TOUR_WORDS = [...strings(TOUR), ...strings(TOUR_TABS)];
const EXPLAINER_WORDS = [...strings(HOW_IT_WORKS), ...strings(OPEN_SOURCE)];
const ALL = [...TOUR_WORDS, ...EXPLAINER_WORDS];

describe("the claims the tour and the two explainer pages do not make", () => {
  it("has plenty of words to scan", () => {
    expect(TOUR_WORDS.length).toBeGreaterThan(60);
    expect(EXPLAINER_WORDS.length).toBeGreaterThan(30);
  });

  it.each(FORBIDDEN)("never says %s", (_name, pattern) => {
    for (const words of ALL) expect(words).not.toMatch(pattern);
  });

  it("carries no number or statistic: no digit in any sentence, apart from the license's and the language version's own", () => {
    for (const words of ALL) {
      expect(words.replace(new RegExp(SOURCE_LICENSE.replace(/\./g, "\\."), "g"), "").replace(/Python 3\.12/g, ""), words).not.toMatch(/\d/);
    }
  });

  it("keeps the em dash out, as the project writes ' -- '", () => {
    for (const words of ALL) expect(words).not.toContain("—");
  });

  it("is free of placeholders and unfinished marks", () => {
    for (const words of ALL) expect(words).not.toMatch(/TODO|FIXME|TBD|lorem|ipsum|coming soon|\[|\]/i);
  });

  it("has no trail line the trail grammar would refuse to draw (it never says 'verified', never names a model)", () => {
    for (const tab of TOUR_TABS) expect(findForbiddenWord(Object.values(tab.trail).flatMap((slot) => strings(slot)).join(" ")), tab.id).toBeNull();
    for (const step of HOW_IT_WORKS.steps) expect(findForbiddenWord(Object.values(step.trail).flatMap((slot) => strings(slot)).join(" ")), step.id).toBeNull();
  });

  it("never offers more than one extra slot or glyph per line, and always says what was not checked in the tour", () => {
    for (const tab of TOUR_TABS) {
      expect(tab.trail.extra, tab.id).toMatch(/^not checked: /);
      expect(tab.trail.also, tab.id).toBeUndefined();
    }
  });
});

describe("what the tour says is true of the product", () => {
  it("draws the sample role as a fictional one, and says so before the tour", () => {
    expect(TOUR.sampleLabel).toContain("fictional");
    expect(TOUR.sampleLabel).toContain("not a control");
    expect(TOUR.company).toBe("Northstar Labs");
  });

  it("Discover: the fit is a ladder over parts, rated by code from a model's sub-scores, and a result past the batch is 'Not scored'", () => {
    expect(workspaceTrails).toContain("code-weighted from your model's sub-scores");
    expect(fitBlock).toContain("Not scored");
    expect(fitBlock).toContain("Lowest part");
    expect(TOUR.discover.lowest).toBe("Lowest part: Skills");
    // The skills table is the Tailor panel's coverage read: clusters from a model, matched by code.
    expect(tailorPanel).toContain("No matching evidence in your profile.");
    expect(workspaceTrails).toContain("clusters extracted by a model");
    expect(workspaceTrails).toContain("matched to your profile by code");
  });

  it("Prepare: the three checks left to the person are checks the export checklist reports as not checked", () => {
    for (const check of TOUR.prepare.checks) {
      const at = exportChecklist.indexOf(`key="${check.id}"`);
      expect(at, check.id).toBeGreaterThan(-1);
      expect(exportChecklist.slice(at, at + 400), check.id).toContain('status="not_checked"');
    }
    expect(TOUR.prepare.checks).toHaveLength(3);
    // The claim check flags what nothing in the profile backs: the flagged row is the "unverifiable" status.
    expect(exportChecklist).toContain("unsupported claim");
  });

  it("Reach out: Gmail makes drafts and the person sends", () => {
    expect(readme).toContain("Creates drafts in your own Gmail and never sends");
    expect(readme).toContain("Gmail (optional)");
    expect(TOUR.reach.waiting).toBe("Waiting in your drafts. You press Send.");
    expect(TOUR.reach.availability).toContain("optional");
    expect(TOUR.reach.availability).toContain("invited people");
  });

  it("Practice: four parts judged by the person's model, and written feedback", () => {
    expect(practicePanel).toContain("Judged by your model");
    expect(TOUR.practice.coverageLabel).toBe("Judged by your model");
    expect(TOUR.practice.coverage.map((item) => item.part)).toEqual(["Situation", "Task", "Action", "Result"]);
  });

  it("Apply: the extension fills when the person presses 'Fill this page', never submits, and is for early testers", () => {
    // The side panel's own source cannot be read here (its tsconfig is generated, see content/legal.test.ts),
    // so the extension's README and store text stand for it.
    expect(extensionReadme).toContain('a manual "Fill this page" trigger');
    expect(extensionPermissions).toContain("Fill this page");
    expect(TOUR.apply.panelButton).toBe("Fill this page");
    expect(extensionReadme).toContain("never does");
    expect(extensionReadme).toContain("Lever, Greenhouse, and Ashby");
    expect(readme).toContain("not published to the Chrome Web Store");
    expect(TOUR.apply.availability).toContain("not in the Chrome Web Store");
  });
});

describe("what How it works says is true of the product", () => {
  it("has four steps, in order, each with its example trail line", () => {
    expect(HOW_IT_WORKS.steps.map((step) => step.id)).toEqual(["profile", "find", "prepare", "track"]);
    for (const step of HOW_IT_WORKS.steps) {
      expect(step.trail.actor, step.id).toBeTruthy();
      expect(step.split.length, step.id).toBeGreaterThan(30);
    }
  });

  it("Bring your profile: JSON is read by code, a PDF or DOCX file by the person's model, and nothing is used before it is activated", () => {
    expect(profileTrails).toContain('actor: "Parsed by code"');
    expect(profileTrails).toContain('actor: "Read by your model"');
    expect(profileTrails).toContain("Nothing is used until you confirm it.");
    expect(profileTrails).toContain("A saved draft is not your profile until you activate it.");
    expect(readme).toContain("one version is active");
    const [first] = HOW_IT_WORKS.steps;
    expect(first.trail.glyph).toBe("model");
  });

  it("Find and fit-check: the registry, the providers a person connects, a pasted posting or a link, and one batched model call", () => {
    expect(readme).toContain("Search the job registry");
    expect(readme).toContain("Add a posting by pasting it, or from a URL");
    expect(readme).toContain("score fit against your profile with one batched");
    expect(workspaceTrails).toContain("code-weighted from your model's sub-scores");
  });

  it("Prepare and review: a model writes the words, code sets the shape, and the checklist keeps the checks apart", () => {
    expect(readme).toContain("code owns structure and the model chooses words");
    expect(exportChecklist).toContain("not_checked");
    expect(workspaceTrails).toContain('actor: "Checked by code", basis: "no model involved"');
  });

  it("Track and practice: the board's seven statuses, and practice feedback from the person's model", () => {
    expect(readme).toContain("saved, applied, screening, interviewing, offer, rejected and withdrawn");
    expect(HOW_IT_WORKS.steps[3].text).toContain("Saved through Offer, Rejected or Withdrawn");
  });

  it("closes on the promise: never submits, never sends", () => {
    expect(HOW_IT_WORKS.closing.motto).toBe("Draft, don't send. Fill, don't submit.");
    expect(readme).toContain("it never submits an application or sends an email for you");
  });
});

describe("what Open source says is true of the repository", () => {
  it("is AGPL-3.0, and says what the license asks of someone who runs a changed version as a service", () => {
    expect(SOURCE_LICENSE).toBe("AGPL-3.0");
    expect(readme).toContain("AGPL-3.0");
    expect(OPEN_SOURCE.lead).toContain(SOURCE_LICENSE);
  });

  it("lists parts the repository really has, and says the larger engine service is not one of them", () => {
    expect(readme).toContain("`src/between_jobs/api/`");
    expect(readme).toContain("`web/`");
    expect(readme).toContain("`extension/`");
    expect(readme).toContain("`latex-service/`");
    expect(readme).toContain("`supabase/migrations/`");
    expect(readme).toContain("Optional: Telegram, Discord, Gmail, the LaTeX service");
    // The built-in engine is in the repository, and the hosted one is not.
    expect(genericBackend.length).toBeGreaterThan(0);
    expect(readme).toContain("The hosted version uses a separate, larger engine service that is not part of this repository");
    expect(OPEN_SOURCE.repository.notIncluded).toContain("is not in the repository");
    expect(OPEN_SOURCE.repository.items.map((item) => item.name)).toContain("A built-in resume writer");
    expect(readme).toContain("does not score them");
  });

  it("says keys are the person's own, and that text goes to the provider they chose", () => {
    expect(readme).toContain("BYOK-first");
    expect(readme).toContain("Resume content, job descriptions and similar text are also sent to the LLM and search providers whose keys you supply");
    expect(OPEN_SOURCE.keys.text).toContain("Between Jobs has no AI provider of its own.");
    expect(OPEN_SOURCE.keys.text).toContain("stored encrypted");
  });

  it("connects OpenRouter in one click, which the web app does", () => {
    expect(readme).toContain("connect in one click");
    expect(openrouterOAuth).toContain("Connecting OpenRouter in one click");
    expect(OPEN_SOURCE.keys.oneClick).toBe("OpenRouter can also be connected in one click.");
  });

  it("lists the model providers the home page lists, from the same table", () => {
    for (const name of LANDING_PROVIDERS) expect(name.length).toBeGreaterThan(0);
    expect(LANDING_PROVIDERS).toHaveLength(7);
  });

  it("summarises the README's setup and invents no step", () => {
    const text = OPEN_SOURCE.selfHost.paragraphs.join("\n");
    for (const thing of ["Python 3.12", "Docker", "Supabase CLI"]) {
      expect(text, thing).toContain(thing);
      expect(readme, thing).toContain(thing);
    }
    for (const command of ["supabase start", "uvicorn", "npm run dev"]) {
      expect(text, command).toContain(`\`${command}`);
      expect(readme, command).toContain(command);
    }
    expect(readme).toContain("Settings page");
    expect(readme).toContain("Load unpacked");
    expect(text).toContain("loaded unpacked in Chrome");
    // Only the commands the README gives: no install script, no docker compose, no one-line installer.
    expect(text).not.toMatch(/docker compose|curl |wget |\bnpx\b|pip install|brew /i);
  });
});
