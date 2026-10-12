import { describe, expect, it, vi } from "vitest";
import gmailClientSource from "../../../src/between_jobs/api/gmail_client.py?raw";
import type { Capabilities, CapabilitiesState } from "./capabilities";
import {
  GMAIL_READONLY_SCOPE,
  afterReconnectRefusal,
  GMAIL_RECONNECT_REASON,
  type GmailCredentialRow,
  gmailAccess,
  gmailAvailabilityFailed,
  gmailConnection,
  gmailState,
  gmailTestingMode,
  gmailUnavailableMessage,
  isGmailReconnectRequired,
  isGmailUnavailable,
  learnFromGmailRefusal,
  mayOfferGmailDraft,
} from "./gmailAccess";

const base: Capabilities = { telegram: false, telegramBotUsername: null, testerProgramRequired: false };
const ready = (extra: Partial<Capabilities>): CapabilitiesState => ({
  kind: "ready",
  capabilities: { ...base, ...extra },
});

const row = (extra: Partial<GmailCredentialRow> = {}): GmailCredentialRow => ({
  service: "oauth",
  provider: "gmail",
  is_validated: true,
  scope: `https://www.googleapis.com/auth/gmail.compose ${GMAIL_READONLY_SCOPE}`,
  ...extra,
});

class FakeApiError extends Error {
  constructor(
    message: string,
    public readonly code?: string,
    public readonly reason?: string,
  ) {
    super(message);
  }
}

describe("the strings shared with the server", () => {
  it("are the ones gmail_client.py uses", () => {
    expect(gmailClientSource).toContain(`GMAIL_RECONNECT_REASON = "${GMAIL_RECONNECT_REASON}"`);
    expect(gmailClientSource).toContain(`https://www.googleapis.com/auth/gmail.readonly`);
    expect(GMAIL_READONLY_SCOPE).toBe("https://www.googleapis.com/auth/gmail.readonly");
  });
});

describe("whether Gmail exists for this person", () => {
  it("is unknown until the capabilities are known, and when asking for them failed", () => {
    expect(gmailAccess({ kind: "checking" })).toBe("unknown");
    expect(gmailAccess({ kind: "unavailable" })).toBe("unknown");
  });

  it("is 'not on this server' when the server does not say it has Gmail", () => {
    expect(gmailAccess(ready({}))).toBe("not_on_server");
    expect(gmailAccess(ready({ gmail: false }))).toBe("not_on_server");
    // the per-person flag means nothing without the server's own
    expect(gmailAccess(ready({ gmailAllowed: true }))).toBe("not_on_server");
  });

  it("is 'not for you' only for a definite no from a server that has Gmail", () => {
    expect(gmailAccess(ready({ gmail: true, gmailAllowed: false }))).toBe("not_for_you");
    expect(gmailAccess(ready({ gmail: true, gmailAllowed: true }))).toBe("available");
    expect(gmailAccess(ready({ gmail: true }))).toBe("available");
  });

  it("tells a failed ask from one still under way, though both leave it unknown", () => {
    expect(gmailAvailabilityFailed({ kind: "unavailable" })).toBe(true);
    expect(gmailAvailabilityFailed({ kind: "checking" })).toBe(false);
    expect(gmailAvailabilityFailed(ready({ gmail: true }))).toBe(false);
  });

  it("reads the testing-mode switch only as a definite yes", () => {
    expect(gmailTestingMode(ready({ gmail: true, gmailTestingMode: true }))).toBe(true);
    expect(gmailTestingMode(ready({ gmail: true, gmailTestingMode: false }))).toBe(false);
    expect(gmailTestingMode(ready({ gmail: true }))).toBe(false);
    expect(gmailTestingMode({ kind: "checking" })).toBe(false);
    expect(gmailTestingMode({ kind: "unavailable" })).toBe(false);
  });

  it("has a sentence for each way it is missing, and none otherwise", () => {
    expect(gmailUnavailableMessage("not_for_you")).toBe("Gmail isn't available for your account yet.");
    expect(gmailUnavailableMessage("not_on_server")).toBe("Gmail isn't set up on this server.");
    expect(gmailUnavailableMessage("available")).toBeNull();
    expect(gmailUnavailableMessage("unknown")).toBeNull();
  });
});

