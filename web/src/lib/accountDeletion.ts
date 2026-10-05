import { ApiError } from "./api";

// Account deletion (launch plan P4.5). The server (POST /account/delete)
// deletes the account as its very last step, so any error the client sees
// means the account still exists -- the copy below leans on that.

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
    if (error.status === 401) {
      return "Your session has ended. Sign in again, then retry. Nothing was deleted.";
    }
    if (error.status === 503 && error.retryable === true) {
      return "We could not remove all of your stored files just yet. Nothing was deleted, and it is safe to try again in a minute.";
    }
    return "Something went wrong. Your account was not deleted -- try again, and if it keeps happening, contact support.";
  }
  return "Could not reach the server, so we could not confirm the deletion. Check your connection and try again; if the account was already deleted you will be signed out.";
}
