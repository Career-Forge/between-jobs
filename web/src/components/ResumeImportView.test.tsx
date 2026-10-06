import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { PRIVACY } from "../content/legal";
import {
  IMPORT_IDS,
  focusTargetAfterChange,
  importReducer,
  initialImportState,
  type ImportEvent,
  type ImportState,
} from "../lib/resumeImport";
import { parseImportResponse, type ImportDraft } from "../lib/resumeImportDraft";
import { buildReview, allFields } from "../lib/resumeImportReview";
import { headingLevels, tagsOf, textOfMarkup } from "../testing/markup";
import { buttonsLabelled, expand, findAll, onlyButton, press, textOf, type TreeNode } from "../testing/reactTree";
import viewSource from "./ResumeImportView.tsx?raw";
import {
  ALREADY_ACTIVE_NOTICE,
  BLOCKED_NOTE,
  DISCARD_LABEL,
  IMPORT_LIMITS,
  IMPORT_MARK_ID,
  IMPORT_PRIVACY_STATEMENT,
  IMPORT_TITLE,
  LEAVE_NOTE,
  LEFT_EMPTY_INTRO,
  LEFT_EMPTY_TITLE,
  REVIEW_DETAIL,
  REVIEW_LABEL,
  REVIEW_TITLE,
  USE_LABEL,
  ResumeImportView,
  type ResumeImportActions,
} from "./ResumeImportView";

// The card as a person meets it, rendered rather than reasoned about: what each state puts on the
// page, which controls are buttons and what they call, and that the marked text is the value's own
// place in the file. The state machine behind it is tested in lib/resumeImport.test.ts.

// A text with an emoji before the values, so that the offsets only line up if they are used as
// UTF-16 units, as the server computes them.
const TEXT = "\u{1F680} Pat Example\nData Engineer\nAcme Corp 2022 - present\n<script>alert(1)</script>\n";

function span(value: string): { start: number; end: number } {
  const start = TEXT.indexOf(value);
  if (start === -1) throw new Error(`${value} is not in the text`);
  return { start, end: start + value.length };
}

function draft(overrides: Record<string, unknown> = {}): ImportDraft {
  const parsed = parseImportResponse({
    version_id: "11111111-1111-1111-1111-111111111111",
    already_active: false,
    profile: {
      personal: {
        name: "Pat Example",
        headline: "Data Engineer",
        emails: [{ address: "pat@example.com", primary: true }],
      },
      summary_bullets: ["Tidy pipelines"],
      experience: [
        {
          title: "Data Engineer",
          company: "Acme Corp",
          start_date: "2022-01",
          end_date: "present",
          is_current: true,
          bullets: ["Built <b>fast</b> things"],
        },
      ],
      skills: { programming: ["Python"] },
    },
    span_unit: "utf16",
    source_spans: {
      "/personal/name": span("Pat Example"),
      "/personal/headline": span("Data Engineer"),
      "/experience/0/title": span("Data Engineer"),
      "/experience/0/company": span("Acme Corp"),
      "/experience/0/start_date": span("2022"),
    },
    dropped: [
      { path: "/skills/tools/2", reason: "not_in_document", detail: "this text is not in the document", value: "HyperWidget" },
      { path: "/experience/1", reason: "entry_incomplete", detail: "removed: no value for title could be found in the document", value: null },
    ],
    assumptions: [
      { path: "/experience/0/start_date", value: "2022-01", note: "The document shows only the year, so the month is a convention. Check it." },
    ],
    extracted_text: TEXT,
    stats: { kept_fields: 14, dropped_fields: 2 },
    warnings: ["Page 2 was read in columns."],
    document: { kind: "pdf", filename: "resume.pdf", pages_total: 2, pages_read: 2, truncated: false },
    ...overrides,
  });
  if (parsed === null) throw new Error("the fixture is not a draft");
  return parsed;
}

function run(...events: ImportEvent[]): ImportState {
  return events.reduce(importReducer, initialImportState);
}

function reviewState(overrides: Record<string, unknown> = {}): ImportState {
  return run(
    { type: "file_chosen", fileName: "Pat Example CV.pdf" },
    { type: "upload_started" },
    { type: "uploaded", draft: draft(overrides) },
  );
}