describe("the person's connection", () => {
  it("is unknown until the credentials are read", () => {
    expect(gmailConnection(null)).toEqual({ kind: "unknown" });
  });

  it("is none without a Gmail row, whatever else they have saved", () => {
    expect(gmailConnection([])).toEqual({ kind: "none" });
    expect(
      gmailConnection([
        row({ service: "llm", provider: "openrouter" }),
        row({ service: "search", provider: "gmail" }),
        row({ service: "oauth", provider: "other" }),
      ]),
    ).toEqual({ kind: "none" });
  });

  it("is connected with a validated row that has read access", () => {
    expect(gmailConnection([row()])).toEqual({ kind: "connected" });
  });

  it("needs a reconnect when Google stopped accepting it, and says that first", () => {
    expect(gmailConnection([row({ is_validated: false })])).toEqual({
      kind: "reconnect_needed",
      cause: "access_ended",
    });
    expect(gmailConnection([row({ is_validated: false, scope: null })])).toEqual({
      kind: "reconnect_needed",
      cause: "access_ended",
    });
  });

  it("needs a reconnect when it was made before the app asked for read access", () => {
    const older = { kind: "reconnect_needed", cause: "older_permissions" };
    expect(gmailConnection([row({ scope: "https://www.googleapis.com/auth/gmail.compose" })])).toEqual(older);
    expect(gmailConnection([row({ scope: null })])).toEqual(older);
    expect(gmailConnection([row({ scope: "" })])).toEqual(older);
  });
});

describe("after the server says Google stopped accepting the connection", () => {
  it("that wins over what the credential list said, whatever it said about a connection that exists", () => {
    const needs = { kind: "reconnect_needed", cause: "access_ended" };
    expect(afterReconnectRefusal({ kind: "connected" }, true)).toEqual(needs);
    expect(afterReconnectRefusal({ kind: "unknown" }, true)).toEqual(needs);
    expect(afterReconnectRefusal({ kind: "reconnect_needed", cause: "older_permissions" }, true)).toEqual(needs);
  });

  it("proves nothing about a connection that is not there", () => {
    expect(afterReconnectRefusal({ kind: "none" }, true)).toEqual({ kind: "none" });
  });

  it("changes nothing without a refusal", () => {
    for (const read of [
      { kind: "unknown" },
      { kind: "none" },
      { kind: "connected" },
      { kind: "reconnect_needed", cause: "older_permissions" },
    ] as const) {
      expect(afterReconnectRefusal(read, false)).toBe(read);
    }
  });
});

describe("the state to draw", () => {
  const connections = [
    { kind: "unknown" },
    { kind: "none" },
    { kind: "connected" },
    { kind: "reconnect_needed", cause: "access_ended" },
  ] as const;

  it("is 'unavailable' whenever Gmail is not for this person or this server, whatever they once connected", () => {
    for (const access of ["not_for_you", "not_on_server"] as const) {
      for (const connection of connections) {
        expect(gmailState(access, connection)).toBe("unavailable");
      }
    }
  });

  it("follows the connection when Gmail is available", () => {
    expect(gmailState("available", { kind: "unknown" })).toBe("unknown");
    expect(gmailState("available", { kind: "none" })).toBe("not_connected");
    expect(gmailState("available", { kind: "connected" })).toBe("connected");
    expect(gmailState("available", { kind: "reconnect_needed", cause: "older_permissions" })).toBe("reconnect_needed");
  });

  it("claims nothing about the connection while it is not known whether Gmail is open to the person", () => {
    // Whatever the credential list says (or has not said yet): availability decides first, and an
    // unknown availability is its own state, never "not connected" or "connected".
    for (const connection of [...connections, { kind: "reconnect_needed", cause: "older_permissions" } as const]) {
      expect(gmailState("unknown", connection)).toBe("availability_unknown");
    }
  });

  it("offers 'Create Gmail draft' only when it can work or the server has to say", () => {
    expect(mayOfferGmailDraft("connected")).toBe(true);
    expect(mayOfferGmailDraft("unknown")).toBe(true);
    expect(mayOfferGmailDraft("availability_unknown")).toBe(false);
    expect(mayOfferGmailDraft("not_connected")).toBe(false);
    expect(mayOfferGmailDraft("reconnect_needed")).toBe(false);
    expect(mayOfferGmailDraft("unavailable")).toBe(false);
  });
});

