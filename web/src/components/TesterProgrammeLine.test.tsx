import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { TESTER_AGREEMENT_VERSION } from "../content/testerAgreement";
import type { CapabilitiesState } from "../lib/capabilities";
import type { Enrollment, EnrollmentLoad } from "../lib/enrollment";
import { EnrollmentContext, type EnrollmentController } from "../lib/useEnrollment";
import { anchorsOf, textOfMarkup } from "../testing/markup";
import { TesterProgrammeLine, TesterProgrammeLineView } from "./TesterProgrammeLine";

// The Profile page's "Tester programme" line: shown only when the server requires the programme or
// the person is already in it (what it says is lib/enrollment.ts's profileLineStatus, a table
// there), and linking to the enrollment page. The connected part is checked with the same
// recorder-for-effects approach as the other wiring tests.

const recorded = vi.hoisted(() => ({
  effects: [] as { fn: () => void | (() => void); deps: readonly unknown[] | undefined }[],
  caps: { kind: "checking" } as { kind: string },
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

vi.mock("../lib/useCapabilities", () => ({ useCapabilities: () => recorded.caps }));

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

function mount(capabilities: CapabilitiesState, load: EnrollmentLoad): { markup: string; controller: EnrollmentController } {
  const controller: EnrollmentController = { load, ensureLoaded: vi.fn(), reload: vi.fn(), replace: vi.fn() };
  recorded.caps = capabilities;
  const markup = renderToStaticMarkup(
    <MemoryRouter>
      <EnrollmentContext.Provider value={controller}>
        <TesterProgrammeLine />
      </EnrollmentContext.Provider>
    </MemoryRouter>,
  );
  return { markup, controller };
}

beforeEach(() => {
  recorded.effects.length = 0;
});

describe("the view", () => {
  it("draws nothing when there is no sentence to show", () => {
    expect(renderToStaticMarkup(<TesterProgrammeLineView status={null} />)).toBe("");
  });

  it("is a card with a heading, the sentence, and one link to the enrollment page that says join or withdraw", () => {
    const markup = renderToStaticMarkup(
      <MemoryRouter>
        <TesterProgrammeLineView status="You are in the tester programme." />
      </MemoryRouter>,
    );
    expect(markup).toContain("<h2>Tester programme</h2>");
    expect(textOfMarkup(markup)).toContain("You are in the tester programme.");
    expect(anchorsOf(markup).map((anchor) => [anchor.attributes.href, anchor.text])).toEqual([["/enroll", "Join or withdraw"]]);
  });
});

describe("the connected line", () => {
  it("is shown on a server that requires the programme, to someone who has not joined", () => {
    const { markup } = mount(caps(true), { kind: "ready", enrollment: NOT_ENROLLED });
    expect(textOfMarkup(markup)).toContain("You have not joined the tester programme.");
    expect(markup).toContain('href="/enroll"');
  });

  it("is shown to someone who is already in it, even where the programme is not required", () => {
    const { markup } = mount(caps(false), { kind: "ready", enrollment: enrollment() });
    expect(textOfMarkup(markup)).toContain("You are in the tester programme.");
  });

  it("is not shown where the programme is not required and the person never joined", () => {
    expect(mount(caps(false), { kind: "ready", enrollment: NOT_ENROLLED }).markup).toBe("");
    expect(mount(caps(false), { kind: "idle" }).markup).toBe("");
    expect(mount({ kind: "unavailable" }, { kind: "idle" }).markup).toBe("");
  });

  it("says only what is certain while the person's own state is not known, if the programme is required", () => {
    const { markup } = mount(caps(true), { kind: "loading" });
    expect(textOfMarkup(markup)).toContain("Joining the tester programme is required on this server.");
    expect(textOfMarkup(markup)).not.toContain("You have not joined");
  });

  it("asks the shared state for the enrollment when the page opens", () => {
    const { controller } = mount(caps(false), { kind: "idle" });
    for (const effect of recorded.effects) effect.fn();
    expect(controller.ensureLoaded).toHaveBeenCalledTimes(1);
  });

  it("draws nothing outside the shell, where no state is held", () => {
    recorded.caps = caps(true);
    expect(renderToStaticMarkup(<MemoryRouter><TesterProgrammeLine /></MemoryRouter>)).toBe("");
  });
});
