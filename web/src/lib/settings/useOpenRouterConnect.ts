import { useCallback, useEffect, useState, useSyncExternalStore } from "react";
import { apiFetch } from "../api";
import { SETTINGS_PATH } from "../settingsPaths";
import { parseCredentialRows, type CredentialSummary } from "./credentials";
import { connectStore, startConnect, tabStorage, type ConnectState, type CryptoLike } from "./openrouterOAuth";
import { isRetryable, settingsFailureMessage } from "./settingsErrors";

// The Settings page's hold on the OpenRouter sign-in: starting it, and what became of a return from
// OpenRouter (the exchange runs when the page mounts with one waiting; see openrouterOAuth.ts).
//
// "Connect with OpenRouter" generates the verifier and challenge, keeps the verifier for ten minutes
// in this tab's session storage, and sends the whole window to openrouter.ai. If the browser cannot
// do the secure part (Web Crypto needs https or localhost) or cannot keep the verifier, nothing is
// started and the person is told to paste a key.

export interface OpenRouterConnect {
  start: () => void;
  /** The sign-in is being started or the window is on its way to OpenRouter. */
  busy: boolean;
  /** Why starting failed; the exchange's own failures are in `result`. */
  error: string | null;
  /** This browser cannot run the sign-in at all. */
  unsupported: boolean;
  result: ConnectState<CredentialSummary | null>;
  retry: () => void;
  dismiss: () => void;
}

export interface OpenRouterDeps {
  navigate?: (url: string) => void;
  crypto?: CryptoLike | undefined;
  now?: () => number;
  origin?: () => string;
}

const store = connectStore as unknown as typeof connectStore & { getSnapshot(): ConnectState<CredentialSummary | null> };

async function exchange(code: string, verifier: string): Promise<CredentialSummary | null> {
  const saved = await apiFetch<unknown>("/credentials/openrouter/exchange", {
    method: "POST",
    body: JSON.stringify({ code, code_verifier: verifier }),
  });
  return parseCredentialRows([saved])?.[0] ?? null;
}

export const UNSUPPORTED_MESSAGE =
  "This browser can't run the one-click sign-in here (it needs a secure connection and somewhere to keep the sign-in for a few minutes). Paste a key instead.";

export function useOpenRouterConnect(deps: OpenRouterDeps = {}): OpenRouterConnect {
  const result = useSyncExternalStore(store.subscribe, store.getSnapshot, store.getSnapshot);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [unsupported, setUnsupported] = useState(false);

  // A return from OpenRouter that is waiting is exchanged once, however many times this runs.
  useEffect(() => {
    connectStore.begin(exchange, isRetryable);
  }, []);

  const start = useCallback(() => {
    if (busy) return;
    setBusy(true);
    setError(null);
    connectStore.acknowledge();
    const navigate = deps.navigate ?? ((url: string) => window.location.assign(url));
    const crypto = "crypto" in deps ? deps.crypto : typeof window === "undefined" ? undefined : window.crypto;
    void startConnect({
      crypto,
      storage: tabStorage(),
      callbackUrl: `${(deps.origin ?? (() => window.location.origin))()}${SETTINGS_PATH}`,
      now: (deps.now ?? Date.now)(),
    }).then(
      (started) => {
        if (started.kind === "unsupported") {
          setUnsupported(true);
          setError(UNSUPPORTED_MESSAGE);
          setBusy(false);
          return;
        }
        // Stays busy: the window is leaving for OpenRouter.
        navigate(started.url);
      },
      (e: unknown) => {
        setError(settingsFailureMessage(e, { action: "exchange", provider: "OpenRouter" }, "Couldn't start the sign-in."));
        setBusy(false);
      },
    );
  }, [busy, deps]);

  return {
    start,
    busy,
    error,
    unsupported,
    result,
    retry: () => connectStore.retry(),
    dismiss: () => connectStore.acknowledge(),
  };
}
