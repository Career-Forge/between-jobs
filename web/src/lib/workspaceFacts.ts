// What the page tells the band: its facts, read off the generation hook and the account reads. Pure,
// so each answer is a test. The rows that use them are lib/nextStep/pages/workspace.ts.

import { flaggedCount } from "./claimWarnings";
import { gmailConnection, gmailState, type GmailAccess, type GmailCredentialRow } from "./gmailAccess";
import type { PrepareApplicationResult } from "./generateTypes";
import type { GmailFact, GenerationFact } from "./nextStep/pages/workspace";
import { fitCeiling } from "./workspaceTrails";
import type { GenerateState, ReadState } from "./useGeneration";

export interface GenerationInputs {
  read: ReadState;
  state: GenerateState;
  /** A resume exists (the page's read, or a run in this visit). */
  hasResume: boolean;
  /** A PDF of the resume was downloaded in this visit. */
  downloaded: boolean;
}

// What is known about the resume right now, for the band.
//
//  - a request is out, or the last one failed in this visit: that, first;
//  - a result in view decides: held back (the fit check declined, no resume came of it), or drafted
//    with the number of claims it flagged;
//  - no result in view: while the stored one is being read, "reading" (the band waits, it does not
//    guess); once the read is over (it answered, or failed) a resume that exists is "drafted" with no
//    count -- nothing is known about its claim check -- and no resume is "none".
export function generationFact(input: GenerationInputs): GenerationFact {
  const { state } = input;
  if (state.kind === "generating") return { state: "generating" };
  if (state.kind === "error") return { state: "failed", at: state.at };
  if (state.kind === "ready") return factOfResult(state.result, input);
  if (input.read === "loading" && state.kind === "idle") return { state: "reading" };
  return input.hasResume ? { state: "drafted", flagged: null, downloaded: input.downloaded } : { state: "none" };
}

function factOfResult(result: PrepareApplicationResult, input: GenerationInputs): GenerationFact {
  if (result.resume !== null) return { state: "drafted", flagged: flaggedCount(result.warnings), downloaded: input.downloaded };
  if (result.fit) return { state: "held_back", ceiling: fitCeiling(result.fit), earlierResume: input.hasResume };
  // Nothing was produced and there was no fit to read: not a held-back attempt. An older resume still
  // exists if the page said so, and nothing is known about its claim check.
  return input.hasResume ? { state: "drafted", flagged: null, downloaded: input.downloaded } : { state: "none" };
}

// The fields of a credential row the Gmail check reads. A row missing one is read as it is: a missing
// `is_validated` is not `false`, so a row the server did not mark as ended is not called ended.
function gmailRowOf(row: Record<string, unknown>): GmailCredentialRow {
  return {
    service: typeof row.service === "string" ? row.service : "",
    provider: typeof row.provider === "string" ? row.provider : "",
    is_validated: row.is_validated !== false,
    scope: typeof row.scope === "string" ? row.scope : null,
  };
}

// Gmail as the band can say it: decided the way every Gmail surface decides it (gmailState), from what
// the server says Gmail is for this person and the credential list. Gmail not being open to a person is
// "not connected" (they have no connection that can read a reply); anything not yet known is "unknown",
// and the band then says nothing about Gmail.
export function gmailFactOf(access: GmailAccess, rows: readonly Record<string, unknown>[] | null): GmailFact {
  const state = gmailState(access, gmailConnection(rows === null ? null : rows.map(gmailRowOf)));
  switch (state) {
    case "connected":
      return "connected";
    case "reconnect_needed":
      return "reconnect_needed";
    case "not_connected":
    case "unavailable":
      return "not_connected";
    default:
      return "unknown";
  }
}
