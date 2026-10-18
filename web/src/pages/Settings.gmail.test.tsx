import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { textOfMarkup } from "../testing/markup";
import { codeBetweenCatchAndCall, sliceBetween } from "../testing/sourcePins";
import Settings, { GmailConnectCard } from "./Settings";
import integrationsSource from "./Settings.tsx?raw";

// The Gmail card in the states a person can be in, and what it says about Google's 7-day
// limit. A static render does not run effects, so nothing is fetched: this is only what the card
// draws for what it is given (lib/gmailAccess.ts decides the state, and has its own tests).

const recorded = vi.hoisted(() => ({ caps: { kind: "checking" } as { kind: string } }));

vi.mock("../lib/useCapabilities", () => ({
  useCapabilities: () => recorded.caps,
  refreshCapabilities: () => undefined,
}));
vi.mock("../lib/useHiringSignalsStatus", () => ({ useHiringSignalsEnabled: () => false }));
vi.mock("../components/SavedSearchesCard", () => ({ SavedSearchesCard: () => null }));

const SCOPES = "https://www.googleapis.com/auth/gmail.compose https://www.googleapis.com/auth/gmail.readonly";

function credential(extra: { is_validated?: boolean; scope?: string | null } = {}) {
  return {
    id: "cred-1",
    service: "oauth",
    provider: "gmail",
    model: null,
    is_validated: true,
    updated_at: "2026-10-01T00:00:00Z",
    scope: SCOPES,
    ...extra,
  };
}

function card(props: {
  credential?: ReturnType<typeof credential> | null;
  access?: "unknown" | "not_on_server" | "not_for_you" | "available";
  availabilityFailed?: boolean;
  testingMode?: boolean;
}): string {
  return renderToStaticMarkup(
    <MemoryRouter>
      <GmailConnectCard
        credential={props.credential === undefined ? null : props.credential}
        access={props.access ?? "available"}
        availabilityFailed={props.availabilityFailed ?? false}
        testingMode={props.testingMode ?? false}
        onChanged={async () => undefined}
      />
    </MemoryRouter>,
  );
}

const text = (markup: string) => textOfMarkup(markup);
const SEVEN_DAYS = "Google ends the connection after 7 days";

describe("not available for you", () => {
  it("says so, draws no Connect button, and points at the mail app", () => {
    const markup = card({ access: "not_for_you" });
    const words = text(markup);
    expect(words).toContain("Not available");
    expect(words).toContain("Gmail isn't available for your account yet.");
    expect(words).toContain("open it in your mail app");
    expect(words).not.toContain("Connect Gmail");
    expect(words).not.toContain("Reconnect Gmail");
    expect(words).not.toContain("Disconnect Gmail");
    expect(markup).not.toContain("<button");
  });

  it("says the server has none when it does not", () => {
    const markup = card({ access: "not_on_server" });
    expect(text(markup)).toContain("Gmail isn't set up on this server.");
    expect(markup).not.toContain("<button");
  });

  it("keeps a connection they made before, says it is unused and that nothing was deleted, and lets them disconnect", () => {
    for (const access of ["not_for_you", "not_on_server"] as const) {
      for (const stored of [credential(), credential({ is_validated: false }), credential({ scope: null })]) {
        const markup = card({ access, credential: stored });
        const words = text(markup);
        expect(words).toContain("Not available");
        expect(words).toContain("You have a Gmail connection from before. It is not being used, and nothing has been deleted.");
        expect(words).toContain("Disconnect Gmail");
        expect(words).not.toMatch(/Connect Gmail|Reconnect Gmail/);
        expect(words).not.toContain("Needs reconnect");
        expect(words).not.toContain("Connected");
      }
    }
  });

  it("never says there is a connection from before when there is none", () => {
    for (const access of ["not_for_you", "not_on_server"] as const) {
      const words = text(card({ access }));
      expect(words).not.toContain("You have a Gmail connection from before");
      expect(words).not.toContain("nothing has been deleted");
    }
  });

  it("never mentions the 7-day limit, even in testing mode", () => {
    expect(text(card({ access: "not_for_you", testingMode: true, credential: credential() }))).not.toContain(SEVEN_DAYS);
  });
});

