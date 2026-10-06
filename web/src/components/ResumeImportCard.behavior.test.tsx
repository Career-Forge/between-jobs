import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { IMPORT_IDS, importReducer, initialImportState, type ImportEvent, type ImportState } from "../lib/resumeImport";
import { parseImportResponse } from "../lib/resumeImportDraft";

// What the connected resume-file card DOES when its actions are called: which request each makes, on
// which draft, and what it dispatches. ResumeImportCard.test.tsx draws the card once, and
// resumeImportWiring.test.ts reads its source; neither calls a handler, so a handler that did nothing,
// or the wrong thing, passed them both.
//
// The package has no DOM and a static render runs no effects and never re-renders, so (the technique of
// wiring.test.tsx) `useEffect` is a recorder that a test runs by hand, `useReducer` reports the state
// the test chose and records what is dispatched, and `useRef` keeps its value from one render to the
// next. The view is replaced by a catcher of its props, which is where the actions are. The API client is
// a pair of fakes: nothing here reaches a network.

const recorded = vi.hoisted(() => ({
  state: null as unknown,
  dispatch: vi.fn(),
  effects: [] as { fn: () => void | (() => void); deps: readonly unknown[] | undefined }[],
  refs: [] as { current: unknown }[],
  refIndex: 0,
  view: null as null | {
    state: unknown;
    replacesCurrent: boolean;
    blocked: boolean;
    fileInputRef: { current: { value: string; click: () => void } | null };
    actions: Record<string, (...args: unknown[]) => void>;
  },
  apiFetch: vi.fn(),
  apiFetchBytes: vi.fn(),
}));

vi.mock("react", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react")>();
  return {
    ...actual,
    useEffect: (fn: () => void | (() => void), deps?: readonly unknown[]) => {
      recorded.effects.push({ fn, deps });
    },
    useReducer: () => [recorded.state, recorded.dispatch],
    useRef: (initial: unknown) => {
      const index = recorded.refIndex++;
      recorded.refs[index] ??= { current: initial };
      return recorded.refs[index];
    },
  };
});

vi.mock("../lib/api", () => ({ apiFetch: recorded.apiFetch, apiFetchBytes: recorded.apiFetchBytes }));

vi.mock("./ResumeImportView", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./ResumeImportView")>();
  return {
    ...actual,
    ResumeImportView: (props: NonNullable<typeof recorded.view>) => {
      recorded.view = props;
      return null;
    },
  };
});

import { IMPORT_MARK_ID } from "./ResumeImportView";
import { ResumeImportCard } from "./ResumeImportCard";

// ── fixtures ───────────────────────────────────────────────────────────────

const VERSION = "v-77";

function answer(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    version_id: VERSION,
    already_active: false,
    profile: { personal: { name: "Pat Example" } },
    span_unit: "utf16",
    source_spans: { "/personal/name": { start: 0, end: 11 } },
    dropped: [],
    assumptions: [],
    extracted_text: "Pat Example",
    stats: {},
    warnings: [],
    document: {},
    ...overrides,
  };
}

function run(...events: ImportEvent[]): ImportState {
  return events.reduce(importReducer, initialImportState);
}

function reviewing(overrides: Record<string, unknown> = {}): ImportState {
  const draft = parseImportResponse(answer(overrides));
  if (draft === null) throw new Error("the fixture is not a draft");
  return run({ type: "file_chosen", fileName: "cv.pdf" }, { type: "upload_started" }, { type: "uploaded", draft });
}

const REVIEW = reviewing();
const ASKED = importReducer(REVIEW, { type: "confirm_asked" });
const ALREADY_IN_USE = reviewing({ already_active: true });

// A file that reads as a PDF, with the parts of a File the card uses.
function pdf(name = "cv.pdf") {
  const bytes = new Uint8Array([0x25, 0x50, 0x44, 0x46, 0x2d, 0x31, 0x2e, 0x37]);
  return { name, size: bytes.length, slice: () => ({ arrayBuffer: async () => bytes.buffer as ArrayBuffer }) };
}

const onActivated = vi.fn();
const element = { focus: vi.fn(), scrollIntoView: vi.fn() };
const getElementById = vi.fn();
const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

