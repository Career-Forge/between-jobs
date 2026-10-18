// What a failure on the Settings page says, and whether asking again could change it.
//
// The API answers a failed key check, model list, model choice or OpenRouter sign-in with its error
// envelope: a `code` and, for a provider's refusal, a `details.reason` (read into `ApiError.reason`)
// saying which kind it was (src/between_jobs/api/llm_client.py and provider_checks.py). Its sentence
// is already plain and never repeats anything the provider said, but it cannot say what to do on
// THIS page, so each reason is worded here, in the page's terms, with the provider's name filled in.
// A reason this table does not know falls back to the server's own sentence, and a failure that is
// not the API's at all (the network, a blocked request) gets a plain sentence of its own: the raw
// message of a failed fetch ("Failed to fetch") tells a person nothing.
//
// Pure module: an error is read structurally (api.ts pulls in the Supabase client).

import { friendlyApiMessage } from "../rateLimitMessage";
import type { ConnectFailure } from "./openrouterOAuth";

export type SettingsAction = "save_key" | "list_models" | "choose_model" | "exchange" | "remove_key";

export interface FailureContext {
  action: SettingsAction;
  /** "OpenAI", "Qwen Cloud": the provider the action was about. */
  provider: string;
}

interface ErrorFields {
  code?: unknown;
  status?: unknown;
  reason?: unknown;
  retryable?: unknown;
}

function fieldsOf(error: unknown): ErrorFields {
  return error instanceof Error ? (error as Error & ErrorFields) : {};
}

// A failed fetch never reached the API (offline, blocked, the server down): fetch throws a
// TypeError, and api.ts lets it through.
export function isNetworkError(error: unknown): boolean {
  return error instanceof TypeError;
}

export const NETWORK_MESSAGE = "Couldn't reach Between Jobs. Check your connection and try again.";

// What was NOT done, in the words used after a failed attempt: a key is only stored after the
// provider accepts it, and a model is only stored after one real request proves it.
function nothingChanged(context: FailureContext): string {
  switch (context.action) {
    case "save_key":
      return "Nothing was saved.";
    case "choose_model":
      return "Your default model was not changed.";
    case "exchange":
      return "No key was saved.";
    case "list_models":
    case "remove_key":
      return "";
  }
}

function withNote(sentence: string, context: FailureContext): string {
  const note = nothingChanged(context);
  return note === "" ? sentence : `${sentence} ${note}`;
}

type Wording = (provider: string, context: FailureContext) => string;

const BY_REASON: Readonly<Record<string, Wording>> = {
  key_rejected: (provider, context) =>
    context.action === "save_key"
      ? `${provider} didn't accept that key. Check that you copied all of it, with no spaces or quotes, and that it is a ${provider} key. Nothing was saved.`
      : `${provider} no longer accepts the key you saved. Replace it with a new one.`,
  out_of_credit: (provider, context) =>
    withNote(`${provider} says that account has no credit left. Add credit with ${provider}, then try again.`, context),
  request_rejected: (provider, context) =>
    withNote(
      `${provider} turned the request down. That usually points at the account rather than the key: billing that isn't switched on, or a region the service isn't offered in. Check the account with ${provider}, then try again.`,
      context,
    ),
  rate_limited: (provider, context) => withNote(`${provider} is rate limiting this key. Wait a minute, then try again.`, context),
  in_flight_budget: (provider, context) => withNote(`${provider} is rate limiting this key. Wait a minute, then try again.`, context),
  timeout: (provider, context) => withNote(`${provider} took too long to answer. Try again in a moment.`, context),
  unreachable: (provider, context) => withNote(`Couldn't reach ${provider}. Try again in a moment.`, context),
  provider_error: (provider, context) => withNote(`${provider} couldn't answer that right now. Try again in a moment.`, context),
  empty_response: (provider, context) =>
    withNote(`${provider} answered, but in a form we couldn't read. Try again in a moment.`, context),
  response_too_large: (provider) => `${provider}'s answer was larger than this server will read.`,
  model_not_found: (provider) =>
    `${provider} has no model with that id on this account. Pick one from the list, or check the spelling. Your default model was not changed.`,
  model_retired: (provider) => `${provider} has retired that model. Pick a current one. Your default model was not changed.`,
  oauth_code_rejected: () =>
    "OpenRouter didn't accept that sign-in. It may have been used already or have run out of time (they last ten minutes). Connect again, or paste a key instead.",
  oauth_malformed: () => "The sign-in came back in a form we can't use. Connect again, or paste a key instead.",
  model_required_to_check_key: (provider) =>
    `${provider} has no free way to check a key, so it needs a model id too: it is checked with one tiny request. Enter a model id and try again.`,
};

// Reasons whose server sentence is the better one: it names the exact problem and is safe to show.
const SERVER_SENTENCE = new Set(["key_malformed", "base_url_refused"]);

export function settingsFailureMessage(error: unknown, context: FailureContext, fallback: string): string {
  if (isNetworkError(error)) return NETWORK_MESSAGE;
  const { reason } = fieldsOf(error);
  if (typeof reason === "string" && Object.hasOwn(BY_REASON, reason)) return BY_REASON[reason](context.provider, context);
  if (typeof reason === "string" && SERVER_SENTENCE.has(reason) && error instanceof Error && error.message.trim() !== "") {
    return error.message;
  }
  return friendlyApiMessage(error, fallback);
}

// Whether the same request, sent again, can come out differently: a network failure, a busy or
// unreachable provider, a throttle. A refused key, an unknown model or a rejected sign-in cannot.
export function isRetryable(error: unknown): boolean {
  if (isNetworkError(error)) return true;
  const { retryable, status, code } = fieldsOf(error);
  if (typeof retryable === "boolean") return retryable;
  if (code === "RATE_LIMITED" || code === "PROVIDER_RATE_LIMITED" || code === "PROVIDER_UNAVAILABLE") return true;
  return typeof status === "number" && status >= 500;
}

// ── a return from OpenRouter that did not complete ───────────────────────

export interface ConnectFailureView {
  message: string;
  /** "again" starts the sign-in over; "retry" repeats the exchange the browser still holds. */
  next: "again" | "retry";
}

const OPENROUTER = "OpenRouter";

export function connectFailureView(failure: ConnectFailure): ConnectFailureView {
  switch (failure.kind) {
    case "denied":
      return { message: "You didn't allow the connection at OpenRouter. Nothing was connected. You can try again, or paste a key instead.", next: "again" };
    case "state_mismatch":
      return {
        message:
          "That sign-in didn't match the one this tab started, so it was thrown away. This can happen if you opened the link in another tab or window. Nothing was connected.",
        next: "again",
      };
    case "expired":
      return { message: "That sign-in ran out of time (they last ten minutes). Nothing was connected. Connect again.", next: "again" };
    case "no_pending":
      return {
        message:
          "This tab didn't start that sign-in, so it was ignored. Nothing was connected. Press Connect with OpenRouter in the tab you want to use.",
        next: "again",
      };
    case "no_code":
      return { message: "OpenRouter sent you back without finishing the sign-in. Nothing was connected. Connect again.", next: "again" };
    case "rejected":
      return {
        message: settingsFailureMessage(failure.error, { action: "exchange", provider: OPENROUTER }, "OpenRouter's key couldn't be saved. Connect again."),
        next: "again",
      };
    case "retryable":
      return {
        message: `${settingsFailureMessage(failure.error, { action: "exchange", provider: OPENROUTER }, "Couldn't finish connecting OpenRouter.")} Try finishing it again; if it keeps failing, connect again.`,
        next: "retry",
      };
  }
}
