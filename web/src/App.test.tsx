import { isValidElement, type ReactElement } from "react";
import { Navigate } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import { PublicLayout } from "./components/PublicLayout";
import Landing from "./pages/Landing";
import Login from "./pages/Login";
import Privacy from "./pages/Privacy";
import Terms from "./pages/Terms";

// Which screen the shell draws -- the public landing page, the legal pages and sign-in for a
// signed-out visitor, the app for a signed-in one -- and its reaction to a password recovery,
// run rather than read. The table of "which screen for which address" is lib/publicRoutes.ts's
// own test; this pins that App asks it with the facts the auth context and the router hold, and
// draws what it says.
//
// The recovery part: someone who followed an
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
  search: "",
  hash: "",
  // What the two hooks App calls above its early returns were asked for, one entry per render.
  titles: [] as (string | null)[],
  scrollHookCalls: 0,
}));

// The real auth module and API client pull in the Supabase client, which throws at import
// without env vars; nothing here reaches a network.
vi.mock("./auth", () => ({ useAuth: () => current.auth }));
vi.mock("./lib/supabase", () => ({ supabase: {} }));
// App is called as a plain function here, so its two effect hooks (the page title, and the
// scroll to the top of a new page) are replaced by recorders: what each does is tested on its
// own (lib/publicRoutes.test.ts, lib/scrollOnNavigate.test.ts), and this pins what App asks.
vi.mock("./lib/useDocumentTitle", () => ({
  useDocumentTitle: (title: string | null) => {
    current.titles.push(title);
  },
}));
vi.mock("./lib/useScrollToTopOnNavigate", () => ({
  useScrollToTopOnNavigate: () => {
    current.scrollHookCalls += 1;
  },
}));
vi.mock("react-router-dom", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-router-dom")>();
  return {
    ...actual,
    useLocation: () => ({ pathname: current.pathname, hash: current.hash, search: current.search }),
  };
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
  current.search = "";
  current.hash = "";
  current.titles = [];
  current.scrollHookCalls = 0;
});

// A public screen is the layout around one page: [layout type, page type].
function publicScreen(): [unknown, unknown] {
  const node = shell();
  const child = (node.props as { children?: ReactElement }).children;
  return [node.type, child?.type];
}

describe("App", () => {
  it("shows the sign-in page to a signed-out visitor at an address that is not public, recovery or not", () => {
    current.auth.session = null;
    for (const pathname of ["/applications/123", "/profile/integrations", "/login", "/update-password"]) {
      current.pathname = pathname;
      current.auth.recovery = false;
      expect(shell().type, pathname).toBe(Login);
      current.auth.recovery = true;
      expect(shell().type, pathname).toBe(Login);
    }
  });

  it("shows the landing page at / to a signed-out visitor, and the legal pages at their own addresses", () => {
    current.auth.session = null;
    current.pathname = "/";
    expect(publicScreen()).toEqual([PublicLayout, Landing]);
    current.pathname = "/privacy";
    expect(publicScreen()).toEqual([PublicLayout, Privacy]);
    current.pathname = "/terms/";
    expect(publicScreen()).toEqual([PublicLayout, Terms]);
  });

  it("keeps a signed-out visitor sent back to / with an auth error on sign-in, not the landing page", () => {
    current.auth.session = null;
    current.pathname = "/";
    current.hash = "#error=access_denied&error_code=otp_expired&error_description=expired";
    expect(shell().type).toBe(Login);
    current.hash = "";
    current.search = "?error=access_denied";
    expect(shell().type).toBe(Login);
    current.search = "?utm_source=newsletter";
    expect(publicScreen()).toEqual([PublicLayout, Landing]);
  });

  it("is not recovery's business at all while signed out: the legal pages still show", () => {
    current.auth.session = null;
    current.auth.recovery = true;
    current.pathname = "/privacy";
    expect(publicScreen()).toEqual([PublicLayout, Privacy]);
  });

  it("waits for the session before it decides anything", () => {
    current.auth.loading = true;
    current.auth.recovery = true;
    expect(shell().type).toBe("div");
    expect(shell().props).toMatchObject({ className: "bj-boot" });
    for (const pathname of ["/", "/privacy", "/login", "/applications/1"]) {
      current.pathname = pathname;
      expect(shell().props, pathname).toMatchObject({ className: "bj-boot" });
    }
  });

  it("keeps a signed-in person in the app at / (Today) and on the legal pages, inside the shell", () => {
    for (const pathname of ["/", "/privacy", "/terms", "/applications/123"]) {
      current.pathname = pathname;
      expect(shell().type, pathname).not.toBe(Navigate);
      expect(shell().props, pathname).toMatchObject({ className: "bj-shell" });
    }
  });

  it("sends a signed-in person away from the sign-in page, to Today", () => {
    for (const pathname of ["/login", "/login/"]) {
      current.pathname = pathname;
      const node = shell();
      expect(node.type, pathname).toBe(Navigate);
      expect(node.props, pathname).toMatchObject({ to: "/", replace: true });
    }
  });

  it("sends a signed-in person in a password recovery to the update page even from /login", () => {
    current.auth.recovery = true;
    current.pathname = "/login";
    expect(shell().props).toMatchObject({ to: "/update-password", replace: true });
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

describe("App's page title and scroll", () => {
  // What the title hook was asked for in one render (null is an answer: "leave it alone").
  function titleFor(): string | null {
    const before = current.titles.length;
    shell();
    expect(current.titles.length - before).toBe(1);
    return current.titles[current.titles.length - 1];
  }

  it("asks for each public screen's own title, and for none while the session is looked up", () => {
    current.auth.session = null;
    current.pathname = "/";
    expect(titleFor()).toBe("Between Jobs");
    current.pathname = "/privacy";
    expect(titleFor()).toBe("Privacy Policy -- Between Jobs");
    current.pathname = "/terms";
    expect(titleFor()).toBe("Terms of Service -- Between Jobs");
    // The sign-in page names itself (its form can change under it), so App stays out of it.
    current.pathname = "/login";
    expect(titleFor()).toBeNull();
    current.auth.loading = true;
    current.pathname = "/privacy";
    expect(titleFor()).toBeNull();
  });

  it("titles the legal pages inside the signed-in shell, and puts the plain title back on the other pages", () => {
    current.pathname = "/privacy";
    expect(titleFor()).toBe("Privacy Policy -- Between Jobs");
    current.pathname = "/applications";
    expect(titleFor()).toBe("Between Jobs");
  });

  it("calls both hooks on every render, whichever screen it ends in (so the hook order never changes)", () => {
    const states: [boolean, boolean, string][] = [
      [true, false, "/"], // booting
      [false, false, "/"], // landing
      [false, false, "/login"], // sign-in
      [false, false, "/privacy"], // a public legal page
      [false, true, "/applications/1"], // the shell
      [false, true, "/login"], // the redirect from sign-in
    ];
    for (const [loading, signedIn, pathname] of states) {
      current.auth.loading = loading;
      current.auth.session = signedIn ? { user: { id: "user-1" } } : null;
      current.pathname = pathname;
      const before = { titles: current.titles.length, scrolls: current.scrollHookCalls };
      shell();
      expect(current.titles.length - before.titles, `${pathname} loading=${loading}`).toBe(1);
      expect(current.scrollHookCalls - before.scrolls, `${pathname} loading=${loading}`).toBe(1);
    }
  });
});