function actionsOf(): ResumeImportActions & { calls: string[]; paths: string[]; files: unknown[] } {
  const calls: string[] = [];
  const paths: string[] = [];
  const files: unknown[] = [];
  const record = (name: string) => () => {
    calls.push(name);
  };
  return {
    calls,
    paths,
    files,
    chooseFile: record("chooseFile"),
    fileChosen: (chosen) => {
      calls.push("fileChosen");
      files.push(chosen);
    },
    retry: record("retry"),
    close: record("close"),
    toggleSource: (path) => {
      calls.push("toggleSource");
      paths.push(path);
    },
    askUse: record("askUse"),
    cancelUse: record("cancelUse"),
    confirmUse: record("confirmUse"),
    discard: record("discard"),
  };
}

function render(state: ImportState, replacesCurrent = true, actions: ResumeImportActions = actionsOf()): string {
  return renderToStaticMarkup(
    <MemoryRouter>
      <ResumeImportView state={state} replacesCurrent={replacesCurrent} actions={actions} />
    </MemoryRouter>,
  );
}

function tree(state: ImportState, replacesCurrent = true, actions: ResumeImportActions = actionsOf()): TreeNode[] {
  return expand(<ResumeImportView state={state} replacesCurrent={replacesCurrent} actions={actions} />);
}

const FAILED: ImportState = run(
  { type: "file_chosen", fileName: "a.pdf" },
  { type: "upload_started" },
  { type: "upload_failed", failure: { kind: "error", message: "That PDF is password-protected." }, retry: false },
);

describe("the card before anything is chosen", () => {
  const html = render(initialImportState);

  it("is a labelled region that names what it imports", () => {
    expect(html).toContain("<section");
    expect(html).toContain('aria-labelledby="bj-import-title"');
    expect(html).toContain('id="bj-import-title"');
    expect(textOfMarkup(html)).toContain("Import from a resume file (PDF or DOCX)");
    expect(IMPORT_TITLE).toBe("Import from a resume file (PDF or DOCX)");
  });

  it("says plainly that the text goes to the person's own AI provider and that the file is not kept", () => {
    const text = textOfMarkup(html);
    expect(text).toContain(IMPORT_PRIVACY_STATEMENT);
    // the whole sentence, so that whose key is used cannot change unnoticed: it is the person's own
    // model key, saved in Integrations (the route resolves the caller's own credential)
    expect(IMPORT_PRIVACY_STATEMENT).toBe(
      "The text of your file is sent to your own AI provider, using the model key you saved in Integrations, so a model can fill in a draft profile for you to review. We do not keep the file itself.",
    );
    expect(text).toContain(IMPORT_LIMITS);
    expect(IMPORT_LIMITS).toBe("PDF or DOCX, up to 5 MiB.");
  });

  it("says nothing the privacy policy does not: it has the same two statements in its own list", () => {
    const policy = JSON.stringify(PRIVACY);
    expect(policy).toContain("Importing a resume file (PDF or DOCX)");
    expect(policy).toContain("the text read from the file");
    expect(policy).toContain("We do not keep the file itself.");
    // and the policy says the same of whose key is used
    expect(policy).toContain("You choose one by saving your own key");
    expect(policy).toContain("AI features run on your own keys");
  });

  it("has a Choose a file button that is a real button, enabled, and a file input for PDF and DOCX", () => {
    const choose = onlyButton(tree(initialImportState), "Choose a file");
    expect(choose.props.id).toBe(IMPORT_IDS.choose);
    expect(choose.props.type).toBe("button");
    expect(choose.props.disabled).toBe(false);
    const input = tagsOf(html, "input")[0];
    expect(input.type).toBe("file");
    expect(input.accept).toContain(".pdf");
    expect(input.accept).toContain(".docx");
    expect(input.accept).toContain("application/pdf");
    expect(input.accept).toContain("application/vnd.openxmlformats-officedocument.wordprocessingml.document");
  });

  it("hands the chosen files to fileChosen when the file input changes", () => {
    const actions = actionsOf();
    const input = findAll(tree(initialImportState, true, actions), (el) => el.type === "input" && el.props.type === "file")[0];
    expect(input).toBeDefined();
    const chosen = ["a file list"];
    (input.props.onChange as (event: { target: { files: unknown } }) => void)({ target: { files: chosen } });
    expect(actions.calls).toEqual(["fileChosen"]);
    expect(actions.files).toEqual([chosen]);
    expect(actions.files[0]).toBe(chosen);
  });

  it("calls chooseFile when the button is pressed", () => {
    const actions = actionsOf();
    press(onlyButton(tree(initialImportState, true, actions), "Choose a file"));
    expect(actions.calls).toEqual(["chooseFile"]);
  });

  it("offers no review controls", () => {
    expect(buttonsLabelled(tree(initialImportState), USE_LABEL)).toHaveLength(0);
    expect(buttonsLabelled(tree(initialImportState), DISCARD_LABEL)).toHaveLength(0);
  });
});

