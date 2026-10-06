import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { TESTER_AGREEMENT_VERSION } from "../content/testerAgreement";
import type { CapabilitiesState } from "../lib/capabilities";
import type { Enrollment, EnrollmentLoad } from "../lib/enrollment";
import { useEnrollmentController, type EnrollmentController } from "../lib/useEnrollment";
import { EnrollmentGate } from "./EnrollmentGate";

// The wiring of the tester-programme gate around the shell's routes: what it draws for each state
// the hooks can be in, that it asks for the person's enrollment only when it must, that the retry
// is wired, and that what it holds reaches the pages below it. WHICH state draws WHICH page is
// decided by lib/enrollment.ts's enrollmentGate (tested there, as a table); this pins that the
// component asks it with the real facts and draws the answer.
//
// The package has no DOM, and a static render does not run effects, so `useEffect` is replaced by
// a recorder: a test renders, then RUNS what was registered and looks at what happened. The
// document title is the hook's own (lib/useDocumentTitle.ts writes document.title, which does not
// exist here), so it is a recorder too.

const recorded = vi.hoisted(() => ({
  effects: [] as { fn: () => void | (() => void); deps: readonly unknown[] | undefined }[],
  caps: { kind: "checking" } as { kind: string },
  retryCapabilities: vi.fn(() => true),
  refreshCapabilities: vi.fn(),
  refusalListeners: [] as (null | (() => void))[],
  titles: [] as (string | null)[],
  controller: null as unknown,
  enrollRenders: 0,
  retryProps: null as null | { message: string; programmeRequired: boolean; onRetry: () => void },
  seen: [] as unknown[],
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

vi.mock("../lib/useCapabilities", () => ({
  useCapabilities: () => recorded.caps,
  retryCapabilities: () => recorded.retryCapabilities(),
  refreshCapabilities: () => recorded.refreshCapabilities(),
}));

vi.mock("../lib/useDocumentTitle", () => ({
  useDocumentTitle: (title: string | null) => {
    recorded.titles.push(title);
  },
}));

vi.mock("../lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../lib/api")>();
  return {
    ...actual,
    setEnrollmentRefusalListener: (listener: null | (() => void)) => {
      recorded.refusalListeners.push(listener);
    },
  };
});

vi.mock("../lib/useEnrollment", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../lib/useEnrollment")>();
  return { ...actual, useEnrollmentState: () => recorded.controller };
});

vi.mock("../pages/Enroll", async () => {
  const { createElement } = await import("react");
  const { useEnrollmentController } = await import("../lib/useEnrollment");
  return {
    default: () => {
      recorded.enrollRenders += 1;
      recorded.seen.push(useEnrollmentController());
      return createElement("div", { id: "the-enrollment-page" });
    },
  };
});

vi.mock("./EnrollmentView", () => ({
  EnrollmentRetryView: (props: { message: string; programmeRequired: boolean; onRetry: () => void }) => {
    recorded.retryProps = props;
    return null;
  },
}));

function enrollment(overrides: Partial<Enrollment> = {}): Enrollment {
  return {
    enrolled: true,
    roleCohort: "data_analyst",
    seniority: "mid",
    needsSponsorship: null,
    consentVersion: TESTER_AGREEMENT_VERSION,
    consentedAt: "2026-10-06T09:00:00+00:00",
    withdrawnAt: null,
    currentVersion: TESTER_AGREEMENT_VERSION,
    needsReconsent: false,
    ...overrides,
  };
}

const NOT_ENROLLED = enrollment({ enrolled: false, roleCohort: null, seniority: null, consentVersion: null, consentedAt: null, currentVersion: null });

function caps(testerProgramRequired: boolean): CapabilitiesState {
  return { kind: "ready", capabilities: { telegram: false, telegramBotUsername: null, testerProgramRequired } };
}

