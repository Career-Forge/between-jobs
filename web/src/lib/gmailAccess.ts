// What a person can do with Gmail right now, decided from two things the page already knows: what
// the server says about Gmail for THIS caller (GET /capabilities) and the Gmail credential row
// (GET /credentials), which the server keeps when Google stops honouring a connection and marks
// `is_validated: false`.
//
// Four states are shown, and they are the honest ones:
//   - "unavailable": Gmail is not for this person (the server limits it to named people and they
//     are not one of them yet) or the server has no Gmail at all. Nothing to connect; anything
//     they already have is kept, not used, and can be disconnected.
//   - "not_connected": Gmail is available and they have not connected it.
//   - "connected": it works.
//   - "reconnect_needed": a connection exists but it cannot be used until they connect again,
//     because Google stopped accepting it (it expired after the 7 days Google gives an app still
//     in Testing, they revoked it, they changed their password) or it was made before the app
//     asked for read access. The server keeps the stored connection and stops using it.
// plus two that say "not known yet", which are different unknowns and are never mixed up:
//   - "availability_unknown": whether Gmail is open to this person is not known (the capabilities
//     are still being asked, or asking failed). Nothing may be offered or claimed: no Connect, no
//     "Connected", no "Create Gmail draft", whatever the credential list says. Like Telegram,
//     Discord and Hiring signals, an offer waits for a definite yes. Disconnecting a connection
//     that is stored is always safe, so that stays.
//   - "unknown": Gmail IS open to them but the credential list has not been read (or reading it
//     failed). The page claims none of the connection states, and the server answers when the
//     person clicks.
//
// Pure module, no React: the hook that reads /credentials is useGmailConnection.ts.

import type { CapabilitiesState } from "./capabilities";

// The `details.reason` of every server error that says Google no longer honours the person's
// Gmail connection (gmail_client.py's GMAIL_RECONNECT_REASON).
export const GMAIL_RECONNECT_REASON = "gmail_reconnect_required";

export const GMAIL_READONLY_SCOPE = "https://www.googleapis.com/auth/gmail.readonly";

// The Gmail row of GET /credentials: only the fields read here (never a secret; the server sends
// none).
export interface GmailCredentialRow {
  service: string;
  provider: string;
  is_validated: boolean;
  scope: string | null;
}

export type GmailAccess = "unknown" | "not_on_server" | "not_for_you" | "available";

/** Whether Gmail exists for this person: "unknown" until the capabilities are known. */
export function gmailAccess(state: CapabilitiesState): GmailAccess {
  if (state.kind !== "ready") return "unknown";
  if (state.capabilities.gmail !== true) return "not_on_server";
  return state.capabilities.gmailAllowed === false ? "not_for_you" : "available";
}

/** Whether asking the server what Gmail is for this person failed, as opposed to still being
 *  under way: both leave the access "unknown", but only a failure will not fix itself. */
export function gmailAvailabilityFailed(state: CapabilitiesState): boolean {
  return state.kind === "unavailable";
}

/** The operator's testing-mode switch: only a definite yes. */
export function gmailTestingMode(state: CapabilitiesState): boolean {
  return state.kind === "ready" && state.capabilities.gmailTestingMode === true;
}

// Why a connection cannot be used. "access_ended" wins when both are true: reconnecting fixes the
// scope as well, and the first thing the person needs to know is that Google stopped accepting it.
export type ReconnectCause = "access_ended" | "older_permissions";

export type GmailConnection =
  | { kind: "unknown" }
  | { kind: "none" }
  | { kind: "connected" }
  | { kind: "reconnect_needed"; cause: ReconnectCause };

/** The person's connection from the credential list: "unknown" while the list is not loaded. */
export function gmailConnection(rows: readonly GmailCredentialRow[] | null): GmailConnection {
  if (rows === null) return { kind: "unknown" };
  const row = rows.find((r) => r.service === "oauth" && r.provider === "gmail");
  if (row === undefined) return { kind: "none" };
  if (row.is_validated === false) return { kind: "reconnect_needed", cause: "access_ended" };
  if (typeof row.scope !== "string" || !row.scope.includes(GMAIL_READONLY_SCOPE)) {
    return { kind: "reconnect_needed", cause: "older_permissions" };
  }
  return { kind: "connected" };
}

/** The connection once the server has said, in answer to a request, that Google stopped accepting
 *  it: that wins over what the list of credentials said when it was read, unless there was no
 *  connection at all (a refusal about a connection that is not there proves nothing). */
export function afterReconnectRefusal(read: GmailConnection, refused: boolean): GmailConnection {
  return refused && read.kind !== "none" ? { kind: "reconnect_needed", cause: "access_ended" } : read;
}

export type GmailState =
  | "availability_unknown"
  | "unknown"
  | "unavailable"
  | "not_connected"
  | "connected"
  | "reconnect_needed";

/** The state to draw. A person Gmail is not for is "unavailable" whatever they once connected, and
 *  while it is not known whether Gmail is open to them nothing about their connection is claimed
 *  either ("availability_unknown"): the connection is only read once availability is settled. */
export function gmailState(access: GmailAccess, connection: GmailConnection): GmailState {
  if (access === "not_on_server" || access === "not_for_you") return "unavailable";
  if (access === "unknown") return "availability_unknown";
  switch (connection.kind) {
    case "unknown":
      return "unknown";
    case "none":
      return "not_connected";
    case "connected":
      return "connected";
    case "reconnect_needed":
      return "reconnect_needed";
  }
}

/** The sentence for "Gmail is not here for you", by why. Only the two unavailable accesses have
 *  one; the server's own answers use the same wording. */
export function gmailUnavailableMessage(access: GmailAccess): string | null {
  if (access === "not_for_you") return "Gmail isn't available for your account yet.";
  if (access === "not_on_server") return "Gmail isn't set up on this server.";
  return null;
}

/** Whether the "Create Gmail draft" button may be offered: connected, or the connection not read
 *  yet for a person Gmail is open to (the server then decides, as it always has). Never while it is
 *  not known whether Gmail is open to them. */
export function mayOfferGmailDraft(state: GmailState): boolean {
  return state === "connected" || state === "unknown";
}

// A server error read structurally (api.ts's ApiError carries `reason`; importing it would pull in
// the Supabase client, which throws at import time without env vars).
function errorField(error: unknown, field: "reason" | "code"): unknown {
  return error instanceof Error ? (error as Error & Record<string, unknown>)[field] : undefined;
}

/** Whether a failed request says Google no longer honours the Gmail connection. */
export function isGmailReconnectRequired(error: unknown): boolean {
  return errorField(error, "reason") === GMAIL_RECONNECT_REASON;
}

/** Whether a failed Gmail request says Gmail is not available for this person (or this server):
 *  the server's 404 FEATURE_DISABLED. */
export function isGmailUnavailable(error: unknown): boolean {
  return errorField(error, "code") === "FEATURE_DISABLED";
}

/** What a screen does with a refused Gmail request, before it shows anything about it. Each
 *  callback is for the one thing the server may have just contradicted: `reconnectNeeded` when it
 *  says Google stopped accepting the connection, `unavailable` when it says Gmail is not open to
 *  this person or server. A refusal that is neither calls nothing. Call it FIRST and
 *  unconditionally in the failure path: the reconnect answer is also a notice that the screen
 *  shows and returns on, and anything after that return is never reached. */
export function learnFromGmailRefusal(
  error: unknown,
  on: { reconnectNeeded?: () => void; unavailable?: () => void },
): void {
  if (isGmailReconnectRequired(error)) on.reconnectNeeded?.();
  if (isGmailUnavailable(error)) on.unavailable?.();
}