// It is not known whether Gmail is open to this person: the capabilities are still being asked, or
// asking failed. /profile/integrations is reachable before they are known, so the card can be drawn
// in this state. It must not offer Connect (which treats the unknown as a yes) or claim a connection
// (which treats it as available), whatever the credential list holds.
describe("availability not known yet", () => {
  const stored = [
    ["no connection", null],
    ["a working connection", credential()],
    ["a connection Google stopped accepting", credential({ is_validated: false })],
    ["an older connection", credential({ scope: null })],
  ] as const;

  describe.each([
    ["still being asked", false, "Checking", "Checking whether Gmail is available for your account..."],
    ["asking failed", true, "Couldn't check", "We couldn't check whether Gmail is available for your account."],
  ] as const)("(%s)", (_why, availabilityFailed, badge, sentence) => {
    it.each(stored)("with %s: says it is checking, and offers and claims nothing", (_what, held) => {
      const words = text(card({ access: "unknown", availabilityFailed, credential: held }));
      expect(words).toContain(badge);
      expect(words).toContain(sentence);
      expect(words).not.toContain("Connect Gmail");
      expect(words).not.toContain("Reconnect Gmail");
      expect(words).not.toContain("Connected");
      expect(words).not.toContain("Not connected");
      expect(words).not.toContain("Needs reconnect");
      expect(words).not.toContain("Not available");
      expect(words).not.toContain("draft-only, always");
      expect(words).not.toContain(SEVEN_DAYS);
    });

    it("draws no button at all without a stored connection", () => {
      expect(card({ access: "unknown", availabilityFailed })).not.toContain("<button");
    });

    it.each(stored.filter(([, held]) => held !== null))(
      "keeps Disconnect Gmail, and only that, for %s",
      (_what, held) => {
        const markup = card({ access: "unknown", availabilityFailed, credential: held });
        expect(text(markup)).toContain("Disconnect Gmail");
        expect(markup.match(/<button/g)).toHaveLength(1);
      },
    );
  });

  it("tells a person who has to try again what to do", () => {
    expect(text(card({ access: "unknown", availabilityFailed: true }))).toContain("Reload the page to try again.");
    expect(text(card({ access: "unknown", availabilityFailed: false }))).not.toContain("Reload");
  });
});

describe("not connected", () => {
  it("offers Connect Gmail, and describes what Gmail does, draft-only", () => {
    const markup = card({});
    const words = text(markup);
    expect(words).toContain("Not connected");
    expect(words).toContain("Connect Gmail");
    expect(words).toContain("draft-only, always");
    expect(words).not.toContain("Disconnect Gmail");
    expect(words).not.toContain("Reconnect Gmail");
  });

  it("is NOT what an unknown availability shows: that offers nothing (see 'availability not known yet')", () => {
    const words = text(card({ access: "unknown" }));
    expect(words).not.toContain("Connect Gmail");
    expect(words).not.toContain("Not connected");
  });

  it("does not mention the 7-day limit before there is a connection to lose", () => {
    expect(text(card({ testingMode: true }))).not.toContain(SEVEN_DAYS);
  });
});

describe("connected", () => {
  it("says Connected and offers only Disconnect Gmail", () => {
    const words = text(card({ credential: credential() }));
    expect(words).toContain("Connected");
    expect(words).toContain("Disconnect Gmail");
    expect(words).not.toContain("Connect Gmail"); // "Disconnect Gmail" has a small c
    expect(words).not.toContain("Reconnect Gmail");
    expect(words).not.toContain("Not connected");
    expect(words).not.toContain("Needs reconnect");
  });

  it("gives the 7-day notice only in testing mode", () => {
    const quiet = text(card({ credential: credential(), testingMode: false }));
    expect(quiet).not.toContain(SEVEN_DAYS);
    expect(quiet).not.toContain("beta");

    const noisy = text(card({ credential: credential(), testingMode: true }));
    expect(noisy).toContain("Gmail is still in beta, so Google ends the connection after 7 days");
    expect(noisy).toContain("expect to reconnect about once a week");
    expect(noisy).toContain("Everything already saved stays.");
  });
});