function setup(capabilities: CapabilitiesState, load: EnrollmentLoad): EnrollmentController {
  const controller: EnrollmentController = {
    load,
    ensureLoaded: vi.fn(),
    reload: vi.fn(),
    replace: vi.fn(),
  };
  recorded.caps = capabilities;
  recorded.controller = controller;
  return controller;
}

function render(pathname = "/discover"): string {
  return renderToStaticMarkup(
    <MemoryRouter initialEntries={[pathname]}>
      <EnrollmentGate>
        <div id="the-app" />
      </EnrollmentGate>
    </MemoryRouter>,
  );
}

function runEffects(): void {
  for (const effect of [...recorded.effects]) effect.fn();
}

beforeEach(() => {
  recorded.effects.length = 0;
  recorded.enrollRenders = 0;
  recorded.retryProps = null;
  recorded.seen.length = 0;
  recorded.titles.length = 0;
  recorded.refusalListeners.length = 0;
  recorded.retryCapabilities.mockClear();
  recorded.refreshCapabilities.mockClear();
});

describe("when the programme is not required, nothing changes", () => {
  it.each([
    ["a person who has not joined", { kind: "ready", enrollment: NOT_ENROLLED } as EnrollmentLoad],
    ["a current tester", { kind: "ready", enrollment: enrollment() } as EnrollmentLoad],
    ["a lookup never made", { kind: "idle" } as EnrollmentLoad],
  ])("draws the page for %s, and never asks for the enrollment", (_who, load) => {
    const controller = setup(caps(false), load);

    expect(render()).toContain('id="the-app"');
    runEffects();

    expect(controller.ensureLoaded).not.toHaveBeenCalled();
    expect(recorded.enrollRenders).toBe(0);
  });

  it("draws the page when the server's settings could not be asked", () => {
    const controller = setup({ kind: "unavailable" }, { kind: "idle" });
    expect(render()).toContain('id="the-app"');
    runEffects();
    expect(controller.ensureLoaded).not.toHaveBeenCalled();
  });
});

// What "wait" draws: not the app (a flash of it, taken back, would be worse) and not a blank page
// either (the two asks behind it have deadlines, but within them a slow server would leave the
// main area empty with no sign of life). A status line that app.css only reveals after a short
// delay, so a normal answer never flashes it.
const WAITING = /^<p class="bj-muted bj-gate-wait" role="status">Loading\.\.\.<\/p>$/;

describe("while the server's settings are not known", () => {
  it("draws only a quiet status line, never the app, and asks for nothing", () => {
    const controller = setup({ kind: "checking" }, { kind: "idle" });

    expect(render()).toMatch(WAITING);
    runEffects();

    expect(controller.ensureLoaded).not.toHaveBeenCalled();
  });
});

