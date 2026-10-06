import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { TESTER_AGREEMENT_VERSION } from "../content/testerAgreement";
import type { EnrollmentActions } from "../components/EnrollmentView";
import type { CapabilitiesState } from "../lib/capabilities";
import type { Enrollment, EnrollmentLoad } from "../lib/enrollment";
import { ApiError } from "../lib/api";
import { EnrollmentContext, type EnrollmentController } from "../lib/useEnrollment";
import Enroll from "./Enroll";

// The connected enrollment page: what it asks for when it opens, what it draws for each state of
// the lookup, and what each action of the view does -- which request it sends, what it hands the
// shared state afterwards, and that a second press while one is out sends nothing. The request
// builders, the reducer and the view are tested on their own (lib/enrollment.test.ts,
// components/EnrollmentView.test.tsx); this is the glue between them.
//
// A static render does not run effects, so `useEffect` is a recorder; the form is pre-filled
// through the page's initial state (a static render cannot type into it); the view is replaced by
// a recorder of its props, whose `actions` are then pressed.

const filled = vi.hoisted(() => ({
  // What the page's reducer starts from. The default is a form already filled in; a test that
  // needs another start changes it (and `beforeEach` puts it back).
  state: {} as Record<string, unknown>,
  complete: {
    form: { roleCohort: "software_engineer", seniority: "senior", sponsorship: "no", agreed: true },
  } as Record<string, unknown>,
}));

const recorded = vi.hoisted(() => ({
  effects: [] as { fn: () => void | (() => void); deps: readonly unknown[] | undefined }[],
  apiFetch: vi.fn(),
  caps: { kind: "checking" } as { kind: string },
  viewProps: null as null | {
    enrollment: Enrollment;
    programmeRequired: boolean;
    page: { error: string | null; busy: string };
    actions: EnrollmentActions;
  },
  retryProps: null as null | { message: string; programmeRequired: boolean; onRetry: () => void },
  loadingRenders: 0,
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

vi.mock("../lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../lib/api")>();
  return { ...actual, apiFetch: recorded.apiFetch };
});

vi.mock("../lib/useCapabilities", () => ({ useCapabilities: () => recorded.caps }));

vi.mock("../lib/enrollment", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../lib/enrollment")>();
  return {
    ...actual,
    // A getter, so each render starts from what the test set up: a static render cannot type
    // into the form, so the form is filled in through the state it starts from.
    get initialEnrollPageState() {
      return { ...actual.initialEnrollPageState, ...filled.state };
    },
  };
});

vi.mock("../components/EnrollmentView", () => ({
  EnrollmentView: (props: NonNullable<typeof recorded.viewProps>) => {
    recorded.viewProps = props;
    return null;
  },
  EnrollmentRetryView: (props: { message: string; programmeRequired: boolean; onRetry: () => void }) => {
    recorded.retryProps = props;
    return null;
  },
  EnrollmentLoadingView: () => {
    recorded.loadingRenders += 1;
    return null;
  },
}));

function enrollment(overrides: Partial<Enrollment> = {}): Enrollment {
  return {
    enrolled: false,
    roleCohort: null,
    seniority: null,
    needsSponsorship: null,
    consentVersion: null,
    consentedAt: null,
    withdrawnAt: null,
    currentVersion: null,
    needsReconsent: false,
    ...overrides,
  };
}

const JOINED_BODY = {
  enrolled: true,
  role_cohort: "software_engineer",
  seniority: "senior",
  needs_sponsorship: false,
  consent_version: TESTER_AGREEMENT_VERSION,
  consented_at: "2026-10-06T09:00:00+00:00",
  withdrawn_at: null,
  current_version: TESTER_AGREEMENT_VERSION,
  needs_reconsent: false,
};

function mount(load: EnrollmentLoad, capabilities?: CapabilitiesState): EnrollmentController {
  const controller: EnrollmentController = {
    load,
    ensureLoaded: vi.fn(),
    reload: vi.fn(),
    replace: vi.fn(),
  };
  recorded.caps = capabilities ?? { kind: "checking" };
  renderToStaticMarkup(
    <MemoryRouter>
      <EnrollmentContext.Provider value={controller}>
        <Enroll />
      </EnrollmentContext.Provider>
    </MemoryRouter>,
  );
  return controller;
}

