import { ApiError } from "./api";

// Account deletion (launch plan P4.5). The server (POST /account/delete)
// removes the person's stored files first (a failure there, answered as a
// retryable 503, changes nothing), then cuts the Gmail link and sessions,
// then deletes the account. An error after the files step can therefore
// leave an account that still exists but is already missing its files and
// Gmail link, and signed out; sending the request again finishes the job.
// Only the 422 and the retryable 503 mean nothing was touched.

export const CONFIRMATION_PHRASE = "delete my account";

function normalise(text: string): string {
  return text.trim().replace(/\s+/g, " ").toLowerCase();
}

export function isConfirmed(typed: string): boolean {
  return normalise(typed) === CONFIRMATION_PHRASE;
}

export function deletionErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 422) {
      return `That did not match. Type "${CONFIRMATION_PHRASE}" exactly to confirm. Nothing was deleted.`;
    }
    if (error.status === 503 && error.retryable === true) {
      return "We could not remove all of your stored files just yet. Nothing was deleted, and it is safe to try again in a minute.";
    }
    if (error.status === 401) {
      return "Your session has ended. Sign in again, then try again.";
    }
    return "Something went wrong while deleting your account. Some of your data may already be removed. Sign in and try again to finish; if it keeps happening, contact support.";
  }
  return "We could not confirm whether the deletion finished. Reload the page: if your account was deleted you will be signed out; otherwise try again.";
}

export type DeletionOutcome = { ok: true } | { ok: false; message: string };

// Runs the deletion. Success ends the local session exactly once; a signOut
// failure is swallowed because the account is already gone and the next
// request 401s into the login screen anyway.
export async function runAccountDeletion(deps: {
  post: () => Promise<unknown>;
  signOut: () => Promise<unknown> | unknown;
}): Promise<DeletionOutcome> {
  try {
    await deps.post();
  } catch (e) {
    return { ok: false, message: deletionErrorMessage(e) };
  }
  try {
    await deps.signOut();
  } catch {
    // Nothing useful to show.
  }
  return { ok: true };
}

export interface AccountCardState {
  typed: string;
  busy: boolean;
  error: string | null;
}

export type AccountCardEvent =
  | { type: "typed"; value: string }
  | { type: "submitted" }
  | { type: "failed"; message: string };

export const initialAccountCardState: AccountCardState = { typed: "", busy: false, error: null };

export function accountCardReducer(state: AccountCardState, event: AccountCardEvent): AccountCardState {
  switch (event.type) {
    case "typed":
      return { ...state, typed: event.value };
    case "submitted":
      return { ...state, busy: true, error: null };
    case "failed":
      return { ...state, busy: false, error: event.message };
  }
}