describe("when the programme is required", () => {
  it("asks for the enrollment once the settings say so, and draws only the status line until it arrives", () => {
    const controller = setup(caps(true), { kind: "idle" });

    expect(render()).toMatch(WAITING);
    runEffects();

    expect(controller.ensureLoaded).toHaveBeenCalledTimes(1);
  });

  it("does not ask again while it is loading, and draws only the status line", () => {
    const controller = setup(caps(true), { kind: "loading" });

    expect(render()).toMatch(WAITING);
    runEffects();

    expect(controller.ensureLoaded).not.toHaveBeenCalled();
  });

  it("draws the enrollment page, and not the page asked for, to a person who has not joined", () => {
    setup(caps(true), { kind: "ready", enrollment: NOT_ENROLLED });

    const markup = render("/discover");

    expect(markup).toContain('id="the-enrollment-page"');
    expect(markup).not.toContain('id="the-app"');
  });

  it("does the same on a deep link, and for a tester on an older agreement or one who withdrew", () => {
    for (const stored of [
      NOT_ENROLLED,
      enrollment({ enrolled: false, needsReconsent: true }),
      enrollment({ enrolled: false, withdrawnAt: "2026-10-07T00:00:00Z" }),
    ]) {
      setup(caps(true), { kind: "ready", enrollment: stored });
      const markup = render("/applications/123");
      expect(markup).toContain('id="the-enrollment-page"');
      expect(markup).not.toContain('id="the-app"');
    }
  });

  it("draws the page asked for to a current tester", () => {
    setup(caps(true), { kind: "ready", enrollment: enrollment() });

    const markup = render("/discover");

    expect(markup).toContain('id="the-app"');
    expect(markup).not.toContain('id="the-enrollment-page"');
  });

  it.each(["/enroll", "/privacy", "/terms", "/profile", "/profile/integrations", "/update-password"])(
    "never stands in front of %s, even for a person who has not joined",
    (pathname) => {
      setup(caps(true), { kind: "ready", enrollment: NOT_ENROLLED });

      const markup = render(pathname);

      expect(markup).toContain('id="the-app"');
      expect(markup).not.toContain('id="the-enrollment-page"');
    },
  );

  it("says the lookup failed, with its message, and wires the retry to the controller's reload: never in, never out silently", () => {
    const controller = setup(caps(true), { kind: "failed", message: "The service is down." });

    const markup = render("/discover");

    expect(markup).not.toContain('id="the-app"');
    expect(markup).not.toContain('id="the-enrollment-page"');
    expect(recorded.retryProps?.message).toBe("The service is down.");
    expect(recorded.retryProps?.onRetry).toBe(controller.reload);
    expect(recorded.retryProps?.programmeRequired).toBe(true);
  });

  it("still lets the exempt pages through when the lookup failed", () => {
    setup(caps(true), { kind: "failed", message: "x" });
    expect(render("/profile")).toContain('id="the-app"');
    expect(recorded.retryProps).toBeNull();
  });
});

describe("what it provides to the pages below it", () => {
  it("is the same controller, so a join on the enrollment page changes what the gate decides", () => {
    const controller = setup(caps(false), { kind: "idle" });
    function Probe() {
      recorded.seen.push(useEnrollmentController());
      return null;
    }

    renderToStaticMarkup(
      <MemoryRouter>
        <EnrollmentGate>
          <Probe />
        </EnrollmentGate>
      </MemoryRouter>,
    );

    expect(recorded.seen).toEqual([controller]);
    expect(recorded.seen[0]).toBe(controller);
  });

  it("hands the enrollment page the same controller when it draws that page in place of another", () => {
    const controller = setup(caps(true), { kind: "ready", enrollment: NOT_ENROLLED });

    render("/discover");

    expect(recorded.seen).toEqual([controller]);
    expect(recorded.seen[0]).toBe(controller);
  });
});

describe("a failed ask for the server's settings does not leave the gate open for the whole visit", () => {
  // The gate sits above every page and never remounts, so it was the one reader that kept the
  // failure for the session: a server that blinked once was treated as "no programme" until a
  // reload, while the Profile line, which asked later, said "you have not joined".
  it("asks again when the settings are unavailable, on a first sight and whenever the person moves to another page", () => {
    setup({ kind: "unavailable" }, { kind: "idle" });

    render("/discover");

    const retry = recorded.effects.filter((effect) => effect.deps?.length === 2);
    expect(retry).toHaveLength(1);
    // keyed on the address and on what kind of answer it is, so a new page and a changed answer
    // each get one look
    expect(retry[0].deps).toEqual(["/discover", "unavailable"]);
    retry[0].fn();
    expect(recorded.retryCapabilities).toHaveBeenCalledTimes(1);
  });

  it("still passes the page while it does, since the server refuses what it must", () => {
    setup({ kind: "unavailable" }, { kind: "idle" });
    expect(render("/discover")).toContain('id="the-app"');
  });

  it.each([
    ["not asked yet", { kind: "checking" } as CapabilitiesState],
    ["known: not required", caps(false)],
    ["known: required", caps(true)],
  ])("does not ask again when the settings are %s", (_label, state) => {
    setup(state, { kind: "ready", enrollment: enrollment() });
    render("/discover");
    runEffects();
    expect(recorded.retryCapabilities).not.toHaveBeenCalled();
  });
});