describe("while the JSON import is being sent", () => {
  function blockedTree(state: ImportState): TreeNode[] {
    return expand(<ResumeImportView state={state} replacesCurrent actions={actionsOf()} blocked />);
  }

  it("turns the file button off and says why, so a file is not started while the page is busy", () => {
    expect(onlyButton(blockedTree(initialImportState), "Choose a file").props.disabled).toBe(true);
    const html = renderToStaticMarkup(
      <MemoryRouter>
        <ResumeImportView state={initialImportState} replacesCurrent actions={actionsOf()} blocked />
      </MemoryRouter>,
    );
    expect(textOfMarkup(html)).toContain(BLOCKED_NOTE);
    expect(html).toContain('role="status"');
    // from an error too: choosing a different file waits as well
    expect(onlyButton(blockedTree(FAILED), "Choose a different file").props.disabled).toBe(true);
  });

  it("changes nothing when the page is not busy: the button is on and there is no note", () => {
    expect(onlyButton(tree(initialImportState), "Choose a file").props.disabled).toBe(false);
    expect(textOfMarkup(render(initialImportState))).not.toContain(BLOCKED_NOTE);
  });

  it("has no note while a file is being read or sent: the button is off for that reason, not this one", () => {
    for (const state of [{ kind: "reading", fileName: "a.pdf" }, { kind: "uploading", fileName: "a.pdf" }] as ImportState[]) {
      const html = renderToStaticMarkup(
        <MemoryRouter>
          <ResumeImportView state={state} replacesCurrent actions={actionsOf()} blocked />
        </MemoryRouter>,
      );
      expect(textOfMarkup(html), state.kind).not.toContain(BLOCKED_NOTE);
      expect(onlyButton(blockedTree(state), "Choose a file").props.disabled).toBe(true);
    }
  });

  it("has no note on a screen with no file button to turn off (a review in progress)", () => {
    const html = renderToStaticMarkup(
      <MemoryRouter>
        <ResumeImportView state={reviewState()} replacesCurrent actions={actionsOf()} blocked />
      </MemoryRouter>,
    );
    expect(textOfMarkup(html)).not.toContain(BLOCKED_NOTE);
  });
});

describe("while a file is read and sent", () => {
  it("says what is happening in a status region, and the button is off", () => {
    const reading = render({ kind: "reading", fileName: "Pat CV.pdf" });
    expect(reading).toContain('id="bj-import-status"');
    expect(reading).toContain('role="status"');
    expect(reading).toContain('tabindex="-1"');
    expect(textOfMarkup(reading)).toContain('Reading "Pat CV.pdf"...');

    const uploading = render({ kind: "uploading", fileName: "Pat CV.pdf" });
    expect(textOfMarkup(uploading)).toContain('Reading "Pat CV.pdf" with your AI model. This can take a little while.');
    for (const state of [{ kind: "reading", fileName: "x" }, { kind: "uploading", fileName: "x" }] as ImportState[]) {
      expect(onlyButton(tree(state), "Choose a file").props.disabled).toBe(true);
    }
  });

  it("escapes a file name that is written like markup", () => {
    const html = render({ kind: "uploading", fileName: '<img src=x onerror="alert(1)">.pdf' });
    expect(html).not.toContain("<img");
    expect(html).toContain("&lt;img");
  });
});

describe("when nothing was imported", () => {
  it("shows the message as an alert with somewhere for focus to go, and a way to choose another file", () => {
    const html = render(FAILED);
    expect(html).toContain('id="bj-import-error"');
    expect(html).toContain('tabindex="-1"');
    expect(html).toContain('role="alert"');
    expect(textOfMarkup(html)).toContain("That PDF is password-protected.");
    expect(onlyButton(tree(FAILED), "Choose a different file").props.disabled).toBe(false);
    expect(buttonsLabelled(tree(FAILED), "Try again")).toHaveLength(0);
  });

  it("offers Try again when sending the same file could work, and it calls retry", () => {
    const state = run(
      { type: "file_chosen", fileName: "a.pdf" },
      { type: "upload_started" },
      { type: "upload_failed", failure: { kind: "error", message: "We could not reach the server." }, retry: true },
    );
    const actions = actionsOf();
    press(onlyButton(tree(state, true, actions), "Try again"));
    expect(actions.calls).toEqual(["retry"]);
  });

  it("dismisses an error back to the start", () => {
    const actions = actionsOf();
    press(onlyButton(tree(FAILED, true, actions), "Dismiss"));
    expect(actions.calls).toEqual(["close"]);
  });

  it("shows a missing model key as the shared notice with its link to Integrations", () => {
    const state = run(
      { type: "file_chosen", fileName: "a.pdf" },
      { type: "upload_started" },
      {
        type: "upload_failed",
        failure: {
          kind: "setup",
          notice: {
            message: "Reading a resume file needs your own AI model.",
            linkTo: "/profile/integrations?capability=profile_import",
            linkLabel: "Add a model key in Integrations",
          },
        },
        retry: false,
      },
    );
    const html = render(state);
    expect(html).toContain('id="bj-import-error"');
    expect(html).toContain("bj-setup-notice");
    expect(html).toContain('href="/profile/integrations?capability=profile_import"');
    expect(textOfMarkup(html)).toContain("Add a model key in Integrations");
    expect(buttonsLabelled(tree(state), "Try again")).toHaveLength(0);
  });
});

