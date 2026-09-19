import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { DetectionStateResponse, FillResult } from "@/lib/types";

// Renders the real side panel against a fake Supabase client and a fake
// `browser`. What the panel does is fully determined by what the content
// script and background answer, so those two are plain functions here.

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const auth = vi.hoisted(() => ({
  callback: null as null | ((event: string, session: unknown) => void),
  getSession: vi.fn(),
  signInWithPassword: vi.fn(),
  signOut: vi.fn(),
  onAuthStateChange: vi.fn(),
}));

vi.mock("@/lib/supabase", () => ({
  getSupabaseClient: () => ({
    auth: {
      getSession: auth.getSession,
      signInWithPassword: auth.signInWithPassword,
      signOut: auth.signOut,
      onAuthStateChange: auth.onAuthStateChange,
    },
  }),
}));

const APP_A = "0b7f3c1e-5d2a-4e8b-9c41-2f6a8d9e0b13";
const APP_B = "9c1d4e7a-1111-4222-8333-444455556666";
const SESSION = { user: { id: "user-1" } };

type Message = { type: string; [key: string]: unknown };
let toTab: (tabId: number, message: Message) => unknown;
let toBackground: (message: Message) => unknown;
const tabMessages: Message[] = [];
const backgroundMessages: Message[] = [];
let runtimeListeners: Array<(message: unknown, sender: unknown) => void> = [];
let container: HTMLDivElement;
let root: Root;

function tracked(applicationId: string): DetectionStateResponse {
  return {
    formDetected: true,
    tabState: {
      status: "tracked",
      applicationId,
      userId: "user-1",
      payload: { prepare_result: null, personal_info: null },
      resume: null,
      coverLetter: null,
      fieldMap: null,
      fieldMapError: null,
    },
  };
}

function fillResult(overrides: Partial<FillResult> = {}): FillResult {
  return {
    filledFields: [],
    skippedFields: [],
    resumeAttached: false,
    resumeError: null,
    coverLetterAttached: false,
    coverLetterError: null,
    unresolvedQuestions: [],
    fieldMapError: null,
    fillError: null,
    ...overrides,
  };
}

async function flush(): Promise<void> {
  await act(async () => {
    await Promise.resolve();
    await new Promise((resolve) => setTimeout(resolve, 0));
    await Promise.resolve();
  });
}

async function mount(): Promise<void> {
  const { default: App } = await import("@/entrypoints/sidepanel/App");
  await act(async () => {
    root.render(<App />);
  });
  await flush();
}

const buttons = () => Array.from(container.querySelectorAll("button"));
const buttonWith = (text: string) => buttons().find((b) => b.textContent?.includes(text));
const textOf = () => container.textContent ?? "";

const buttonExactly = (text: string) => buttons().find((b) => b.textContent?.trim() === text);

async function click(text: string, options: { exact?: boolean } = {}): Promise<void> {
  const button = options.exact ? buttonExactly(text) : buttonWith(text);
  if (button === undefined) throw new Error(`no button matching ${JSON.stringify(text)}; panel shows: ${textOf()}`);
  await act(async () => {
    button.dispatchEvent(new MouseEvent("click", { bubbles: true }));
  });
  await flush();
}

