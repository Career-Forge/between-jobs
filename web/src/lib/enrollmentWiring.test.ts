import { describe, expect, it } from "vitest";
import appSource from "../App.tsx?raw";
import profileSource from "../pages/Profile.tsx?raw";
import enrollSource from "../pages/Enroll.tsx?raw";
import viewSource from "../components/EnrollmentView.tsx?raw";
import accountCardSource from "../components/AccountCardView.tsx?raw";
import gateSource from "../components/EnrollmentGate.tsx?raw";
import savedSearchesCardSource from "../components/SavedSearchesCard.tsx?raw";
import savedSearchesCardViewSource from "../components/SavedSearchesCardView.tsx?raw";
import integrationsSource from "../pages/Integrations.tsx?raw";
import appCss from "../app.css?raw";
import apiSource from "./api.ts?raw";
import enrollmentSource from "./enrollment.ts?raw";
import useCapabilitiesSource from "./useCapabilities.ts?raw";
import useEnrollmentSource from "./useEnrollment.ts?raw";
import { TESTER_AGREEMENT, TESTER_AGREEMENT_VERSION } from "../content/testerAgreement";
import { GATE_EXEMPT_PATHS, INTEGRATIONS_PATH } from "./enrollment";
import { UPDATE_PASSWORD_PATH } from "./passwordReset";
import { ENROLL_PATH, PRIVACY_PATH, TERMS_PATH, appView, documentTitleFor } from "./publicRoutes";
import { rulesOf } from "../testing/css";

// What the source of the app says about the tester programme, read as text: the places that the
// rendering tests do not reach (App.tsx is only ever called as a plain function there). A wrong
// route, a gate that stopped wrapping the routes or a Profile branch that lost its line
// type-checks and passes every other test.
//
// vite's `?raw` imports, not `node:fs`: @types/node is not installed here.

describe("App.tsx", () => {
  it("wraps the signed-in routes, and only those, in the gate", () => {
    expect(appSource).toContain('import { EnrollmentGate } from "./components/EnrollmentGate";');
    const open = appSource.indexOf("<EnrollmentGate>");
    const close = appSource.indexOf("</EnrollmentGate>");
    expect(open).toBeGreaterThan(-1);
    expect(close).toBeGreaterThan(open);
    const inside = appSource.slice(open, close);
    expect(inside).toContain("<Routes>");
    expect(inside).toContain("</Routes>");
    // The signed-out screens (landing, legal pages, sign-in) are returned before the shell, so
    // the gate, which sits inside it, never stands in front of them.
    expect(appSource.indexOf('return <Login />')).toBeLessThan(open);
    expect(appSource.indexOf("<PublicLayout>")).toBeLessThan(open);
  });

  it("has the enrollment page on its own route, the one the Profile line links to", () => {
    expect(appSource).toContain("<Route path={ENROLL_PATH} element={<Enroll />} />");
    expect(ENROLL_PATH).toBe("/enroll");
  });

  it("still routes the two legal documents, which the gate leaves open", () => {
    expect(appSource).toContain("<Route path={PRIVACY_PATH} element={<Privacy />} />");
    expect(appSource).toContain("<Route path={TERMS_PATH} element={<Terms />} />");
  });
});

describe("the enrollment address", () => {
  it("is a deep link for a signed-out visitor: sign in first, then the same address opens it", () => {
    expect(appView({ signedIn: false, loading: false, pathname: ENROLL_PATH })).toBe("login");
    expect(appView({ signedIn: true, loading: false, pathname: ENROLL_PATH })).toBe("app");
  });

  it("has a title of its own inside the shell", () => {
    expect(documentTitleFor("app", ENROLL_PATH)).toBe("Tester programme -- Between Jobs");
    expect(documentTitleFor("app", "/enroll/")).toBe("Tester programme -- Between Jobs");
    expect(documentTitleFor("app", "/discover")).toBe("Between Jobs");
  });
});

