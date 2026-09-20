import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useFocusRequests } from "../lib/useFocusRequests";
import { useScrollToHash } from "../lib/useScrollToHash";
import { HiringSignalsTab } from "./HiringSignalsTab";

// The wiring layer: the hooks and the connected component that bind the tested
// pieces (the model, the status store, the view) to React. Each piece is tested
// on its own elsewhere; what only THIS layer decides -- which model method an
// action calls, what is loaded when the tab mounts, when focus moves and that the
// model is told, that a consumer asks the server on mount, when a hash scrolls --
// was untested, and a one-token mistake in any of it type-checks and passes every
// other test.
//
// The package has no DOM, and a static render does not run effects. So `useEffect`
// is replaced by a recorder: a test renders the component (real hooks, real
// state), then RUNS what was registered and looks at what happened. `document` is a
// stub the test controls. Nothing here reaches a network: the API client is
// replaced, and the model is a recording fake wherever the model is not the thing
// under test.

const recorded = vi.hoisted(() => ({
  effects: [] as {
    fn: () => void | (() => void);
    deps: readonly unknown[] | undefined;
  }[],
  calls: [] as unknown[][],
  focusRequest: null as { id: number; target: { kind: string } } | null,
  viewProps: null as null | {
    actions: Record<string, (...args: unknown[]) => void>;
  },
  apiFetch: vi.fn(),
}));

vi.mock("react", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react")>();
  return {
    ...actual,
    useEffect: (fn: () => void | (() => void), deps?: readonly unknown[]) => {
      recorded.effects.push({ fn, deps });
    },
  };
});

vi.mock("../lib/api", () => ({ apiFetch: recorded.apiFetch }));

vi.mock("./HiringSignalsTabView", () => ({
  HiringSignalsTabView: (props: { actions: Record<string, (...args: unknown[]) => void> }) => {
    recorded.viewProps = props;
    return null;
  },
}));

vi.mock("../lib/hiringSignalsTabModel", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../lib/hiringSignalsTabModel")>();
  const record =
    (name: string) =>
    (...args: unknown[]) => {
      recorded.calls.push([name, ...args]);
      return Promise.resolve();
    };
  class FakeModel {
    getState = () => ({
      ...actual.initialTabState(),
      focusRequest: recorded.focusRequest,
    });
    subscribe = () => () => {};
    consumeFocusRequest = record("consumeFocusRequest");
    setQuery = record("setQuery");
    setLocation = record("setLocation");
    setFreshness = record("setFreshness");
    setLocale = record("setLocale");
    search = record("search");
    saveCurrentSearch = record("saveCurrentSearch");
    runSavedSearch = record("runSavedSearch");
    deleteSearch = record("deleteSearch");
    loadSearches = record("loadSearches");
    widen = record("widen");
    retry = record("retry");
    toggleEmbed = record("toggleEmbed");
    saveSignal = record("saveSignal");
    removeSaved = record("removeSaved");
    loadSaves = record("loadSaves");
  }
  return { ...actual, HiringTabModel: FakeModel };
});

const element = { focus: vi.fn(), scrollIntoView: vi.fn() };
const getElementById = vi.fn();