describe("after the decision", () => {
  it("says the profile now comes from the file, and that the old one stays in the history when it replaced one", () => {
    const replaced = render({ kind: "activated", replacedCurrent: true });
    expect(replaced).toContain('id="bj-import-notice"');
    expect(replaced).toContain('role="status"');
    expect(textOfMarkup(replaced)).toContain("Your profile now comes from your file.");
    expect(textOfMarkup(replaced)).toContain("that version stays in your history");
    const first = textOfMarkup(render({ kind: "activated", replacedCurrent: false }));
    expect(first).toContain("It is your active profile.");
    expect(first).not.toContain("history");
  });

  it("says a discarded draft changed nothing, or that one that matched an earlier version stays in the history", () => {
    expect(textOfMarkup(render({ kind: "discarded", keptInHistory: false }))).toContain("Draft discarded. Nothing was changed.");
    expect(textOfMarkup(render({ kind: "discarded", keptInHistory: true }))).toContain(
      "That draft matches a version you used before, so it stays in your history. Nothing was changed.",
    );
  });

  it("lets the person dismiss the notice and choose another file", () => {
    const actions = actionsOf();
    const state: ImportState = { kind: "activated", replacedCurrent: true };
    press(onlyButton(tree(state, true, actions), "Dismiss"));
    expect(actions.calls).toEqual(["close"]);
    expect(onlyButton(tree(state), "Choose a file").props.disabled).toBe(false);
  });
});