async function type(input: HTMLInputElement, value: string): Promise<void> {
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!;
  await act(async () => {
    setter.call(input, value);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

async function setAuth(session: unknown): Promise<void> {
  await act(async () => {
    auth.callback?.(session ? "SIGNED_IN" : "SIGNED_OUT", session);
  });
  await flush();
}

beforeEach(() => {
  vi.resetModules();
  tabMessages.length = 0;
  backgroundMessages.length = 0;
  runtimeListeners = [];
  auth.callback = null;
  auth.getSession.mockReset().mockResolvedValue({ data: { session: SESSION } });
  auth.signInWithPassword.mockReset().mockResolvedValue({ error: null });
  auth.signOut.mockReset().mockResolvedValue({ error: null });
  auth.onAuthStateChange.mockReset().mockImplementation((cb: (event: string, session: unknown) => void) => {
    auth.callback = cb;
    return { data: { subscription: { unsubscribe: vi.fn() } } };
  });

  toTab = (_tabId, message) => (message.type === "GET_DETECTION_STATE" || message.type === "RECHECK" ? tracked(APP_A) : undefined);
  toBackground = () => undefined;
  vi.stubGlobal("browser", {
    tabs: {
      query: vi.fn(async () => [{ id: 1 }]),
      sendMessage: vi.fn(async (tabId: number, message: Message) => {
        tabMessages.push(message);
        return toTab(tabId, message);
      }),
      onUpdated: { addListener: vi.fn(), removeListener: vi.fn() },
      onActivated: { addListener: vi.fn(), removeListener: vi.fn() },
    },
    runtime: {
      sendMessage: vi.fn(async (message: Message) => {
        backgroundMessages.push(message);
        return toBackground(message);
      }),
      onMessage: {
        addListener: vi.fn((fn: (message: unknown, sender: unknown) => void) => runtimeListeners.push(fn)),
        removeListener: vi.fn((fn: (message: unknown, sender: unknown) => void) => {
          runtimeListeners = runtimeListeners.filter((l) => l !== fn);
        }),
      },
    },
  });

  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  vi.unstubAllGlobals();
});

// ---- AB-05 / AB-02: sign-out ------------------------------------------------------

describe("signing out", () => {
  it("signs out of THIS extension's session only -- auth-js's default scope is global, which would also end the web app's sessions", async () => {
    await mount();

    await click("Sign out");

    expect(auth.signOut).toHaveBeenCalledTimes(1);
    expect(auth.signOut).toHaveBeenCalledWith({ scope: "local" });
  });

  it("leaves no address or password in the form for the next person on this browser", async () => {
    auth.getSession.mockResolvedValue({ data: { session: null } });
    await mount();
    const email = () => container.querySelector<HTMLInputElement>('input[type="email"]')!;
    const password = () => container.querySelector<HTMLInputElement>('input[type="password"]')!;
    await type(email(), "alice@example.com");
    await type(password(), "hunter2-alice");
    expect(email().value).toBe("alice@example.com");

    await act(async () => {
      container.querySelector("form")!.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    });
    await flush();
    await setAuth(SESSION); // the sign-in took
    await click("Sign out");
    await setAuth(null); // ...and the panel is back on the sign-in form

    expect(email().value).toBe("");
    expect(password().value).toBe("");
  });

  it("clears the password as soon as sign-in succeeds, not only at sign-out", async () => {
    auth.getSession.mockResolvedValue({ data: { session: null } });
    await mount();
    const password = () => container.querySelector<HTMLInputElement>('input[type="password"]')!;
    await type(container.querySelector<HTMLInputElement>('input[type="email"]')!, "alice@example.com");
    await type(password(), "hunter2-alice");

    await act(async () => {
      container.querySelector("form")!.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    });
    await flush();

    expect(password().value).toBe("");
  });

  it("keeps what was typed when sign-in FAILS, so a typo can be corrected", async () => {
    auth.getSession.mockResolvedValue({ data: { session: null } });
    auth.signInWithPassword.mockResolvedValue({ error: { message: "Invalid login credentials" } });
    await mount();
    await type(container.querySelector<HTMLInputElement>('input[type="email"]')!, "alice@example.com");

    await act(async () => {
      container.querySelector("form")!.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    });
    await flush();

    expect(container.querySelector<HTMLInputElement>('input[type="email"]')!.value).toBe("alice@example.com");
    expect(textOf()).toContain("Invalid login credentials");
  });

  it("asks the page to re-detect, so the tab stops holding the signed-out user's data", async () => {
    await mount();
    tabMessages.length = 0;

    await click("Sign out");

    expect(tabMessages.map((m) => m.type)).toContain("RECHECK");
  });
});

// ---- a fresh sign-in re-checks the page instead of reading the cache ---------------

describe("detection on auth changes", () => {
  it("opening the panel already signed in reads the page's cached state, without forcing a new lookup", async () => {
    await mount();
    expect(tabMessages.map((m) => m.type)).toEqual(["GET_DETECTION_STATE"]);
  });

  it("a sign-in after being signed out forces a fresh lookup -- possibly a different user on the same profile", async () => {
    auth.getSession.mockResolvedValue({ data: { session: null } });
    await mount();
    tabMessages.length = 0;

    await setAuth(SESSION);

    expect(tabMessages.map((m) => m.type)).toEqual(["RECHECK"]);
  });
});

// ---- WS-3 / AB-04: acting on what the page shows NOW ---------------------------------

describe("acting on an application re-checks that the page is still showing it", () => {
  function pageMovesToB(): void {
    let reads = 0;
    toTab = (_tabId, message) => {
      if (message.type === "GET_DETECTION_STATE") return tracked(++reads === 1 ? APP_A : APP_B);
      return undefined;
    };
  }

  it("'mark as applied' does NOT mark application A when the tab has moved on to B", async () => {
    pageMovesToB();
    await mount(); // panel believes it's looking at A

    await click("mark as applied");

    expect(backgroundMessages.filter((m) => m.type === "MARK_APPLIED")).toEqual([]);
    expect(textOf()).toContain("This page changed");
  });

  it("...and does mark it when the page is still on the same application", async () => {
    toBackground = (m) => (m.type === "MARK_APPLIED" ? { ok: true, status: "applied" } : undefined);
    await mount();

    await click("mark as applied");

    const sent = backgroundMessages.filter((m) => m.type === "MARK_APPLIED");
    expect(sent).toHaveLength(1);
    expect(sent[0]).toMatchObject({ applicationId: APP_A });
    expect(textOf()).toContain("Marked as applied");
  });

  it("drafting an answer doesn't run for application A once the tab has moved on to B", async () => {
    let reads = 0;
    toTab = (_tabId, message) => {
      if (message.type === "GET_DETECTION_STATE") return tracked(++reads <= 1 ? APP_A : APP_B);
      if (message.type === "REQUEST_FILL") {
        return fillResult({ unresolvedQuestions: [{ fieldName: "question_1", label: "Why us?", kind: "text" }] });
      }
      return undefined;
    };
    await mount();
    await click("Fill this page");

    await click("Draft answer");

    expect(backgroundMessages.filter((m) => m.type === "DRAFT_ANSWER" || m.type === "MATCH_ANSWER")).toEqual([]);
  });

  it("refreshes when the content script announces a client-side navigation in the ACTIVE tab", async () => {
    await mount();
    tabMessages.length = 0;

    await act(async () => {
      for (const listener of runtimeListeners) listener({ type: "PAGE_CHANGED" }, { id: "test", tab: { id: 1 } });
    });
    await flush();

    expect(tabMessages.map((m) => m.type)).toEqual(["GET_DETECTION_STATE"]);
  });

  it("ignores that announcement from a background tab", async () => {
    await mount();
    tabMessages.length = 0;

    await act(async () => {
      for (const listener of runtimeListeners) listener({ type: "PAGE_CHANGED" }, { id: "test", tab: { id: 2 } });
    });
    await flush();

    expect(tabMessages).toEqual([]);
  });
});

// ---- WS-2 / D5-1: a per-question fill that declines because the field has text ------

describe("filling a drafted answer into a field that already has text (D5)", () => {
  beforeEach(() => {
    toTab = (_tabId, message) => {
      if (message.type === "GET_DETECTION_STATE") return tracked(APP_A);
      if (message.type === "REQUEST_FILL") {
        return fillResult({ unresolvedQuestions: [{ fieldName: "question_1", label: "Why us?", kind: "text" }] });
      }
      if (message.type === "FILL_FIELD") return { filled: false, reason: "not_empty" };
      return undefined;
    };
    toBackground = (m) => {
      if (m.type === "MATCH_ANSWER") return { answer: null };
      if (m.type === "DRAFT_ANSWER") return { eligible: true, answer_text: "My drafted answer", declined_reason: null, warnings: [] };
      return undefined;
    };
  });

  it("says the field was left alone, keeps the draft, and doesn't call it an error or offer to re-draft", async () => {
    await mount();
    await click("Fill this page");
    await click("Draft answer");
    expect(container.querySelector("textarea")!.value).toBe("My drafted answer");

    await click("Fill", { exact: true });

    expect(textOf()).toContain("already has text");
    expect(textOf()).not.toContain("Couldn't fill this field");
    expect(buttonWith("Try again")).toBeUndefined();
    expect(container.querySelector("textarea")!.value).toBe("My drafted answer");
    expect(buttonExactly("Fill")).toBeDefined();
  });

  it("does not save the answer to memory when the field wasn't written", async () => {
    await mount();
    await click("Fill this page");
    await click("Draft answer");

    await click("Fill & remember");

    expect(backgroundMessages.filter((m) => m.type === "SAVE_ANSWER")).toEqual([]);
  });
});

describe("a fill that finds the page has changed", () => {
  it("re-reads the page and says so, instead of a generic failure", async () => {
    toTab = (_tabId, message) => {
      if (message.type === "GET_DETECTION_STATE") return tracked(APP_A);
      if (message.type === "REQUEST_FILL") {
        return fillResult({ unresolvedQuestions: [{ fieldName: "question_1", label: "Why us?", kind: "text" }] });
      }
      if (message.type === "FILL_FIELD") return { filled: false, reason: "page_changed" };
      return undefined;
    };
    toBackground = (m) => {
      if (m.type === "MATCH_ANSWER") return { answer: null };
      if (m.type === "DRAFT_ANSWER") return { eligible: true, answer_text: "Draft", declined_reason: null, warnings: [] };
      return undefined;
    };
    await mount();
    await click("Fill this page");
    await click("Draft answer");

    await click("Fill", { exact: true });

    expect(textOf()).toContain("This page changed");
    expect(textOf()).not.toContain("Couldn't fill this field");
  });
});

// ---- UI-2 / D6: what the panel offers to draft -----------------------------------------

describe("only a text field with a readable label is offered for drafting", () => {
  it("shows 'Answer this one yourself' -- and no Draft button -- for a text question whose label couldn't be read", async () => {
    toTab = (_tabId, message) => {
      if (message.type === "GET_DETECTION_STATE") return tracked(APP_A);
      if (message.type === "REQUEST_FILL") {
        return fillResult({ unresolvedQuestions: [{ fieldName: "cards[abc][field0]", label: null, kind: "text" }] });
      }
      return undefined;
    };
    await mount();
    await click("Fill this page");

    expect(buttonWith("Draft answer")).toBeUndefined();
    expect(textOf()).toContain("Answer this one yourself");
  });

  it("still offers Draft for an ordinary text question", async () => {
    toTab = (_tabId, message) => {
      if (message.type === "GET_DETECTION_STATE") return tracked(APP_A);
      if (message.type === "REQUEST_FILL") {
        return fillResult({ unresolvedQuestions: [{ fieldName: "question_1", label: "Why us?", kind: "text" }] });
      }
      return undefined;
    };
    await mount();
    await click("Fill this page");
    expect(buttonWith("Draft answer")).toBeDefined();
  });
});

// ---- REV-1: the review box shows the whole draft ------------------------------------------

describe("the draft under review", () => {
  it("collapses blank-line padding and strips invisible characters before showing it, and says how long it is", async () => {
    const rlo = String.fromCharCode(0x202e);
    const zwsp = String.fromCharCode(0x200b);
    toTab = (_tabId, message) => {
      if (message.type === "GET_DETECTION_STATE") return tracked(APP_A);
      if (message.type === "REQUEST_FILL") {
        return fillResult({ unresolvedQuestions: [{ fieldName: "question_1", label: "Why us?", kind: "text" }] });
      }
      return undefined;
    };
    toBackground = (m) => {
      if (m.type === "MATCH_ANSWER") return { answer: null };
      if (m.type === "DRAFT_ANSWER") {
        return { eligible: true, answer_text: `Visible start.${rlo}\n\n\n\n\n\n\n\n\n\nTail${zwsp} that was below the fold.`, declined_reason: null, warnings: [] };
      }
      return undefined;
    };
    await mount();
    await click("Fill this page");

    await click("Draft answer");

    const shown = container.querySelector("textarea")!.value;
    expect(shown).toBe("Visible start.\n\nTail that was below the fold.");
    expect(textOf()).toContain(`${shown.length} characters`);
  });
});

// ---- the fill's own failure and field-map notices -------------------------------------------

describe("fill notices", () => {
  it("shows a fill that threw, rather than waiting on a reply that never comes", async () => {
    toTab = (_tabId, message) => {
      if (message.type === "GET_DETECTION_STATE") return tracked(APP_A);
      if (message.type === "REQUEST_FILL") return fillResult({ fillError: "boom" });
      return undefined;
    };
    await mount();

    await click("Fill this page");

    expect(textOf()).toContain("Something went wrong while filling this page");
    expect(textOf()).toContain("boom");
    expect(textOf()).not.toContain("Still checking this page");
  });

  it("explains a skipped field-map selector without claiming the map failed verification", async () => {
    toTab = (_tabId, message) => {
      if (message.type === "GET_DETECTION_STATE") return tracked(APP_A);
      if (message.type === "REQUEST_FILL") {
        return fillResult({ fieldMapError: "This ATS's field map has an invalid selector (input[) -- skipped that field." });
      }
      return undefined;
    };
    await mount();

    await click("Fill this page");

    expect(textOf()).toContain("invalid selector (input[)");
    expect(textOf()).not.toContain("failed signature verification");
  });

  it("'Still checking this page' is reserved for a null reply", async () => {
    toTab = (_tabId, message) => (message.type === "GET_DETECTION_STATE" ? tracked(APP_A) : null);
    await mount();

    await click("Fill this page");

    expect(textOf()).toContain("Still checking this page");
  });
});