describe("reading a failed request", () => {
  it("knows the server's reconnect answer by its reason", () => {
    expect(isGmailReconnectRequired(new FakeApiError("x", "PROVIDER_REJECTED", GMAIL_RECONNECT_REASON))).toBe(true);
    expect(isGmailReconnectRequired(new FakeApiError("x", "PROVIDER_REJECTED", "key_rejected"))).toBe(false);
    expect(isGmailReconnectRequired(new FakeApiError("x", "PROVIDER_REJECTED"))).toBe(false);
    expect(isGmailReconnectRequired("gmail_reconnect_required")).toBe(false);
    expect(isGmailReconnectRequired(null)).toBe(false);
    expect(isGmailReconnectRequired({ reason: GMAIL_RECONNECT_REASON })).toBe(false); // not an Error
  });

  it("knows the server's 'not available' answer by its code", () => {
    expect(isGmailUnavailable(new FakeApiError("x", "FEATURE_DISABLED"))).toBe(true);
    expect(isGmailUnavailable(new FakeApiError("x", "NOT_FOUND"))).toBe(false);
    expect(isGmailUnavailable(new Error("FEATURE_DISABLED"))).toBe(false);
    expect(isGmailUnavailable(undefined)).toBe(false);
  });
});

describe("learning from a refused Gmail request", () => {
  const reconnect = () => new FakeApiError("x", "PROVIDER_REJECTED", GMAIL_RECONNECT_REASON);

  function learn(error: unknown) {
    const reconnectNeeded = vi.fn();
    const unavailable = vi.fn();
    learnFromGmailRefusal(error, { reconnectNeeded, unavailable });
    return { reconnectNeeded, unavailable };
  }

  it("remembers the reconnect answer, and does nothing else", () => {
    const { reconnectNeeded, unavailable } = learn(reconnect());
    expect(reconnectNeeded).toHaveBeenCalledTimes(1);
    expect(unavailable).not.toHaveBeenCalled();
  });

  it("asks again what is known when the server says Gmail is not available, and does nothing else", () => {
    const { reconnectNeeded, unavailable } = learn(new FakeApiError("x", "FEATURE_DISABLED"));
    expect(unavailable).toHaveBeenCalledTimes(1);
    expect(reconnectNeeded).not.toHaveBeenCalled();
  });

  it.each([
    ["a provider outage", new FakeApiError("x", "PROVIDER_UNAVAILABLE")],
    ["a refusal about something else", new FakeApiError("x", "PROVIDER_REJECTED", "key_rejected")],
    ["a plain error", new Error("boom")],
    ["something that is not an error", "gmail_reconnect_required"],
    ["nothing", undefined],
  ])("does nothing for %s", (_what, error) => {
    const { reconnectNeeded, unavailable } = learn(error);
    expect(reconnectNeeded).not.toHaveBeenCalled();
    expect(unavailable).not.toHaveBeenCalled();
  });

  it("needs only the callbacks a screen cares about", () => {
    expect(() => learnFromGmailRefusal(reconnect(), { unavailable: () => undefined })).not.toThrow();
    expect(() => learnFromGmailRefusal(new FakeApiError("x", "FEATURE_DISABLED"), {})).not.toThrow();
  });
});
