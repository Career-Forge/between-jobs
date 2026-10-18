// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { connectStore } from "./lib/settings/openrouterOAuth";
import { render } from "./testing/render";
import { settle } from "./testing/forms";

type Listener = (event: string, session: unknown) => void;
const listeners: Listener[] = [];

vi.mock("./lib/supabase", () => ({
  supabase: {
    auth: {
      getSession: () => Promise.resolve({ data: { session: null } }),
      onAuthStateChange: (listener: Listener) => {
        listeners.push(listener);
        return { data: { subscription: { unsubscribe: () => undefined } } };
      },
    },
  },
}));

import { AuthProvider } from "./auth";

afterEach(() => {
  connectStore.acknowledge();
  listeners.length = 0;
});

describe("signing out", () => {
  it("drops a code from OpenRouter that was waiting, so whoever signs in next cannot spend it", async () => {
    render(
      <AuthProvider>
        <p>child</p>
      </AuthProvider>,
    );
    await settle();
    connectStore.receive({ kind: "ready", code: "the-code", verifier: "the-verifier", expiresAt: Date.now() + 600_000 });
    const exchange = vi.fn(async () => null);
    act(() => listeners[0]("SIGNED_OUT", null));
    connectStore.begin(exchange, () => false);
    expect(exchange).not.toHaveBeenCalled();
  });

  it("keeps it through the other events (a token refresh is not a sign-out)", async () => {
    render(
      <AuthProvider>
        <p>child</p>
      </AuthProvider>,
    );
    await settle();
    connectStore.receive({ kind: "ready", code: "the-code", verifier: "the-verifier", expiresAt: Date.now() + 600_000 });
    const exchange = vi.fn(async () => null);
    act(() => listeners[0]("TOKEN_REFRESHED", { user: { id: "u" } }));
    connectStore.begin(exchange, () => false);
    expect(exchange).toHaveBeenCalledTimes(1);
  });
});