describe("the review", () => {
  const state = reviewState();
  const html = render(state);
  const text = textOfMarkup(html);

  it("says, before anything else, that a model read the file and every field has to be checked", () => {
    expect(REVIEW_LABEL).toBe("Read from your file by an AI model: check every field before you use it");
    expect(text).toContain(`${REVIEW_LABEL}.`);
    expect(html).toContain('role="note"');
    expect(text).toContain(REVIEW_DETAIL);
    expect(REVIEW_DETAIL).toContain("This draft is saved but not in use");
    expect(text.indexOf(REVIEW_LABEL)).toBeLessThan(text.indexOf("Personal details"));
    expect(textOfMarkup(html)).toContain(REVIEW_TITLE);
  });

  it("names the file, its kind and pages, and how many values were kept and left out", () => {
    expect(text).toContain('Read from "Pat Example CV.pdf" -- PDF -- 2 pages.');
    expect(text).toContain("14 values were found in your file and kept; 2 proposed values were left out.");
  });

  it("says one page and one value in the singular", () => {
    const one = textOfMarkup(
      render(
        reviewState({
          document: { kind: "pdf", pages_total: 1, pages_read: 1, truncated: false },
          stats: { kept_fields: 1, dropped_fields: 1 },
        }),
      ),
    );
    expect(one).toContain('Read from "Pat Example CV.pdf" -- PDF -- 1 page.');
    expect(one).not.toContain("1 pages");
    expect(one).toContain("1 value was found in your file and kept; 1 proposed value was left out.");
    expect(one).not.toContain("1 values");
  });

  it("shows no counts line, and never the word null, when the server sent only one of the two counts", () => {
    const partial = textOfMarkup(render(reviewState({ stats: { kept_fields: 5 } })));
    expect(partial).not.toContain("were found in your file");
    expect(partial).not.toContain("was found in your file");
    expect(partial).not.toContain("null");
  });

  it("says when part of the file was not read", () => {
    const cut = textOfMarkup(render(reviewState({ document: { kind: "pdf", pages_total: 12, pages_read: 8, truncated: true } })));
    expect(cut).toContain("8 of 12 pages");
    expect(cut).toContain("Part of the file was not read, so values from the cut part are missing.");
  });

  it("lists every kept value as a labelled field, grouped by section, with real headings", () => {
    const fields = allFields(buildReview(draft().profile, draft().spans));
    expect(fields.length).toBe(12);
    for (const field of fields) {
      expect(text, field.label).toContain(field.label);
      expect(html).toContain(`<dt>${field.label}</dt>`);
    }
    for (const heading of ["Personal details", "Summary", "Experience", "Skills"]) {
      expect(html).toContain(`>${heading}</h4>`);
    }
    expect(html).toContain("<h5>Experience 1: Data Engineer, Acme Corp</h5>");
    // a labelled region per section, a definition list per block
    expect((html.match(/<dl /g) ?? []).length).toBe(4);
    expect(headingLevels(html)).toEqual([2, 3, 4, 4, 4, 5, 4, 4]);
  });

  it("escapes a value that is written like markup, the way a stranger's file might", () => {
    expect(html).not.toContain("<b>fast</b>");
    expect(html).toContain("Built &lt;b&gt;fast&lt;/b&gt; things");
  });

  it("gives each value read from the file a button that shows where it came from, closed, named by its value", () => {
    const toggles = findAll(tree(state), (el) => el.type === "button" && el.props.className === "bj-import-toggle");
    // twelve values: the two flags set by a rule have no toggle, and neither has a value with no place
    expect(toggles.length).toBe(5);
    for (const toggle of toggles) {
      expect(toggle.props.type).toBe("button");
      expect(toggle.props["aria-expanded"]).toBe(false);
      expect(toggle.props["aria-controls"]).toBeUndefined();
    }
    expect(textOfMarkup(html)).toContain("Show where this came from for Name");
    expect(textOfMarkup(html)).toContain("Show where this came from for Company");
  });

  it("calls toggleSource with the value's own path when a toggle is pressed", () => {
    const actions = actionsOf();
    const toggles = findAll(tree(state, true, actions), (el) => el.type === "button" && el.props.className === "bj-import-toggle");
    for (const toggle of toggles) press(toggle);
    expect(actions.paths).toEqual([
      "/personal/name",
      "/personal/headline",
      "/experience/0/title",
      "/experience/0/company",
      "/experience/0/start_date",
    ]);
  });

  it("says a value set by a rule was not read from the file, and one with no place says so", () => {
    expect(text).toContain("Set by a rule from other values, not read from your file.");
    expect(text).toContain("No place in your file was found for this value.");
    const unknownUnit = textOfMarkup(render(reviewState({ span_unit: "codepoints" })));
    expect(unknownUnit).toContain("Where this came from is not available.");
    expect(unknownUnit).not.toContain("Show where this came from");
  });

  it("shows an assumption beside the value and in a list of its own", () => {
    expect(text).toContain("Assumed: The document shows only the year, so the month is a convention. Check it.");
    expect(text).toContain("Assumed while reading your file");
    expect(text).toContain("Experience, entry 1, Start date: 2022-01.");
    expect(text).toContain("Entry numbers here are the model's own and can differ from the lists on this page.");
  });

  it("shows the notes from reading the file", () => {
    expect(text).toContain("Note: Page 2 was read in columns.");
    expect(html).toContain('aria-label="Notes from reading your file"');
  });

  it("lists what the model proposed that the file does not contain under its own heading", () => {
    expect(LEFT_EMPTY_TITLE).toBe("Left out of the draft");
    expect(html).toContain(`>${LEFT_EMPTY_TITLE}</h4>`);
    expect(html).toContain('aria-labelledby="bj-import-dropped-title"');
    expect(text).toContain(LEFT_EMPTY_INTRO);
    expect(text).toContain("Skills, Tools, item 3. This text is not in your file.");
    expect(text).not.toContain("(this text is not in the document)"); // it only says the sentence again
    expect(text).toContain("(removed: no value for title could be found in the document)");
    expect(text).toContain("The model proposed: “HyperWidget”");
    expect(text).toContain("Experience, entry 2. It was removed because a value it cannot do without was not kept.");
  });

  // A value is also dropped when it IS in the file: a work arrangement given as a city, a field
  // that repeats its degree, a date of birth (never read from a file). The heading and the intro
  // are shared by every reason, so they must claim none of them; each line says its own.
  it("does not tell a person their file lacks a value that was left out for another reason", () => {
    const html3 = render(
      reviewState({
        dropped: [
          { path: "/personal/location/city", reason: "invalid_value", detail: "a work arrangement, not a place", value: "Remote" },
          { path: "/education/0/field", reason: "duplicates_sibling", detail: "already part of the degree", value: "Widgetry" },
          { path: "/personal/dob", reason: "never_from_document", detail: "this field is not filled from a document", value: "1990-01-01" },
          { path: "/skills/tools/2", reason: "not_in_document", detail: "", value: "HyperWidget" },
        ],
      }),
    );
    const shared = `${LEFT_EMPTY_TITLE} ${LEFT_EMPTY_INTRO}`;
    expect(shared).not.toMatch(/does not say so/i);
    // "not in your file" appears only as one reason among several, never as the reason for all
    expect(LEFT_EMPTY_TITLE).not.toMatch(/your file/i);
    expect(LEFT_EMPTY_INTRO).not.toMatch(/but they are not in your file/i);
    expect(LEFT_EMPTY_INTRO).toMatch(/some are not in your file; others may be in it/);
    const text3 = textOfMarkup(html3);
    expect(text3).toContain(LEFT_EMPTY_INTRO);
    // each line still carries its own, accurate reason
    expect(text3).toContain("Personal details, City. This is not a usable value for that field.");
    expect(text3).toContain("Education, entry 1, Field of study. It is already part of another value, so it was not repeated.");
    expect(text3).toContain("This is never read from a file. Set it yourself in the profile editor.");
    expect(text3).toContain("Skills, Tools, item 3. This text is not in your file.");
  });

  it("says so when nothing was left out", () => {
    expect(textOfMarkup(render(reviewState({ dropped: [] })))).toContain("Nothing the model proposed was left out.");
  });

  it("escapes what the model proposed too", () => {
    const html2 = render(
      reviewState({ dropped: [{ path: "/skills/tools/0", reason: "not_in_document", detail: "", value: "<img src=x onerror=alert(1)>" }] }),
    );
    expect(html2).not.toContain("<img");
    expect(html2).toContain("&lt;img");
  });
});