beforeEach(() => {
  recorded.state = initialImportState;
  recorded.dispatch.mockReset();
  recorded.effects.length = 0;
  recorded.refs.length = 0;
  recorded.refIndex = 0;
  recorded.view = null;
  recorded.apiFetch.mockReset();
  recorded.apiFetchBytes.mockReset();
  onActivated.mockReset();
  element.focus.mockReset();
  element.scrollIntoView.mockReset();
  getElementById.mockReset();
  getElementById.mockReturnValue(element);
  vi.stubGlobal("document", { getElementById });
});

// Draws the card in `state` and hands back what it gave its view. Effects and refs of an earlier
// mount in the same test are kept when `again` is set (a second render of the same card).
function mount(state: ImportState, props: { replacesCurrent?: boolean; blocked?: boolean; onOpenChange?: (open: boolean) => void } = {}, again = false) {
  recorded.state = state;
  recorded.effects.length = 0;
  recorded.refIndex = 0;
  if (!again) recorded.refs.length = 0;
  renderToStaticMarkup(<ResumeImportCard replacesCurrent={props.replacesCurrent ?? true} onActivated={onActivated} blocked={props.blocked} onOpenChange={props.onOpenChange} />);
  if (recorded.view === null) throw new Error("the view was not rendered");
  return recorded.view;
}

function dispatched(): ImportEvent[] {
  return recorded.dispatch.mock.calls.map((call) => call[0] as ImportEvent);
}

function runEffects(): (() => void)[] {
  const cleanups: (() => void)[] = [];
  for (const effect of [...recorded.effects]) {
    const cleanup = effect.fn();
    if (typeof cleanup === "function") cleanups.push(cleanup);
  }
  return cleanups;
}

// ── using the draft ────────────────────────────────────────────────────────

describe("confirming: a draft is used only after the person was asked", () => {
  it("sends nothing and tells nobody when the question was never asked, whatever calls confirmUse", async () => {
    for (const state of [REVIEW, initialImportState, ALREADY_IN_USE]) {
      recorded.apiFetch.mockReset();
      recorded.dispatch.mockReset();
      mount(state).actions.confirmUse();
      await flush();
      expect(recorded.apiFetch).not.toHaveBeenCalled();
      expect(dispatched()).toEqual([]);
      expect(onActivated).not.toHaveBeenCalled();
    }
  });

  it("asks the server to activate that draft, once, and tells the page once that the server said yes", async () => {
    recorded.apiFetch.mockResolvedValue({});
    mount(ASKED).actions.confirmUse();
    await flush();
    expect(recorded.apiFetch).toHaveBeenCalledTimes(1);
    expect(recorded.apiFetch).toHaveBeenCalledWith(`/profile/versions/${VERSION}/activate`, { method: "POST" });
    expect(dispatched()).toEqual([{ type: "activate_started" }, { type: "activated", replacedCurrent: true }]);
    expect(onActivated).toHaveBeenCalledTimes(1);
  });

  it("says in the end state whether the draft took the place of a profile, as the page knows it", async () => {
    recorded.apiFetch.mockResolvedValue({});
    mount(ASKED, { replacesCurrent: false }).actions.confirmUse();
    await flush();
    expect(dispatched()[1]).toEqual({ type: "activated", replacedCurrent: false });
  });

  it("sends one request for a second press while the first is in flight", async () => {
    let finish: (value: unknown) => void = () => {};
    recorded.apiFetch.mockReturnValue(new Promise((resolve) => (finish = resolve)));
    const { actions } = mount(ASKED);
    actions.confirmUse();
    actions.confirmUse();
    await flush();
    expect(recorded.apiFetch).toHaveBeenCalledTimes(1);
    finish({});
    await flush();
    expect(onActivated).toHaveBeenCalledTimes(1);
  });

  it("does not tell the page when the server refused, and a press after that is a new request", async () => {
    recorded.apiFetch.mockRejectedValueOnce(new TypeError("Failed to fetch"));
    const { actions } = mount(ASKED);
    actions.confirmUse();
    await flush();
    expect(onActivated).not.toHaveBeenCalled();
    expect(dispatched().map((event) => event.type)).toEqual(["activate_started", "activate_failed"]);
    recorded.apiFetch.mockResolvedValueOnce({});
    actions.confirmUse();
    await flush();
    expect(recorded.apiFetch).toHaveBeenCalledTimes(2);
    expect(onActivated).toHaveBeenCalledTimes(1);
  });
});

