import { vi } from "vitest";
import type { LeverFieldMap } from "@/lib/ats-field-map";
import { CONSENT_STORAGE_KEY, CONSENT_VERSION } from "@/lib/consent";
import type { ContentScriptMessage, ExtensionPersonalInfo, TabState } from "@/lib/types";

// Loads entrypoints/content.ts the way the browser would -- `main()` run once
// against the current jsdom document -- with the WXT/browser globals it
// expects stubbed. The content script only ever talks to the outside world
// through `browser.runtime.sendMessage`, so a `respond` function standing in
// for the service worker is the whole backend.

export const PERSON: ExtensionPersonalInfo = {
  name: "Alice Example",
  email: "alice@example.com",
  phone: "+1-555-0100",
  location: { city: "New York", region: "NY", country: "US" },
  linkedin: "https://linkedin.com/in/alice",
  github: "",
  portfolio: "https://alice.dev",
};

export type TrackedState = Extract<TabState, { status: "tracked" }>;

export function trackedState(overrides: Partial<TrackedState> = {}): TrackedState {
  return {
    status: "tracked",
    applicationId: "app-A",
    userId: "user-1",
    payload: { prepare_result: null, personal_info: PERSON },
    resume: null,
    coverLetter: null,
    fieldMap: null,
    fieldMapError: null,
    ...overrides,
  };
}

export const LEVER_MAP: LeverFieldMap = {
  ats_type: "lever",
  version: 1,
  schema: "ats-field-map/v1",
  standard_fields: [
    {
      field: "location",
      selector: 'input[name="location"]',
      strategy: "joinNonEmpty",
      profileFields: ["location.city", "location.region", "location.country"],
      separator: ", ",
    },
  ],
  custom_question_prefix: "cards[",
  label_wrapper_selector: ".application-field",
  label_selector: ".application-label",
  cover_letter_label_pattern: "cover letter",
};

type Responder = (message: { type: string; [key: string]: unknown }) => unknown;

/** Pass as `consent` to start with no stored consent at all. */
export const NO_CONSENT = Symbol("no stored consent");

export interface ContentHarness {
  /** Deliver a message from the side panel and return the reply. */
  send: (message: ContentScriptMessage) => Promise<unknown>;
  /** Every message the content script sent to the extension, in order. */
  sent: Array<{ type: string; [key: string]: unknown }>;
  sentOfType: (type: string) => Array<{ type: string; [key: string]: unknown }>;
  /** Move the tab to another URL, as history.pushState would. */
  navigate: (href: string) => void;
  /** Fire WXT's `wxt:locationchange`, as it does after a client-side route change. */
  fireLocationChange: () => void;
  /** Let queued promise work (including the initial detect) run to completion. */
  settle: () => Promise<void>;
  /** Replace what `chrome.storage.local` holds for the consent flag (`undefined`
   * removes it). Fires `chrome.storage.onChanged` unless `notify` is false, so a
   * test can tell the listener apart from the per-use read. */
  setConsent: (value: unknown, options?: { notify?: boolean }) => void;
}

export async function settle(): Promise<void> {
  for (let i = 0; i < 12; i++) await Promise.resolve();
  await new Promise((resolve) => setTimeout(resolve, 0));
  for (let i = 0; i < 12; i++) await Promise.resolve();
}

export async function loadContentScript(options: {
  href: string;
  respond: Responder;
  /** The stored consent flag. Defaults to a current-version agreement, so a test
   * that is not about consent runs past the gate; `NO_CONSENT` stores nothing. */
  consent?: unknown;
}): Promise<ContentHarness> {
  vi.resetModules();
  const storage: Record<string, unknown> = {};
  if (options.consent !== NO_CONSENT) {
    storage[CONSENT_STORAGE_KEY] = options.consent === undefined ? { version: CONSENT_VERSION } : options.consent;
  }
  const storageListeners: Array<(changes: Record<string, { oldValue?: unknown; newValue?: unknown }>, area: string) => void> =
    [];
  vi.stubGlobal("chrome", {
    storage: {
      local: {
        get: async (key: string) => (key in storage ? { [key]: storage[key] } : {}),
      },
      onChanged: { addListener: (fn: (typeof storageListeners)[number]) => storageListeners.push(fn) },
    },
  });
  const loc = { href: options.href, hostname: new URL(options.href).hostname };
  vi.stubGlobal("location", loc);
  vi.stubGlobal("defineContentScript", (definition: unknown) => definition);

  const sent: ContentHarness["sent"] = [];
  const listeners: Array<(message: ContentScriptMessage, sender: unknown) => unknown> = [];
  const sendMessage = vi.fn(async (message: { type: string; [key: string]: unknown }) => {
    sent.push(message);
    return options.respond(message);
  });
  vi.stubGlobal("browser", {
    runtime: {
      id: "test-extension",
      sendMessage,
      onMessage: { addListener: (fn: (typeof listeners)[number]) => listeners.push(fn) },
    },
  });

  const locationHandlers: Array<() => void> = [];
  const ctx = {
    addEventListener: (_target: unknown, type: string, handler: () => void) => {
      if (type === "wxt:locationchange") locationHandlers.push(handler);
    },
    setTimeout: (callback: () => void, ms: number) => setTimeout(callback, ms),
  };

  const module = await import("@/entrypoints/content");
  (module.default as unknown as { main: (ctx: unknown) => void }).main(ctx);
  await settle();

  return {
    send: async (message) => {
      const listener = listeners[0];
      if (listener === undefined) throw new Error("content script registered no message listener");
      return await listener(message, { id: "test-extension" });
    },
    sent,
    sentOfType: (type) => sent.filter((m) => m.type === type),
    navigate: (href) => {
      loc.href = href;
      loc.hostname = new URL(href).hostname;
    },
    fireLocationChange: () => {
      for (const handler of locationHandlers) handler();
    },
    settle,
    setConsent: (value, setOptions) => {
      const oldValue = storage[CONSENT_STORAGE_KEY];
      if (value === undefined) delete storage[CONSENT_STORAGE_KEY];
      else storage[CONSENT_STORAGE_KEY] = value;
      if (setOptions?.notify === false) return;
      for (const listener of storageListeners) {
        listener({ [CONSENT_STORAGE_KEY]: { oldValue, newValue: value } }, "local");
      }
    },
  };
}