describe("Profile.tsx", () => {
  it("draws the tester-programme line above the account card, in every state it draws that card", () => {
    const cards = [...profileSource.matchAll(/<AccountCard \/>/g)].length;
    const lines = [...profileSource.matchAll(/<TesterProgrammeLine \/>\s*<AccountCard \/>/g)].length;
    expect(cards).toBe(3);
    expect(lines).toBe(cards);
  });
});

describe("the gate's exempt pages are real routes of the app", () => {
  it("are the enrollment page, the two legal documents and Profile, which is where account deletion lives", () => {
    expect(enrollmentSource).toContain('export const PROFILE_PATH = "/profile"');
    expect(appSource).toContain('<Route path="/profile" element={<Profile />} />');
    expect(accountCardSource).toContain("Delete my account");
    expect(profileSource).toContain("<AccountCard />");
    expect([ENROLL_PATH, PRIVACY_PATH, TERMS_PATH, "/profile"].sort()).toEqual(["/enroll", "/privacy", "/profile", "/terms"]);
  });

  it("also include Integrations and the new-password page, each a route that App.tsx draws inside the gate", () => {
    expect(INTEGRATIONS_PATH).toBe("/profile/integrations");
    expect(appSource).toContain('<Route path="/profile/integrations" element={<Integrations />} />');
    expect(appSource).toContain("<Route path={UPDATE_PASSWORD_PATH} element={<UpdatePassword />} />");
    expect(GATE_EXEMPT_PATHS).toContain(INTEGRATIONS_PATH);
    expect(GATE_EXEMPT_PATHS).toContain(UPDATE_PASSWORD_PATH);
    // the list takes the new-password path from the one place that names it
    expect(enrollmentSource).toContain('import { UPDATE_PASSWORD_PATH } from "./passwordReset";');
    expect(enrollmentSource).toMatch(/GATE_EXEMPT_PATHS: readonly string\[\] = \[[^\]]*UPDATE_PASSWORD_PATH/);
    expect(enrollmentSource).toMatch(/GATE_EXEMPT_PATHS: readonly string\[\] = \[[^\]]*INTEGRATIONS_PATH/);
  });

  // The public text promises these controls to a person who withdrew or never joined: the Tester
  // Agreement ('Disconnect Gmail' on the Integrations page; pause or delete a saved search there)
  // and the Privacy Policy (remove a key, disconnect Gmail). The server leaves what they call open,
  // so the only thing that could take them away is the gate, and this fails if it ever does again.
  it("Integrations really has the controls the text names: Remove key, Disconnect Gmail, and Pause / Resume / Delete for a saved search", () => {
    expect(integrationsSource).toContain("Remove key");
    expect(integrationsSource).toContain("Disconnect Gmail");
    expect(integrationsSource).toContain("<SavedSearchesCard />");
    expect(savedSearchesCardViewSource).toContain('"Pause" : "Resume"');
    expect(savedSearchesCardViewSource).toContain("Delete");
    expect(savedSearchesCardSource).toContain("setSavedSearchActive(apiFetch, id, isActive)");
    expect(savedSearchesCardSource).toContain("deleteSavedSearch(apiFetch, id)");
  });

  it("the server calls those controls make are the open ones, never behind the enrollment gate (the inventory is tests/test_rate_limit_inventory.py)", () => {
    // the page asks the list, patches and deletes saved searches, lists credentials, and deletes
    // a Gmail connection or a key: all by path, none through a limiter
    expect(integrationsSource).toContain('"/credentials/oauth/gmail"');
    expect(integrationsSource).toContain('"/credentials"');
    expect(savedSearchesCardSource).not.toMatch(/\/discover|\/prepare|\/generate/);
  });

  it("the shell's gate wraps them: they are exempt by the list, not by being outside the gate", () => {
    const open = appSource.indexOf("<EnrollmentGate>");
    const close = appSource.indexOf("</EnrollmentGate>");
    const inside = appSource.slice(open, close);
    expect(inside).toContain('path="/profile/integrations"');
    expect(inside).toContain("UPDATE_PASSWORD_PATH");
  });
});

