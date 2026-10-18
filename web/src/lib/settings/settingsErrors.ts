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

// `serverMessage` is the API's own sentence for the failure, for the one wording that needs a part of it.
type Wording = (provider: string, context: FailureContext, serverMessage: string) => string;

// The way out some providers add after "<name> rejected that key." (Anthropic: create the key inside
// one workspace; Qwen: a key only works on the platform and region it was made on). It is the API's
// own fixed sentence, so it is safe to show; this page's generic advice cannot replace it.
function rejectedHint(serverMessage: string): string {
  const match = /rejected that key\.\s+(\S[\s\S]{0,400})$/.exec(serverMessage);
  return match === null ? "" : ` ${match[1].trim()}`;
}

// A sign-in has no key yet, so it is not "this key" that is being limited.
function rateLimitSentence(provider: string, context: FailureContext): string {
  return `${provider} is rate limiting this ${context.action === "exchange" ? "sign-in" : "key"}. Wait a minute, then try again.`;
}

const BY_REASON: Readonly<Record<string, Wording>> = {
  key_rejected: (provider, context, serverMessage) => {
    // The sign-in hands over a key OpenRouter has just issued: if it is refused, the person has
    // nothing to re-copy.
    if (context.action === "exchange") return `${provider} didn't accept the key it issued. No key was saved. Connect again, or paste a key instead.`;
    return context.action === "save_key"
      ? `${provider} didn't accept that key. Check that you copied all of it, with no spaces or quotes, and that it came from ${provider}.${rejectedHint(serverMessage)} Nothing was saved.`
      : `${provider} no longer accepts the key you saved. Replace it with a new one.`;
  },
  out_of_credit: (provider, context) =>
    withNote(`${provider} says that account has no credit left. Add credit with ${provider}, then try again.`, context),
  // Choosing a model sends a real request with that model, and a provider that does not say "no such
  // model" (OpenRouter) answers a mistyped id the same way as an account problem: the model comes
  // first. A key check has no model in it, so there the refusal does point at the account.
  request_rejected: (provider, context) =>
    withNote(
      context.action === "choose_model"
        ? `${provider} turned the request down, often because of the model chosen. Pick one from the list, or check how you spelled it. If the model is right, check the account with ${provider}.`
        : `${provider} turned the request down. That usually points at the account rather than the key: billing that isn't switched on, or a region the service isn't offered in. Check the account with ${provider}, then try again.`,
      context,
    ),
  rate_limited: (provider, context) => withNote(rateLimitSentence(provider, context), context),
  in_flight_budget: (provider, context) => withNote(rateLimitSentence(provider, context), context),
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
  const { reason, code, status } = fieldsOf(error);
  const serverMessage = error instanceof Error ? error.message : "";
  if (typeof reason === "string" && Object.hasOwn(BY_REASON, reason)) return BY_REASON[reason](context.provider, context, serverMessage);
  if (typeof reason === "string" && SERVER_SENTENCE.has(reason) && serverMessage.trim() !== "") return serverMessage;
  // A gateway or a cold start answers with no envelope at all, and the client's own sentence for
  // that is "Request failed (503)". A failure with a code keeps the server's sentence.
  if (code === undefined && typeof status === "number" && status >= 500) {
    const note = nothingChanged(context);
    return `Between Jobs had a problem on its side (${status}). ${note === "" ? "" : `${note} `}Try again in a moment.`;
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

// Whether a failed OpenRouter exchange can be repeated with the same code. Only a failure of the
// exchange itself can: those carry a reason (a throttle, an outage, a slow answer) and the code was
// not spent. A failure with no reason comes from the check of the key OpenRouter had already issued
// (llm_credentials_routes.exchange_openrouter_code), after the code was used up, so repeating it can
// only be refused: that sign-in has to be started again.
export function isExchangeRetryable(error: unknown): boolean {
  if (isNetworkError(error)) return true;
  return isRetryable(error) && typeof fieldsOf(error).reason === "string";
}

// ── a return from OpenRouter that did not complete ───────────────────────

export interface ConnectFailureView {
  message: string;
  /** "again" starts the sign-in over; "retry" repeats the exchange the browser still holds. */
  next: "again" | "retry";
}

const OPENROUTER = "OpenRouter";

// A sentence that is joined to another one ends in a full stop (a bare "Request failed (503)" did not).
function endSentence(sentence: string): string {
  const trimmed = sentence.trim();
  return /[.!?]$/.test(trimmed) ? trimmed : `${trimmed}.`;
}

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
        message: `${endSentence(
          isNetworkError(failure.error)
            ? "Couldn't reach Between Jobs. Check your connection."
            : settingsFailureMessage(failure.error, { action: "exchange", provider: OPENROUTER }, "Couldn't finish connecting OpenRouter."),
        )} Try finishing it again; if it keeps failing, connect again.`,
        next: "retry",
      };
  }
}
