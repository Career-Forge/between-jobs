import { isValidElement, type ReactElement } from "react";
import { Navigate } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import Login from "./pages/Login";

// The shell's reaction to a password recovery, run rather than read: someone who followed an
// emailed reset link is in a recovery session, and whichever page the link landed them on they
// set the new password first. The rule (lib/passwordReset.ts's recoveryRedirect) is tested on
// its own; this pins that App asks it with the flag the auth context holds, and sends the
// person where it says -- a constant in place of that flag type-checks and passes every test
// that only calls the rule.
//
// `App` is called as a plain function: it only reads two hooks, both replaced below, and
// returns the elements it would render, which are inspected rather than drawn.

const current = vi.hoisted(() => ({
  auth: {
    session: null as { user: { id: string } } | null,
    loading: false,
    recovery: false,
    signOut: () => Promise.resolve(),
  },
  pathname: "/",
}));

// The real auth module and API client pull in the Supabase client, which throws at import
// without env vars; nothing here reaches a network.
vi.mock("./auth", () => ({ useAuth: () => current.auth }));
vi.mock("./lib/supabase", () => ({ supabase: {} }));
vi.mock("react-router-dom", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-router-dom")>();
  return { ...actual, useLocation: () => ({ pathname: current.pathname, hash: "", search: "" }) };
});

function shell(): ReactElement {
  const node = App();
  if (!isValidElement(node)) throw new Error("App rendered nothing");
  return node as ReactElement;
}

beforeEach(() => {
  current.auth.session = { user: { id: "user-1" } };
  current.auth.loading = false;
  current.auth.recovery = false;
  current.pathname = "/";
});

describe("App", () => {
  it("shows the sign-in page to a signed-out visitor, recovery or not", () => {
    current.auth.session = null;
    expect(shell().type).toBe(Login);
    current.auth.recovery = true;
    expect(shell().type).toBe(Login);
  });

  it("waits for the session before it decides anything", () => {
    current.auth.loading = true;
    current.auth.recovery = true;
    expect(shell().type).toBe("div");
    expect(shell().props).toMatchObject({ className: "bj-boot" });
  });

  it("sends a signed-in person in a password recovery to the update page, wherever they landed", () => {
    current.auth.recovery = true;
    for (const pathname of ["/", "/applications/123", "/profile/integrations"]) {
      current.pathname = pathname;
      const node = shell();
      expect(node.type).toBe(Navigate);
      expect(node.props).toMatchObject({ to: "/update-password", replace: true });
    }
  });

  it("leaves them on the update page once they are on it", () => {
    current.auth.recovery = true;
    current.pathname = "/update-password";
    expect(shell().type).not.toBe(Navigate);
    expect(shell().props).toMatchObject({ className: "bj-shell" });
  });

  it("does not redirect a signed-in person who is not in a recovery -- including after one is done", () => {
    current.auth.recovery = false;
    for (const pathname of ["/", "/applications/123", "/update-password"]) {
      current.pathname = pathname;
      expect(shell().type).not.toBe(Navigate);
      expect(shell().props).toMatchObject({ className: "bj-shell" });
    }
  });
});
