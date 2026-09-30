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

export interface Capabilities {
  telegram: boolean;
  // The bot's public username, without the "@", when the server says which one
  // it is; null otherwise. A self-hosted server has its own bot, so the page
  // must not assume the hosted service's.
  telegramBotUsername: string | null;
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
  };
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

/** The bot's name for the card's copy, or null when it isn't known (the card
 *  then says "this server's Telegram bot"). */
export function telegramBotUsername(state: CapabilitiesState): string | null {
  return state.kind === "ready" && state.capabilities.telegram
    ? state.capabilities.telegramBotUsername
    : null;
}