describe("where a value came from", () => {
  const open = importReducer(reviewState(), { type: "source_toggled", path: "/personal/name" });
  const html = render(open);

  it("opens the text of the file with that value marked, and says the toggle is open", () => {
    expect(html).toContain('aria-expanded="true"');
    expect(html).toContain(`aria-controls="bj-import-source-${encodeURIComponent("/personal/name")}"`);
    expect(html).toContain(`id="bj-import-source-${encodeURIComponent("/personal/name")}"`);
    expect(textOfMarkup(html)).toContain("Hide where this came from for Name");
  });

  it("marks exactly the value, at the offsets the server gave in UTF-16, after an emoji", () => {
    const marks = [...html.matchAll(/<mark[^>]*>(.*?)<\/mark>/g)];
    expect(marks).toHaveLength(1);
    const inner = marks[0][1].replace(/<span class="bj-visually-hidden">.*?<\/span>/g, "");
    expect(inner).toBe("Pat Example");
    expect(marks[0][0]).toContain(`id="${IMPORT_MARK_ID}"`);
    // and the text around it is the file's own, in order
    const region = html.slice(html.indexOf("<pre>"), html.indexOf("</pre>"));
    expect(textOfMarkup(region)).toContain("\u{1F680} Start of the part this value came from: Pat Example End of that part. Data Engineer");
  });

  it("gives the mark words for a screen reader, since a highlight is only colour to the eye", () => {
    expect(html).toContain('<span class="bj-visually-hidden">Start of the part this value came from: </span>');
    expect(html).toContain('<span class="bj-visually-hidden"> End of that part.</span>');
  });

  it("puts the text in a region a keyboard can scroll, with a name", () => {
    expect(html).toContain('role="region"');
    expect(html).toContain('aria-label="Text read from your file"');
    expect(html).toMatch(/<div role="region" aria-label="Text read from your file" tabindex="0"/);
  });

  it("shows the file's text as text: markup inside it is not markup", () => {
    expect(html).not.toContain("<script>");
    expect(html).toContain("&lt;script&gt;alert(1)&lt;/script&gt;");
  });

  it("opens one value at a time", () => {
    const other = importReducer(open, { type: "source_toggled", path: "/experience/0/company" });
    const markup = render(other);
    expect((markup.match(/<mark/g) ?? []).length).toBe(1);
    expect(markup).toContain("Acme Corp");
    expect((markup.match(/aria-expanded="true"/g) ?? []).length).toBe(1);
  });

  it("says nothing is marked when the place given is not in the text", () => {
    const bad = reviewState({ source_spans: { "/personal/name": { start: 9000, end: 9010 } } });
    const markup = render(importReducer(bad, { type: "source_toggled", path: "/personal/name" }));
    expect(markup).not.toContain("<mark");
    expect(textOfMarkup(markup)).toContain("is not in the text read from your file, so nothing is marked");
  });
});