describe("needs reconnect", () => {
  it("when Google stopped accepting it: says what that means and keeps nothing hidden", () => {
    const markup = card({ credential: credential({ is_validated: false }) });
    const words = text(markup);
    expect(words).toContain("Needs reconnect");
    expect(words).toContain("Google stopped accepting this connection.");
    expect(words).toContain("Until you reconnect, drafts aren't created in Gmail and replies aren't checked.");
    expect(words).toContain("Everything already saved stays");
    expect(words).toContain("Reconnect Gmail");
    expect(words).toContain("Disconnect Gmail");
    expect(words).not.toContain("Connected");
    expect(words).not.toContain("Not connected");
  });

  it("when it was made before reply-checking: says that instead", () => {
    const words = text(card({ credential: credential({ scope: "https://www.googleapis.com/auth/gmail.compose" }) }));
    expect(words).toContain("Needs reconnect");
    expect(words).toContain("Connected before reply-checking existed -- reconnect to grant the extra read access.");
    expect(words).toContain("Reconnect Gmail");
    expect(words).not.toContain("Google stopped accepting");
  });

  it("does not give the 7-day notice (that is for a working connection), even in testing mode", () => {
    expect(text(card({ credential: credential({ is_validated: false }), testingMode: true }))).not.toContain(SEVEN_DAYS);
  });
});

describe("the page", () => {
  beforeEach(() => {
    recorded.caps = { kind: "checking" };
  });

  it("does not draw the Gmail card until the credentials are read (a static render has not read them)", () => {
    const markup = renderToStaticMarkup(
      <MemoryRouter>
        <Settings />
      </MemoryRouter>,
    );
    expect(markup).not.toContain("<h2>Gmail</h2>");
  });

  it("reads the capabilities for the card: the card source asks /capabilities' answer, not a guess", async () => {
    const source = (await import("./Settings.tsx?raw")).default as string;
    const page = source.slice(source.indexOf("export default function Settings"), source.indexOf("export function SearchProviderCard"));
    expect(page).toContain("gmailAccess(capabilities)");
    expect(page).toContain("gmailTestingMode(capabilities)");
    // Disconnecting is a plain credential delete that nothing gates
    expect(source).toContain('await apiFetch("/credentials/oauth/gmail", { method: "DELETE" });');
    expect(source).toContain("Disconnect Gmail");
  });

  it("starts the connection at the server's route and goes where Google's address says", () => {
    expect(integrationsSource).toContain('apiFetch<{ authorize_url: string }>("/profile/integrations/gmail/connect")');
    expect(integrationsSource).toContain("window.location.href = result.authorize_url;");
  });

  it("hands the card whether asking the server failed, from the capabilities it read", () => {
    expect(integrationsSource).toContain("availabilityFailed={gmailAvailabilityFailed(capabilities)}");
  });

  it("learns from a refusal: when the server says Gmail is not available, the capabilities are asked again", () => {
    // First thing in the failure path and with no condition around it: anything in front of it (an
    // early return, a guard that is never true) would leave the card believing what the server just
    // contradicted. What the helper does with each kind of error is run in gmailAccess.test.ts.
    const connect = sliceBetween(integrationsSource, "async function connect()", "async function disconnect()");
    expect(connect).not.toBeNull();
    const call = "learnFromGmailRefusal(e, { unavailable: refreshCapabilities });";
    expect(codeBetweenCatchAndCall(connect ?? "", call)).toBe("");
    expect(connect?.indexOf("problemOf(e")).toBeGreaterThan(connect?.indexOf(call) ?? Infinity);
  });
});
