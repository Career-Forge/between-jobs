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
}

export type CapabilitiesState =
  // Not asked yet, or the ask is in flight.
  | { kind: "checking" }
  | { kind: "ready"; capabilities: Capabilities }
  // The ask failed (network, auth, server error) or the answer wasn't the shape
  // we expect. Nothing that depends on a capability is shown.
  | { kind: "unavailable" };

export type CapabilitiesFetcher = <T>(path: string) => Promise<T>;

/** The body as a `Capabilities`, or null if it isn't one. A missing or
 *  non-boolean flag is not guessed at. */
export function parseCapabilities(body: unknown): Capabilities | null {
  if (typeof body !== "object" || body === null || Array.isArray(body)) return null;
  const telegram = (body as Record<string, unknown>).telegram;
  return typeof telegram === "boolean" ? { telegram } : null;
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