describe("using or discarding the draft", () => {
  it("offers Use this profile and Discard this draft, and nothing that uses the draft yet", () => {
    const actions = actionsOf();
    const nodes = tree(reviewState(), true, actions);
    press(onlyButton(nodes, USE_LABEL));
    press(onlyButton(nodes, DISCARD_LABEL));
    expect(actions.calls).toEqual(["askUse", "discard"]);
    expect(buttonsLabelled(nodes, "Yes, use this profile")).toHaveLength(0);
    // no control of the view is wired to the confirmation until the question is on screen
    const wired = findAll(nodes, (el) => el.props.onClick === actions.confirmUse);
    expect(wired).toHaveLength(0);
  });

  it("asks before using it, says what that does, and only then offers the confirming button", () => {
    const asked = importReducer(reviewState(), { type: "confirm_asked" });
    const html = render(asked, true);
    expect(html).toContain('id="bj-import-confirm"');
    expect(html).toContain('role="group"');
    expect(html).toContain('aria-labelledby="bj-import-confirm-text"');
    expect(textOfMarkup(html)).toContain(
      "Use this profile in place of your current one? It becomes your active profile, and your current version stays in your history.",
    );

    const actions = actionsOf();
    const nodes = tree(asked, true, actions);
    press(onlyButton(nodes, "Yes, use this profile"));
    press(onlyButton(nodes, "Not yet"));
    expect(actions.calls).toEqual(["confirmUse", "cancelUse"]);
    // the question replaces the first button, so Use this profile cannot be pressed twice
    expect(buttonsLabelled(nodes, USE_LABEL)).toHaveLength(0);
    // and discarding is still possible
    expect(buttonsLabelled(nodes, DISCARD_LABEL)).toHaveLength(1);
  });

  it("says it becomes the active profile, with no history to claim, when there was no profile yet", () => {
    const asked = importReducer(reviewState(), { type: "confirm_asked" });
    const text = textOfMarkup(render(asked, false));
    expect(text).toContain("Use this profile? It becomes your active profile.");
    expect(text).not.toContain("history");
  });

  it("is busy while the request is out: nothing can be pressed twice, and a status says what is happening", () => {
    const activating = run(
      { type: "file_chosen", fileName: "a" },
      { type: "upload_started" },
      { type: "uploaded", draft: draft() },
      { type: "confirm_asked" },
      { type: "activate_started" },
    );
    const html = render(activating);
    expect(html).toContain('id="bj-import-status"');
    expect(textOfMarkup(html)).toContain("Switching to this profile...");
    const nodes = tree(activating);
    for (const button of findAll(nodes, (el) => el.type === "button")) {
      if (textOf(button).includes("Dismiss")) continue;
      expect(button.props.disabled, textOf(button)).toBe(true);
    }

    const discarding = importReducer(reviewState(), { type: "discard_started" });
    expect(textOfMarkup(render(discarding))).toContain("Discarding the draft...");
    for (const button of findAll(tree(discarding), (el) => el.type === "button")) {
      expect(button.props.disabled, textOf(button)).toBe(true);
    }
  });

  it("shows why it failed, in an alert focus can go to, and keeps the draft and the question", () => {
    const failed = run(
      { type: "file_chosen", fileName: "a" },
      { type: "upload_started" },
      { type: "uploaded", draft: draft() },
      { type: "confirm_asked" },
      { type: "activate_started" },
      { type: "activate_failed", failure: { kind: "error", message: "We could not reach the server. Check your connection, then try again." } },
    );
    const html = render(failed);
    expect(html).toContain('id="bj-import-problem"');
    expect(html).toContain('role="alert"');
    expect(textOfMarkup(html)).toContain("We could not reach the server.");
    expect(html).toContain('id="bj-import-confirm"');
    expect(buttonsLabelled(tree(failed), "Yes, use this profile")).toHaveLength(1);
  });

  it("tells a person who leaves that the draft stays saved but unused, and that it is not listed", () => {
    expect(textOfMarkup(render(reviewState()))).toContain(LEAVE_NOTE);
    expect(LEAVE_NOTE).toContain("stays saved but is not used");
  });
});