describe("a refusal for enrollment from the server", () => {
  // A tab that was enrolled when it opened and has since withdrawn (in another tab), met a newer
  // agreement, or a server that switched the programme on mid-visit, keeps being refused. api.ts
  // tells the gate; the gate asks for the settings and the person's enrollment again, and the
  // decision then puts the enrollment page where the feature was.
  it("is listened for by the gate, which asks for the settings and the enrollment again", () => {
    const controller = setup(caps(true), { kind: "ready", enrollment: enrollment() });
    render("/discover");
    runEffects();

    const listener = recorded.refusalListeners.at(-1);
    expect(listener).toBeTypeOf("function");
    expect(recorded.refreshCapabilities).not.toHaveBeenCalled();
    expect(controller.reload).not.toHaveBeenCalled();

    listener?.();

    expect(recorded.refreshCapabilities).toHaveBeenCalledTimes(1);
    expect(controller.reload).toHaveBeenCalledTimes(1);
  });

  it("stops listening when the gate goes away, and registers against the controller's own reload", () => {
    const controller = setup(caps(true), { kind: "ready", enrollment: enrollment() });
    render("/discover");
    const registration = recorded.effects.find((effect) => effect.deps?.length === 1 && effect.deps[0] === controller.reload);
    expect(registration).toBeDefined();

    const cleanup = registration?.fn();
    expect(cleanup).toBeTypeOf("function");
    (cleanup as () => void)();

    expect(recorded.refusalListeners.at(-1)).toBeNull();
  });

  it("makes the gate draw the enrollment page once the fresh answer says the person is not enrolled", () => {
    // what the page showed before the refusal, then what asking again returned
    setup(caps(true), { kind: "ready", enrollment: enrollment() });
    expect(render("/discover")).toContain('id="the-app"');
    setup(caps(true), { kind: "ready", enrollment: enrollment({ enrolled: false, withdrawnAt: "2026-10-07T00:00:00Z" }) });
    const markup = render("/discover");
    expect(markup).toContain('id="the-enrollment-page"');
    expect(markup).not.toContain('id="the-app"');
  });
});

describe("the shell's document title", () => {
  // Only the gate knows it has drawn the enrollment page at another page's address, so it owns the
  // title (App.tsx asks for none inside the shell).
  it("is the enrollment page's while the gate draws that page in place of another", () => {
    setup(caps(true), { kind: "ready", enrollment: NOT_ENROLLED });
    render("/discover");
    expect(recorded.titles.at(-1)).toBe("Tester programme -- Between Jobs");
  });

  it("is the enrollment page's while it draws the retry view", () => {
    setup(caps(true), { kind: "failed", message: "x" });
    render("/applications");
    expect(recorded.titles.at(-1)).toBe("Tester programme -- Between Jobs");
  });

  it("is the address's own title when it lets the page through, so joining puts it back", () => {
    setup(caps(true), { kind: "ready", enrollment: enrollment() });
    render("/discover");
    expect(recorded.titles.at(-1)).toBe("Between Jobs");
    render("/privacy");
    expect(recorded.titles.at(-1)).toBe("Privacy Policy -- Between Jobs");
    setup(caps(false), { kind: "idle" });
    render("/enroll");
    expect(recorded.titles.at(-1)).toBe("Tester programme -- Between Jobs");
  });

  it("is asked for on every render, whichever decision it reaches (the hook order never changes)", () => {
    for (const [state, load] of [
      [{ kind: "checking" }, { kind: "idle" }],
      [caps(true), { kind: "idle" }],
      [caps(true), { kind: "failed", message: "x" }],
      [caps(true), { kind: "ready", enrollment: NOT_ENROLLED }],
      [caps(false), { kind: "idle" }],
    ] as [CapabilitiesState, EnrollmentLoad][]) {
      setup(state, load);
      const before = recorded.titles.length;
      render("/discover");
      expect(recorded.titles.length - before, `${state.kind}`).toBe(1);
    }
  });
});