function actions(): EnrollmentActions {
  if (recorded.viewProps === null) throw new Error("the view was not rendered");
  return recorded.viewProps.actions;
}

function runEffects(): void {
  for (const effect of [...recorded.effects]) effect.fn();
}

beforeEach(() => {
  filled.state = filled.complete;
  recorded.effects.length = 0;
  recorded.viewProps = null;
  recorded.retryProps = null;
  recorded.loadingRenders = 0;
  recorded.apiFetch.mockReset();
});

describe("what the page draws", () => {
  it("draws nothing outside the shell (no gate above it to hold the state)", () => {
    recorded.caps = { kind: "checking" };
    expect(renderToStaticMarkup(<Enroll />)).toBe("");
    expect(recorded.viewProps).toBeNull();
  });

  it.each([["idle"], ["loading"]] as const)("says it is checking, and claims nothing, while the lookup is %s", (kind) => {
    mount({ kind });
    expect(recorded.loadingRenders).toBe(1);
    expect(recorded.viewProps).toBeNull();
  });

  it("says the lookup failed, with its message, and retries through the controller", () => {
    const controller = mount({ kind: "failed", message: "The service is down." });
    expect(recorded.retryProps?.message).toBe("The service is down.");
    expect(recorded.retryProps?.onRetry).toBe(controller.reload);
    expect(recorded.retryProps?.programmeRequired).toBe(false);
    expect(recorded.viewProps).toBeNull();
  });

  it("draws the view with what is known, and whether the server requires the programme", () => {
    mount({ kind: "ready", enrollment: enrollment() });
    expect(recorded.viewProps?.programmeRequired).toBe(false);

    mount(
      { kind: "ready", enrollment: enrollment() },
      { kind: "ready", capabilities: { telegram: false, telegramBotUsername: null, testerProgramRequired: true } },
    );
    expect(recorded.viewProps?.programmeRequired).toBe(true);
  });
});

describe("when it opens", () => {
  it("asks the shared state for the enrollment, once", () => {
    const controller = mount({ kind: "idle" });
    runEffects();
    expect(controller.ensureLoaded).toHaveBeenCalledTimes(1);
  });
});

describe("a submit that was refused for what is missing", () => {
  it("moves focus to the first field that needs it, and to nothing when nothing is missing", () => {
    const focus = vi.fn();
    const getElementById = vi.fn(() => ({ focus }));
    vi.stubGlobal("document", { getElementById });

    filled.state = { attempts: 1, form: { roleCohort: "", seniority: "", sponsorship: "not_given", agreed: false } };
    mount({ kind: "ready", enrollment: enrollment() });
    runEffects();
    expect(getElementById).toHaveBeenCalledExactlyOnceWith("bj-enroll-role");
    expect(focus).toHaveBeenCalledTimes(1);

    getElementById.mockClear();
    focus.mockClear();
    recorded.effects.length = 0;
    filled.state = { attempts: 1, form: { roleCohort: "qa_sdet", seniority: "mid", sponsorship: "not_given", agreed: false } };
    mount({ kind: "ready", enrollment: enrollment() });
    runEffects();
    expect(getElementById).toHaveBeenCalledExactlyOnceWith("bj-enroll-agreed");

    getElementById.mockClear();
    recorded.effects.length = 0;
    filled.state = { attempts: 0 };
    mount({ kind: "ready", enrollment: enrollment() });
    runEffects();
    expect(getElementById).not.toHaveBeenCalled();
    vi.unstubAllGlobals();
  });
});