describe("the other decisions", () => {
  it("asking, backing out, closing and opening a source each dispatch their own event and send nothing", () => {
    const { actions } = mount(REVIEW);
    actions.askUse();
    actions.cancelUse();
    actions.close();
    actions.toggleSource("/personal/name");
    expect(dispatched()).toEqual([
      { type: "confirm_asked" },
      { type: "confirm_cancelled" },
      { type: "closed" },
      { type: "source_toggled", path: "/personal/name" },
    ]);
    expect(recorded.apiFetch).not.toHaveBeenCalled();
    expect(recorded.apiFetchBytes).not.toHaveBeenCalled();
  });

  it("discards by deleting that draft, and says it is gone", async () => {
    recorded.apiFetch.mockResolvedValue(undefined);
    mount(REVIEW).actions.discard();
    await flush();
    expect(recorded.apiFetch).toHaveBeenCalledTimes(1);
    expect(recorded.apiFetch).toHaveBeenCalledWith(`/profile/versions/${VERSION}`, { method: "DELETE" });
    expect(dispatched()).toEqual([{ type: "discard_started" }, { type: "discarded", keptInHistory: false }]);
  });

  it("can discard while the question is up, but not a draft that is already the profile in use, nor nothing", async () => {
    recorded.apiFetch.mockResolvedValue(undefined);
    mount(ASKED).actions.discard();
    await flush();
    expect(recorded.apiFetch).toHaveBeenCalledTimes(1);

    recorded.apiFetch.mockReset();
    for (const state of [ALREADY_IN_USE, initialImportState]) {
      mount(state).actions.discard();
      await flush();
      expect(recorded.apiFetch).not.toHaveBeenCalled();
    }
  });

  it("sends one request for a second discard press while the first is in flight", async () => {
    let finish: (value: unknown) => void = () => {};
    recorded.apiFetch.mockReturnValue(new Promise((resolve) => (finish = resolve)));
    const { actions } = mount(REVIEW);
    actions.discard();
    actions.discard();
    await flush();
    expect(recorded.apiFetch).toHaveBeenCalledTimes(1);
    finish(undefined);
    await flush();
  });
});

// ── choosing a file ────────────────────────────────────────────────────────

describe("choosing a file", () => {
  it("sends the chosen file's bytes as it is, with the type its first bytes give, and shows what came back", async () => {
    const file = pdf();
    recorded.apiFetchBytes.mockResolvedValue(answer());
    mount(initialImportState).actions.fileChosen([file]);
    await flush();
    expect(recorded.apiFetchBytes).toHaveBeenCalledTimes(1);
    const [path, body, contentType] = recorded.apiFetchBytes.mock.calls[0];
    expect(path).toBe("/profile/import-document?filename=resume.pdf");
    expect(body).toBe(file);
    expect(contentType).toBe("application/pdf");
    expect(dispatched().map((event) => event.type)).toEqual(["file_chosen", "upload_started", "uploaded"]);
    expect(recorded.apiFetch).not.toHaveBeenCalled();
  });

  it("starts nothing when no file was chosen", async () => {
    const { actions } = mount(initialImportState);
    actions.fileChosen(null);
    actions.fileChosen([]);
    await flush();
    expect(recorded.apiFetchBytes).not.toHaveBeenCalled();
    expect(dispatched()).toEqual([]);
  });

  it("sends the same file again when the person presses Try again after a failure", async () => {
    const file = pdf();
    recorded.apiFetchBytes.mockRejectedValueOnce(new TypeError("Failed to fetch")).mockResolvedValueOnce(answer());
    const { actions } = mount(initialImportState);
    actions.fileChosen([file]);
    await flush();
    expect(dispatched().map((event) => event.type)).toContain("upload_failed");
    expect(recorded.apiFetchBytes).toHaveBeenCalledTimes(1);

    actions.retry();
    await flush();
    expect(recorded.apiFetchBytes).toHaveBeenCalledTimes(2);
    expect(recorded.apiFetchBytes.mock.calls[1][1]).toBe(file);
    expect(dispatched().filter((event) => event.type === "uploaded")).toHaveLength(1);
  });

  it("has nothing to send again before a file was ever chosen", async () => {
    mount(initialImportState).actions.retry();
    await flush();
    expect(recorded.apiFetchBytes).not.toHaveBeenCalled();
  });

  it("sends one request for a second choice while the first is being sent", async () => {
    let finish: (value: unknown) => void = () => {};
    recorded.apiFetchBytes.mockReturnValue(new Promise((resolve) => (finish = resolve)));
    const { actions } = mount(initialImportState);
    actions.fileChosen([pdf("a.pdf")]);
    actions.fileChosen([pdf("b.pdf")]);
    await flush();
    expect(recorded.apiFetchBytes).toHaveBeenCalledTimes(1);
    finish(answer());
    await flush();
  });

  it("clears the file input after a choice, so the same file can be chosen again, and opens the chooser from the button", async () => {
    recorded.apiFetchBytes.mockResolvedValue(answer());
    const view = mount(initialImportState);
    const input = { value: "C:\\fakepath\\cv.pdf", click: vi.fn() };
    view.fileInputRef.current = input;
    view.actions.chooseFile();
    expect(input.click).toHaveBeenCalledTimes(1);
    view.actions.fileChosen([pdf()]);
    expect(input.value).toBe("");
    await flush();
    // and an empty choice clears it as well
    input.value = "x";
    view.actions.fileChosen([]);
    expect(input.value).toBe("");
  });

  it("does not fail when there is no file input yet", () => {
    const view = mount(initialImportState);
    expect(view.fileInputRef.current).toBeNull();
    expect(() => view.actions.chooseFile()).not.toThrow();
    expect(() => view.actions.fileChosen([])).not.toThrow();
  });
});