beforeEach(() => {
  recorded.effects.length = 0;
  recorded.calls.length = 0;
  recorded.focusRequest = null;
  recorded.viewProps = null;
  recorded.apiFetch.mockReset();
  element.focus.mockReset();
  element.scrollIntoView.mockReset();
  getElementById.mockReset();
  getElementById.mockReturnValue(element);
  vi.stubGlobal("document", { getElementById });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function runEffects(): void {
  for (const effect of [...recorded.effects]) effect.fn();
}

// ── the tab ──────────────────────────────────────────────────────────────

describe("the connected Hiring signals tab", () => {
  function mount(): Record<string, (...args: unknown[]) => void> {
    renderToStaticMarkup(<HiringSignalsTab />);
    if (recorded.viewProps === null) throw new Error("the view was not rendered");
    return recorded.viewProps.actions;
  }

  it("asks for BOTH saved lists when it mounts, once each, and for nothing else", () => {
    mount();
    runEffects();
    expect(recorded.calls).toEqual([["loadSaves"], ["loadSearches"]]);
  });

  // [action, the model method it must call, arguments, what the model must receive]
  const WIRING: [string, string, unknown[], unknown[]][] = [
    ["setQuery", "setQuery", ["data engineer"], ["data engineer"]],
    ["setLocation", "setLocation", ["Pune"], ["Pune"]],
    ["setFreshness", "setFreshness", ["week"], ["week"]],
    ["setLocale", "setLocale", ["india"], ["india"]],
    ["search", "search", [], []],
    ["saveSearch", "saveCurrentSearch", [], []],
    ["runSavedSearch", "runSavedSearch", [{ id: "s1" }], [{ id: "s1" }]],
    ["deleteSearch", "deleteSearch", [{ id: "s1" }], [{ id: "s1" }]],
    ["reloadSearches", "loadSearches", [], []],
    ["widen", "widen", ["week"], ["week"]],
    ["retry", "retry", [], []],
    ["toggleEmbed", "toggleEmbed", ["search:1"], ["search:1"]],
    ["save", "saveSignal", [{ activity_id: "1" }, "a label"], [{ activity_id: "1" }, "a label"]],
    ["unsave", "removeSaved", [{ id: "sv" }, "saved:sv"], [{ id: "sv" }, "saved:sv"]],
    ["reloadSaves", "loadSaves", [], []],
  ];

  it("hands the view an action for every control, and only those", () => {
    expect(Object.keys(mount()).sort()).toEqual(WIRING.map(([action]) => action).sort());
  });

  it.each(WIRING)(
    "%s calls model.%s, with what it was given, and nothing else",
    (action, method, args, expected) => {
      mount()[action](...args);
      expect(recorded.calls).toEqual([[method, ...expected]]);
    },
  );

  it("moves focus where the model asked, and then tells the model it did", () => {
    recorded.focusRequest = { id: 7, target: { kind: "query_input" } };
    mount();
    recorded.calls.length = 0;
    runEffects();
    expect(getElementById).toHaveBeenCalledWith("hst-query");
    expect(element.focus).toHaveBeenCalledTimes(1);
    expect(recorded.calls).toContainEqual(["consumeFocusRequest", 7]);
  });

  it("moves no focus and consumes nothing when the model asked for none", () => {
    mount();
    runEffects();
    expect(element.focus).not.toHaveBeenCalled();
    expect(recorded.calls.map(([name]) => name)).not.toContain("consumeFocusRequest");
  });
});

// ── useFocusRequests ─────────────────────────────────────────────────────

describe("useFocusRequests", () => {
  function Probe(props: {
    request: { id: number; target: string } | null;
    consume: (id: number) => void;
  }) {
    useFocusRequests(props.request, props.consume, (target) => `id-of-${target}`);
    return null;
  }

  it("focuses the element the target resolves to, once, then consumes the request", () => {
    const consume = vi.fn();
    renderToStaticMarkup(<Probe request={{ id: 3, target: "box" }} consume={consume} />);
    runEffects();
    expect(getElementById).toHaveBeenCalledWith("id-of-box");
    expect(element.focus).toHaveBeenCalledTimes(1);
    expect(consume).toHaveBeenCalledExactlyOnceWith(3);
  });

  it("still consumes the request when the element is not there (nothing to focus, nothing stuck)", () => {
    getElementById.mockReturnValue(null);
    const consume = vi.fn();
    renderToStaticMarkup(<Probe request={{ id: 4, target: "gone" }} consume={consume} />);
    runEffects();
    expect(element.focus).not.toHaveBeenCalled();
    expect(consume).toHaveBeenCalledExactlyOnceWith(4);
  });

  it("does nothing without a request", () => {
    const consume = vi.fn();
    renderToStaticMarkup(<Probe request={null} consume={consume} />);
    runEffects();
    expect(getElementById).not.toHaveBeenCalled();
    expect(consume).not.toHaveBeenCalled();
  });

  it("runs again for a NEW request, and not for the same one", () => {
    const consume = vi.fn();
    const request = { id: 5, target: "box" };
    renderToStaticMarkup(<Probe request={request} consume={consume} />);
    renderToStaticMarkup(<Probe request={{ id: 6, target: "box" }} consume={consume} />);
    const [first, second] = recorded.effects;
    expect(first.deps?.[0]).toBe(request);
    expect(second.deps?.[0]).not.toBe(request);
  });
});

// ── useScrollToHash ──────────────────────────────────────────────────────

describe("useScrollToHash", () => {
  function Probe(props: { ready: boolean; hash: string; also: boolean }) {
    useScrollToHash(props.ready, props.hash, props.also);
    return null;
  }

  it("scrolls to the element the hash names once the page is ready", () => {
    renderToStaticMarkup(<Probe ready hash="#hiring-posts" also />);
    runEffects();
    expect(getElementById).toHaveBeenCalledWith("hiring-posts");
    expect(element.scrollIntoView).toHaveBeenCalledExactlyOnceWith({
      behavior: "smooth",
      block: "start",
    });
  });

  it("does nothing before the page is ready, or with no hash, or when nothing has that id", () => {
    renderToStaticMarkup(<Probe ready={false} hash="#hiring-posts" also />);
    renderToStaticMarkup(<Probe ready hash="" also />);
    renderToStaticMarkup(<Probe ready hash="#" also />);
    runEffects();
    expect(getElementById).not.toHaveBeenCalled();
    getElementById.mockReturnValue(null);
    renderToStaticMarkup(<Probe ready hash="#nothing" also />);
    recorded.effects.splice(0, 3);
    expect(() => runEffects()).not.toThrow();
    expect(element.scrollIntoView).not.toHaveBeenCalled();
  });

  it("runs again when the panel it points at becomes available (the deep-link race)", () => {
    // `#hiring-posts` lands before the server's feature status arrives: the slot is
    // empty and hidden, so the first scroll goes nowhere. The effect must depend on
    // what makes the panel exist, or nothing scrolls again when it does.
    renderToStaticMarkup(<Probe ready hash="#hiring-posts" also={false} />);
    renderToStaticMarkup(<Probe ready hash="#hiring-posts" also />);
    const [before, after] = recorded.effects;
    expect(before.deps).toEqual([true, "#hiring-posts", false]);
    expect(after.deps).toEqual([true, "#hiring-posts", true]);
  });

  it("runs again for a second hash on the same page, and when the page becomes ready", () => {
    renderToStaticMarkup(<Probe ready={false} hash="#a" also />);
    renderToStaticMarkup(<Probe ready hash="#a" also />);
    renderToStaticMarkup(<Probe ready hash="#b" also />);
    const [loading, ready, other] = recorded.effects;
    expect(loading.deps?.[0]).toBe(false);
    expect(ready.deps?.[0]).toBe(true);
    expect(ready.deps?.[1]).toBe("#a");
    expect(other.deps?.[1]).toBe("#b");
  });
});

// ── the shared status hook ───────────────────────────────────────────────

describe("useHiringSignalsStatus and useHiringSignalsEnabled", () => {
  // The store is module-level (one answer for the whole app), so each test loads a
  // fresh copy of the module.
  async function fresh() {
    vi.resetModules();
    return await import("../lib/useHiringSignalsStatus");
  }
  const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

  function Enabled({ hook }: { hook: () => boolean }) {
    return <span>{`enabled=${hook()}`}</span>;
  }

  it("asks the server once when a consumer mounts, and hides the feature while it waits", async () => {
    const { useHiringSignalsEnabled } = await fresh();
    let answer!: (value: unknown) => void;
    recorded.apiFetch.mockReturnValue(new Promise((resolve) => (answer = resolve)));

    expect(renderToStaticMarkup(<Enabled hook={useHiringSignalsEnabled} />)).toContain(
      "enabled=false",
    );
    runEffects();
    expect(recorded.apiFetch).toHaveBeenCalledExactlyOnceWith("/hiring-signals/status");
    // still waiting: `checking` is not "on"
    expect(renderToStaticMarkup(<Enabled hook={useHiringSignalsEnabled} />)).toContain(
      "enabled=false",
    );

    answer({ enabled: true });
    await flush();
    expect(renderToStaticMarkup(<Enabled hook={useHiringSignalsEnabled} />)).toContain(
      "enabled=true",
    );
  });

  it("shows the feature only for a definite yes: not for no, and not for a failed ask", async () => {
    const off = await fresh();
    recorded.apiFetch.mockResolvedValue({ enabled: false });
    renderToStaticMarkup(<Enabled hook={off.useHiringSignalsEnabled} />);
    runEffects();
    await flush();
    expect(renderToStaticMarkup(<Enabled hook={off.useHiringSignalsEnabled} />)).toContain(
      "enabled=false",
    );

    recorded.effects.length = 0;
    const failed = await fresh();
    recorded.apiFetch.mockRejectedValue(new Error("network"));
    renderToStaticMarkup(<Enabled hook={failed.useHiringSignalsEnabled} />);
    runEffects();
    await flush();
    expect(renderToStaticMarkup(<Enabled hook={failed.useHiringSignalsEnabled} />)).toContain(
      "enabled=false",
    );
  });

  it("does not ask again once the answer is known", async () => {
    const { useHiringSignalsEnabled } = await fresh();
    recorded.apiFetch.mockResolvedValue({ enabled: true });
    renderToStaticMarkup(<Enabled hook={useHiringSignalsEnabled} />);
    runEffects();
    await flush();
    recorded.effects.length = 0;
    renderToStaticMarkup(<Enabled hook={useHiringSignalsEnabled} />); // a second consumer
    runEffects();
    await flush();
    expect(recorded.apiFetch).toHaveBeenCalledTimes(1);
  });
});