describe("the gate keeps its picture of the server true", () => {
  it("shares what the server was started with through one store, read with useSyncExternalStore, not a copy per hook", () => {
    expect(useCapabilitiesSource).toContain("useSyncExternalStore(store.subscribe, store.getSnapshot, store.getSnapshot)");
    expect(useCapabilitiesSource).toContain("createCapabilitiesStore(");
    expect(useCapabilitiesSource).not.toContain("useState");
    expect(useCapabilitiesSource).toContain("deadlineSignal(CAPABILITIES_TIMEOUT_MS)");
    expect(useCapabilitiesSource).toContain("export function retryCapabilities()");
    expect(useCapabilitiesSource).toContain("export function refreshCapabilities()");
  });

  it("the gate asks again after a failed ask, listens for a refusal for enrollment, and owns the shell's title", () => {
    expect(gateSource).toContain("if (capabilities.kind === \"unavailable\") retryCapabilities();");
    expect(gateSource).toContain("[pathname, capabilities.kind]");
    expect(gateSource).toContain("setEnrollmentRefusalListener(() => {");
    expect(gateSource).toContain("refreshCapabilities();");
    expect(gateSource).toContain("reload();");
    expect(gateSource).toContain("return () => setEnrollmentRefusalListener(null);");
    expect(gateSource).toContain("useDocumentTitle(enrollmentTitleFor(decision, pathname));");
  });

  it("api.ts tells the listener about a refusal for enrollment on both ways of fetching, and still throws", () => {
    expect(apiSource).toContain("isEnrollmentRefusal(error)");
    expect(apiSource).toContain("enrollmentRefusalListener?.();");
    expect((apiSource.match(/throw failedWith\(response, body\);/g) ?? []).length).toBe(2);
    expect(apiSource).not.toContain("throw apiErrorFrom(");
  });

  it("the enrollment lookup has a deadline, so the gate's blank wait ends", () => {
    expect(useEnrollmentSource).toContain("signal: deadlineSignal(ENROLLMENT_LOOKUP_TIMEOUT_MS)");
    expect(useEnrollmentSource).toContain("loadEnrollment(lookupFetcher)");
  });

  it("draws a status line while it waits, which app.css reveals only after a short delay, so a normal answer never flashes it", () => {
    expect(gateSource).toContain('className="bj-muted bj-gate-wait" role="status"');
    const rule = rulesOf(appCss).find((candidate) => candidate.selectors.includes(".bj-gate-wait"));
    expect(rule, ".bj-gate-wait in app.css").toBeDefined();
    expect(rule?.declarations.opacity).toBe("0");
    expect(rule?.declarations.animation).toMatch(/^bj-gate-wait-in\s+[\d.]+s\s+linear\s+0?\.[1-9]\d*s\s+forwards$/);
    expect(appCss).toMatch(/@keyframes bj-gate-wait-in\s*\{\s*to\s*\{\s*opacity:\s*1;?\s*\}\s*\}/);
  });
});

describe("focus on the enrollment page", () => {
  it("moves to what replaces the control that was used, after a join, a withdrawal, asking and cancelling", () => {
    expect(enrollSource).toContain("focusTargetAfterChange(focusFacts.current, now)");
    expect(enrollSource).toContain("document.getElementById(target)?.focus()");
    expect(enrollSource).toContain("[page.confirmingWithdraw, page.notice]");
    // the failure path keeps its own, earlier, effect
    expect(enrollSource).toContain("if (page.error) errorRef.current?.focus();");
    // the ids the effect aims at are drawn by the view, with tabIndex -1
    for (const id of ["ENROLL_FOCUS_IDS.withdraw", "ENROLL_FOCUS_IDS.confirm", "ENROLL_FOCUS_IDS.notice"]) {
      expect(viewSource, id).toContain(id);
    }
    expect((viewSource.match(/tabIndex=\{-1\}/g) ?? []).length).toBeGreaterThanOrEqual(2);
  });

  it("is registered above the page's early returns, so the hook order never changes", () => {
    const effect = enrollSource.indexOf("focusTargetAfterChange(focusFacts.current, now)");
    expect(effect).toBeGreaterThan(-1);
    expect(effect).toBeLessThan(enrollSource.indexOf("if (controller === null || load === undefined) return null;"));
  });
});