// ── what the card tells its view and its page ──────────────────────────────

describe("what the card passes on", () => {
  it("gives the view the state, whether there is a profile to replace, and whether the page is busy", () => {
    expect(mount(ASKED).state).toBe(ASKED);
    expect(mount(ASKED).replacesCurrent).toBe(true);
    expect(mount(ASKED, { replacesCurrent: false }).replacesCurrent).toBe(false);
    expect(mount(initialImportState).blocked).toBe(false);
    expect(mount(initialImportState, { blocked: true }).blocked).toBe(true);
  });

  it("tells the page whether a file import is under way, and that it is not once the card goes away", () => {
    const onOpenChange = vi.fn();
    mount(initialImportState, { onOpenChange });
    runEffects();
    expect(onOpenChange).toHaveBeenLastCalledWith(false);

    onOpenChange.mockReset();
    mount(REVIEW, { onOpenChange }, true);
    const cleanups = runEffects();
    expect(onOpenChange).toHaveBeenLastCalledWith(true);

    onOpenChange.mockReset();
    for (const cleanup of cleanups) cleanup();
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it("is fine without anyone to tell", () => {
    mount(REVIEW);
    expect(() => runEffects()).not.toThrow();
  });
});

describe("where focus and the page go after a change", () => {
  it("moves focus to what replaced the control just used, on a change and not on the first draw", () => {
    mount(initialImportState);
    runEffects();
    expect(getElementById).not.toHaveBeenCalledWith(IMPORT_IDS.status);
    expect(element.focus).not.toHaveBeenCalled();

    // a file was chosen: the status line says what is happening
    mount(run({ type: "file_chosen", fileName: "cv.pdf" }), {}, true);
    runEffects();
    expect(getElementById).toHaveBeenCalledWith(IMPORT_IDS.status);
    expect(element.focus).toHaveBeenCalledTimes(1);

    // the file is with the server: nothing new to move to
    getElementById.mockClear();
    mount(run({ type: "file_chosen", fileName: "cv.pdf" }, { type: "upload_started" }), {}, true);
    runEffects();
    expect(getElementById).not.toHaveBeenCalled();

    // the draft arrived: the review's heading
    mount(REVIEW, {}, true);
    runEffects();
    expect(getElementById).toHaveBeenCalledWith(IMPORT_IDS.review);
    expect(element.focus).toHaveBeenCalledTimes(2);
  });

  it("brings the marked part of an opened source into view, and does nothing when none is open", () => {
    mount(REVIEW);
    runEffects();
    expect(element.scrollIntoView).not.toHaveBeenCalled();

    const opened = importReducer(REVIEW, { type: "source_toggled", path: "/personal/name" });
    mount(opened, {}, true);
    runEffects();
    expect(getElementById).toHaveBeenCalledWith(IMPORT_MARK_ID);
    expect(element.scrollIntoView).toHaveBeenCalledWith({ block: "nearest" });
  });
});
