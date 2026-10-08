// P2.4 -- what the server was started with, asked of it once.
//
// A server can run without a Telegram bot (no TELEGRAM_BOT_TOKEN /
// TELEGRAM_WEBHOOK_SECRET). GET /capabilities reports that, so the Integrations
// page leaves its Telegram card out instead of offering a link code that no bot
// could ever redeem. Same shape of answer as GET /hiring-signals/status, the
// other route that reports a server-side switch.
//
// WHILE IT IS NOT KNOWN, THE CARD IS HIDDEN. Only a definite "telegram: true"
// shows it -- not while the answer is still in flight, and not when asking
// failed -- for the reason hiringSignalsStatus.ts gives: a card that appears a
// moment late beats one that appears and is then taken away.
//
// Pure module: the fetcher is injected (api.ts pulls in the Supabase client,
// which throws at import time without env vars), and there is no React here --
// the hook that binds this to the app is useCapabilities.ts.

export const CAPABILITIES_PATH = "/capabilities";

// How long the ask may take before it counts as failed. The signed-in shell draws nothing until
// the answer is known (components/EnrollmentGate.tsx), so a server that hangs must become
// "unavailable" in a bounded time rather than a blank page for as long as the browser waits.
export const CAPABILITIES_TIMEOUT_MS = 8000;

export interface Capabilities {
  telegram: boolean;
  // The server requires the tester agreement before it will run the costly features
  // (TESTER_PROGRAM_REQUIRED). False when the server says so and when it says nothing: a server
  // that predates the switch has no gate, and one that sends something that is not a boolean has
  // not said "required", which the server enforces anyway (a 403 ENROLLMENT_REQUIRED that says
  // to join), so the page never has to guess it into existence.
  testerProgramRequired: boolean;
  // The bot's public username, without the "@", when the server says which one
  // it is; null otherwise. A self-hosted server has its own bot, so the page
  // must not assume the hosted service's.
  telegramBotUsername: string | null;
  // The server has the Discord app configured, so a Discord link code can be redeemed. The server
  // sends `discord` only when that is so, and a server that says nothing has no Discord: both
  // fields are therefore absent unless it is true, and the card stays hidden.
  discord?: boolean;
  // The address people open to add the Discord app: the operator's own, or else Discord's install
  // link for the application (a query string and all). null when the server has none or what it
  // sent is not an https address.
  discordInstallUrl?: string | null;
}

export type CapabilitiesState =
  // Not asked yet, or the ask is in flight.
  | { kind: "checking" }
  | { kind: "ready"; capabilities: Capabilities }
  // The ask failed (network, auth, server error) or the answer wasn't the shape
  // we expect. Nothing that depends on a capability is shown.
  | { kind: "unavailable" };

export type CapabilitiesFetcher = <T>(path: string) => Promise<T>;

// Telegram's own rule for a username: 5-32 letters, digits and underscores,
// starting with a letter. Anything else is shown as no name rather than
// rendered as whatever the server sent.
const BOT_USERNAME = /^[A-Za-z][A-Za-z0-9_]{4,31}$/;

/** The body as a `Capabilities`, or null if it isn't one. A missing or
 *  non-boolean `telegram` flag is not guessed at; a missing or malformed bot
 *  name just means the name isn't known. */
export function parseCapabilities(body: unknown): Capabilities | null {
  if (typeof body !== "object" || body === null || Array.isArray(body)) return null;
  const record = body as Record<string, unknown>;
  if (typeof record.telegram !== "boolean") return null;
  const name = record.telegram_bot_username;
  return {
    telegram: record.telegram,
    telegramBotUsername: typeof name === "string" && BOT_USERNAME.test(name) ? name : null,
    testerProgramRequired: record.tester_program_required === true,
    ...(record.discord === true
      ? { discord: true, discordInstallUrl: httpsAddress(record.discord_install_url) }
      : {}),
  };
}

// The install address as the card may link to it: an https address, and nothing else. Anything
// the server sent that is not one is shown as no address rather than rendered as whatever it was.
function httpsAddress(value: unknown): string | null {
  if (typeof value !== "string") return null;
  try {
    const url = new URL(value);
    return url.protocol === "https:" && url.username === "" && url.password === "" ? url.href : null;
  } catch {
    return null;
  }
}

export async function loadCapabilities(fetcher: CapabilitiesFetcher): Promise<CapabilitiesState> {
  try {
    const capabilities = parseCapabilities(await fetcher<unknown>(CAPABILITIES_PATH));
    return capabilities === null ? { kind: "unavailable" } : { kind: "ready", capabilities };
  } catch {
    return { kind: "unavailable" };
  }
}

/** Whether the Telegram card may be shown: only for a definite yes. */
export function isTelegramAvailable(state: CapabilitiesState): boolean {
  return state.kind === "ready" && state.capabilities.telegram;
}

/** Whether the Discord card may be shown: only for a definite yes. */
export function isDiscordAvailable(state: CapabilitiesState): boolean {
  return state.kind === "ready" && state.capabilities.discord === true;
}

/** Where people add the Discord app, for the card's link, or null when the operator gave none. */
export function discordInstallUrl(state: CapabilitiesState): string | null {
  return state.kind === "ready" && state.capabilities.discord === true
    ? (state.capabilities.discordInstallUrl ?? null)
    : null;
}

/** The bot's name for the card's copy, or null when it isn't known (the card
 *  then says "this server's Telegram bot"). */
export function telegramBotUsername(state: CapabilitiesState): string | null {
  return state.kind === "ready" && state.capabilities.telegram
    ? state.capabilities.telegramBotUsername
    : null;
}

/** Whether the server requires the tester agreement: only for a definite yes. While the answer
 *  is unknown, and when asking failed, nothing is required of the page (the server still
 *  refuses the costly features, with a message that says to join). */
export function isTesterProgramRequired(state: CapabilitiesState): boolean {
  return state.kind === "ready" && state.capabilities.testerProgramRequired;
}