describe("focus after a change", () => {
  it("is left alone on the first draw, whatever the page starts in (nothing has changed yet)", () => {
    const getElementById = vi.fn(() => ({ focus: vi.fn() }));
    vi.stubGlobal("document", { getElementById });

    for (const start of [{}, { confirmingWithdraw: true }, { notice: "joined" }, { notice: "withdrew" }]) {
      recorded.effects.length = 0;
      filled.state = { ...filled.complete, ...start };
      mount({ kind: "ready", enrollment: enrollment() });
      runEffects();
    }

    expect(getElementById).not.toHaveBeenCalled();
    vi.unstubAllGlobals();
  });
});

describe("joining", () => {
  it("posts the filled form, hands the answer to the shared state, and sends nothing else", async () => {
    recorded.apiFetch.mockResolvedValue(JOINED_BODY);
    const controller = mount({ kind: "ready", enrollment: enrollment() });

    actions().submit();
    await vi.waitFor(() => expect(controller.replace).toHaveBeenCalledTimes(1));

    expect(recorded.apiFetch).toHaveBeenCalledTimes(1);
    const [path, init] = recorded.apiFetch.mock.calls[0];
    expect(path).toBe("/tester/enrollment");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual({
      role_cohort: "software_engineer",
      seniority: "senior",
      needs_sponsorship: false,
      accept_version: TESTER_AGREEMENT_VERSION,
    });
    expect(vi.mocked(controller.replace).mock.calls[0][0]).toMatchObject({ enrolled: true, roleCohort: "software_engineer" });
  });

  it("sends nothing while a join is already out", async () => {
    let finish: (value: unknown) => void = () => {};
    recorded.apiFetch.mockReturnValue(new Promise((resolve) => (finish = resolve)));
    const controller = mount({ kind: "ready", enrollment: enrollment() });

    actions().submit();
    actions().submit();
    actions().submit();
    expect(recorded.apiFetch).toHaveBeenCalledTimes(1);

    finish(JOINED_BODY);
    await vi.waitFor(() => expect(controller.replace).toHaveBeenCalledTimes(1));
  });

  it("does not replace the shared state when the server refuses", async () => {
    recorded.apiFetch.mockRejectedValue(new ApiError(409, "changed", "CONFLICT"));
    const controller = mount({ kind: "ready", enrollment: enrollment() });

    actions().submit();
    await vi.waitFor(() => expect(recorded.apiFetch).toHaveBeenCalledTimes(1));
    await Promise.resolve();

    expect(controller.replace).not.toHaveBeenCalled();
  });
});

describe("withdrawing", () => {
  it("posts to the withdraw route and hands the answer to the shared state", async () => {
    recorded.apiFetch.mockResolvedValue({ ...JOINED_BODY, enrolled: false, withdrawn_at: "2026-10-07T09:00:00+00:00" });
    const controller = mount({ kind: "ready", enrollment: enrollment({ enrolled: true }) });

    actions().confirmWithdraw();
    await vi.waitFor(() => expect(controller.replace).toHaveBeenCalledTimes(1));

    expect(recorded.apiFetch).toHaveBeenCalledWith("/tester/enrollment/withdraw", { method: "POST" });
    expect(vi.mocked(controller.replace).mock.calls[0][0]).toMatchObject({
      enrolled: false,
      withdrawnAt: "2026-10-07T09:00:00+00:00",
    });
  });

  it("asking and cancelling send nothing", () => {
    mount({ kind: "ready", enrollment: enrollment({ enrolled: true }) });
    actions().askWithdraw();
    actions().cancelWithdraw();
    expect(recorded.apiFetch).not.toHaveBeenCalled();
  });

  it("sends nothing while another request is out", async () => {
    let finish: (value: unknown) => void = () => {};
    recorded.apiFetch.mockReturnValue(new Promise((resolve) => (finish = resolve)));
    const controller = mount({ kind: "ready", enrollment: enrollment({ enrolled: true }) });

    actions().confirmWithdraw();
    actions().confirmWithdraw();
    actions().submit();
    expect(recorded.apiFetch).toHaveBeenCalledTimes(1);

    finish({ ...JOINED_BODY, enrolled: false, withdrawn_at: "2026-10-07T09:00:00+00:00" });
    await vi.waitFor(() => expect(controller.replace).toHaveBeenCalledTimes(1));
  });
});