describe("a draft that already is the profile in use", () => {
  const same = reviewState({ already_active: true });
  const html = render(same);

  it("says there is nothing to change, and offers neither using nor discarding it", () => {
    expect(ALREADY_ACTIVE_NOTICE).toBe("This matches your current profile: nothing to change.");
    expect(textOfMarkup(html)).toContain(ALREADY_ACTIVE_NOTICE);
    const nodes = tree(same);
    expect(buttonsLabelled(nodes, USE_LABEL)).toHaveLength(0);
    expect(buttonsLabelled(nodes, DISCARD_LABEL)).toHaveLength(0);
    expect(textOfMarkup(html)).not.toContain(LEAVE_NOTE);
  });

  it("has a Done button that closes the review, and still shows every value", () => {
    const actions = actionsOf();
    press(onlyButton(tree(same, true, actions), "Done"));
    expect(actions.calls).toEqual(["close"]);
    expect(textOfMarkup(html)).toContain("Pat Example");
  });
});

describe("accessibility", () => {
  const states: [string, ImportState][] = [
    ["idle", initialImportState],
    ["reading", { kind: "reading", fileName: "a" }],
    ["uploading", { kind: "uploading", fileName: "a" }],
    ["error", FAILED],
    ["review", reviewState()],
    ["open source", importReducer(reviewState(), { type: "source_toggled", path: "/personal/name" })],
    ["confirming", importReducer(reviewState(), { type: "confirm_asked" })],
    ["already in use", reviewState({ already_active: true })],
    ["activated", { kind: "activated", replacedCurrent: true }],
    ["discarded", { kind: "discarded", keptInHistory: false }],
  ];

  it("every button is a type=button button, and nothing clickable is a div, span, p or li", () => {
    for (const [name, state] of states) {
      const markup = render(state);
      const buttons = tagsOf(markup, "button");
      expect(buttons.length, name).toBeGreaterThan(0);
      for (const button of buttons) expect(button.type, name).toBe("button");
    }
    expect(viewSource).not.toMatch(/<(div|span|p|li|dd|dt|a|mark)\b[^>]*\bonClick=/);
  });

  it("has one card heading, one review heading, and headings that only go down a level at a time", () => {
    for (const [name, state] of states) {
      const levels = headingLevels(render(state));
      expect(levels[0], name).toBe(2);
      for (let i = 1; i < levels.length; i += 1) {
        expect(levels[i] - levels[i - 1], `${name}: ${levels.join(",")}`).toBeLessThanOrEqual(1);
      }
    }
  });

  it("draws every element focus is moved to, with tabIndex -1, in the state that moves it there", () => {
    const transitions: [ImportState, ImportState][] = [
      [initialImportState, { kind: "reading", fileName: "a" }],
      [{ kind: "uploading", fileName: "a" }, reviewState()],
      [{ kind: "uploading", fileName: "a" }, FAILED],
      [reviewState(), importReducer(reviewState(), { type: "confirm_asked" })],
      [importReducer(reviewState(), { type: "confirm_asked" }), reviewState()],
      [
        importReducer(reviewState(), { type: "confirm_asked" }),
        importReducer(importReducer(reviewState(), { type: "confirm_asked" }), { type: "activate_started" }),
      ],
      [
        importReducer(importReducer(reviewState(), { type: "confirm_asked" }), { type: "activate_started" }),
        { kind: "activated", replacedCurrent: true },
      ],
      [importReducer(reviewState(), { type: "discard_started" }), { kind: "discarded", keptInHistory: false }],
      [FAILED, initialImportState],
    ];
    for (const [before, after] of transitions) {
      const target = focusTargetAfterChange(before, after);
      expect(target, `${before.kind} -> ${after.kind}`).not.toBeNull();
      const markup = render(after);
      expect(markup, `${before.kind} -> ${after.kind}`).toContain(`id="${target}"`);
      const element = new RegExp(`<[a-z0-9]+[^>]*id="${target}"[^>]*>`).exec(markup);
      expect(element?.[0], String(target)).toBeDefined();
      // a button already takes focus; anything else needs tabindex -1 to take it
      if (!element?.[0].startsWith("<button")) expect(element?.[0], String(target)).toContain('tabindex="-1"');
    }
  });

  it("only has one element with each of its ids", () => {
    for (const [name, state] of states) {
      const markup = render(state);
      const ids = [...markup.matchAll(/ id="([^"]+)"/g)].map((match) => match[1]);
      expect(new Set(ids).size, `${name}: ${ids.join(", ")}`).toBe(ids.length);
    }
  });
});

describe("the view never calls the network", () => {
  it("is built from props only: it imports no API client", () => {
    expect(viewSource).not.toContain('from "../lib/api"');
    expect(viewSource).not.toMatch(/fetch\(/);
  });
});
