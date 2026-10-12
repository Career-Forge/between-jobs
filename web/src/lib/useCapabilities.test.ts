import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

// The hook that binds the capabilities store to the signed-in person, RUN rather than read. The
// package has no DOM and a static render does not run effects, so `useEffect` is replaced by a
// recorder (the pattern components/EnrollmentGate.test.tsx uses): a test renders, then runs what
// was registered and looks at what the real store and the real hook did. The things outside are
// stand-ins: who is signed in, and the server's answer, which depends on who asked.
//
// What this guards is the ORDER inside the hook's effect. When a second person signs in within one
// page session, the store must be told first (it starts over) and asked second; the other way
// round it sees the previous person's answer, asks nothing, and is then reset with no ask in
// flight, leaving the next person on "Loading..." for good.

const recorded = vi.hoisted(() => ({
  effects: [] as { fn: () => void | (() => void) }[],
  user: null as string | null,
  asked: [] as string[],
}));

vi.mock("react", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react")>();
  return {
    ...actual,
    useEffect: (fn: () => void | (() => void)) => {
      recorded.effects.push({ fn });
    },
  };
});

vi.mock("../auth", () => ({
  useAuth: () => ({
    session: recorded.user === null ? null : { user: { id: recorded.user } },
  }),
}));

vi.mock("./api", () => ({
  apiFetch: async (path: string) => {
    recorded.asked.push(`${path} as ${recorded.user}`);
    // Gmail is open to user-A and closed to everyone else: an answer that is the caller's own.
    return { telegram: false, gmail: true, gmail_allowed: recorded.user === "user-A" };
  },
}));

// A fresh module (so a fresh store) for each test: the store lives at module level.
async function freshHook() {
  vi.resetModules();
  const { useCapabilities } = await import("./useCapabilities");
  function Probe() {
    const state = useCapabilities();
    return createElement("i", null, state.kind === "ready" ? `ready:${String(state.capabilities.gmailAllowed)}` : state.kind);
  }
  const render = () => renderToStaticMarkup(createElement(Probe));

  // One mount of the gate as `user`: the render registers the effect, which then runs, and the
  // store's ask is given time to come back. Returns what the page draws afterwards.
  return async function mountAs(user: string | null): Promise<string> {
    recorded.user = user;
    recorded.effects.length = 0;
    render();
    for (const effect of [...recorded.effects]) effect.fn();
    await new Promise((resolve) => setTimeout(resolve, 0));
    return render();
  };
}

beforeEach(() => {
  recorded.effects.length = 0;
  recorded.user = null;
  recorded.asked.length = 0;
});

describe("useCapabilities and whose answer it holds", () => {
  it("asks for the signed-in person and shows the answer they were given", async () => {
    const mountAs = await freshHook();

    expect(await mountAs("user-A")).toBe("<i>ready:true</i>");
    expect(recorded.asked).toEqual(["/capabilities as user-A"]);
  });

  it("gives a second person their own answer, asked for after the first one's", async () => {
    const mountAs = await freshHook();

    expect(await mountAs("user-A")).toBe("<i>ready:true</i>");
    expect(await mountAs("user-B")).toBe("<i>ready:false</i>");

    expect(recorded.asked).toEqual(["/capabilities as user-A", "/capabilities as user-B"]);
  });

  it("asks nothing when nobody is signed in", async () => {
    const mountAs = await freshHook();

    expect(await mountAs(null)).toBe("<i>checking</i>");
    expect(recorded.asked).toEqual([]);
  });

  it("does not ask again for the same person", async () => {
    const mountAs = await freshHook();

    await mountAs("user-A");
    expect(await mountAs("user-A")).toBe("<i>ready:true</i>");

    expect(recorded.asked).toEqual(["/capabilities as user-A"]);
  });

  it("forgets the previous person when it runs with nobody signed in, and asks for the next", async () => {
    const mountAs = await freshHook();

    await mountAs("user-A");
    expect(await mountAs(null)).toBe("<i>checking</i>");
    expect(await mountAs("user-B")).toBe("<i>ready:false</i>");

    expect(recorded.asked).toEqual(["/capabilities as user-A", "/capabilities as user-B"]);
  });
});