describe("links inside the agreement", () => {
  it("open in a new tab, so reading them never loses the form the person has filled in", () => {
    expect(viewSource).toContain("<BlockView key={index} block={block} newTabLinks />");
  });
});

describe("the buttons and pages the agreement names exist under those names", () => {
  const text = TESTER_AGREEMENT.sections
    .flatMap((section) =>
      section.blocks.flatMap((block) => {
        if (block.kind === "p") return [block.inline.map((part) => (typeof part === "string" ? part : "text" in part ? part.text : part.email)).join("")];
        if (block.kind === "ul")
          return block.items.map((item) => item.map((part) => (typeof part === "string" ? part : "text" in part ? part.text : part.email)).join(""));
        return [];
      }),
    )
    .join("\n");

  it("'Withdraw from the programme', on the programme page, and the Profile page links there", () => {
    expect(text).toContain("'Withdraw from the programme'");
    expect(viewSource).toContain("Withdraw from the programme");
    expect(profileSource).toContain("TesterProgrammeLine");
  });

  it("'Delete my account' on the Profile page", () => {
    expect(text).toContain("'Delete my account' on the Profile page");
    expect(accountCardSource).toContain("Delete my account");
  });

  it("'Disconnect Gmail' on the Integrations page", () => {
    expect(text).toContain("'Disconnect Gmail' on the Integrations page");
    expect(integrationsSource).toContain("Disconnect Gmail");
  });

  it("pausing or deleting a saved search on the Integrations page, which is what a withdrawn tester is told to do to stop the background work", () => {
    expect(text).toContain("Pause or delete a saved search, or disconnect Gmail, on the Integrations page to stop them");
    expect(integrationsSource).toContain("<SavedSearchesCard />");
    expect(savedSearchesCardViewSource).toContain("Pause");
    expect(savedSearchesCardViewSource).toContain("Delete");
  });

  it("'Prefer not to say' on the sponsorship question", () => {
    expect(text).toContain("'Prefer not to say'");
    expect(enrollmentSource).toContain('label: "Prefer not to say"');
  });
});

describe("the page sends the version the content file holds", () => {
  it("takes it from the one constant, and the constant is what the server compares against", () => {
    expect(enrollmentSource).toContain('import { TESTER_AGREEMENT_VERSION } from "../content/testerAgreement";');
    expect(enrollmentSource).toContain("accept_version: TESTER_AGREEMENT_VERSION");
    expect(enrollmentSource).not.toMatch(/accept_version: ["']/);
    expect(TESTER_AGREEMENT_VERSION).toBe("2026-10-06");
  });

  it("is posted by the connected page through the shared request function, nowhere else", () => {
    expect(enrollSource).toContain("submitEnrollment(apiFetch, page.form)");
    expect(enrollSource).toContain("withdrawEnrollment(apiFetch)");
  });
});

// The agreement and the Privacy Policy promise the product never uses the sponsorship answer.
// The API side is pinned in tests/test_tester_enrollment.py; here, the web app: only the module
// that reads and sends it and the page that shows it back to the person know it exists.
const allSources = import.meta.glob(
  ["../**/*.ts", "../**/*.tsx", "!../**/*.test.ts", "!../**/*.test.tsx"],
  { query: "?raw", import: "default", eager: true },
) as Record<string, string>;

describe("nothing in the web app but the enrollment code reads the sponsorship answer", () => {
  it("is named in two files, the model that sends it and the view that shows it back", () => {
    const users = Object.entries(allSources)
      .filter(([, source]) => /needsSponsorship|needs_sponsorship/.test(source))
      // A file next to this one is keyed "./name.ts"; every other is spelled "../dir/name.ts".
      .map(([path]) => (path.startsWith("./") ? `../lib/${path.slice(2)}` : path))
      .sort();
    expect(users).toEqual(["../components/EnrollmentView.tsx", "../lib/enrollment.ts"]);
  });
});
