import { describe, expect, it } from "vitest";
import { PRIVACY, TERMS } from "../content/legal";
import {
  ENROLL_PATH,
  LANDING_PATH,
  LOGIN_PATH,
  PRIVACY_PATH,
  REGISTER_PATH,
  SITE_NAME,
  TERMS_PATH,
  appView,
  documentTitleFor,
  loginPageTitle,
  normalizePath,
  pageTitle,
  type AppView,
  type ViewInput,
} from "./publicRoutes";
import { UPDATE_PASSWORD_PATH, initialLoginMode, loginLinkProblem } from "./passwordReset";

// The whole table of "which screen does this visitor get", run rather than read. The shell
// (App.tsx) only draws what appView says; App.test.tsx pins that it asks with the real session
// and path.

function view(overrides: Partial<ViewInput>): AppView {
  return appView({ signedIn: false, loading: false, pathname: "/", ...overrides });
}

describe("while the session is being looked up", () => {
  it.each(["/", "/login", "/privacy", "/terms", "/applications/123", "/update-password"])(
    "%s shows nothing yet, signed in or not (the answer is not known)",
    (pathname) => {
      expect(view({ loading: true, signedIn: false, pathname })).toBe("boot");
      expect(view({ loading: true, signedIn: true, pathname })).toBe("boot");
    },
  );
});

describe("a signed-out visitor", () => {
  it.each([
    ["/", "landing"],
    ["/login", "login"],
    ["/privacy", "privacy"],
    ["/terms", "terms"],
  ] as const)("reaches %s: %s", (pathname, expected) => {
    expect(view({ pathname })).toBe(expected);
  });

  it.each([
    "/applications",
    "/applications/123",
    "/discover",
    "/hiring-signals",
    "/practice",
    "/profile",
    "/profile/integrations",
    "/does-not-exist",
    "/privacy/extra",
    "/terms/extra",
    "/login/extra",
    "/index.html",
    "/landing",
  ])("is sent to sign-in at %s, and the address is left alone so signing in opens it", (pathname) => {
    expect(view({ pathname })).toBe("login");
  });

  it("is shown the sign-in page at /update-password, which handles a dead reset link", () => {
    expect(view({ pathname: UPDATE_PASSWORD_PATH })).toBe("login");
    expect(view({ pathname: UPDATE_PASSWORD_PATH, hash: "#error=access_denied&error_code=otp_expired" })).toBe(
      "login",
    );
  });
});

describe("a signed-in visitor", () => {
  it("is in the app at / (Today), and at every page the shell routes", () => {
    for (const pathname of [
      "/",
      "/discover",
      "/hiring-signals",
      "/applications",
      "/applications/123",
      "/practice",
      "/profile",
      "/profile/integrations",
      "/update-password",
      "/privacy",
      "/terms",
      "/does-not-exist",
    ]) {
      expect(view({ signedIn: true, pathname }), pathname).toBe("app");
    }
  });

  it("is sent home from /login, because they have no use for the form", () => {
    expect(view({ signedIn: true, pathname: "/login" })).toBe("home_redirect");
    expect(view({ signedIn: true, pathname: "/login/" })).toBe("home_redirect");
    expect(view({ signedIn: true, pathname: REGISTER_PATH })).toBe("home_redirect");
  });

  it("is not sent anywhere from another spelling of /login (it is not that route)", () => {
    expect(view({ signedIn: true, pathname: "/Login" })).toBe("app");
    expect(view({ signedIn: true, pathname: "/login/extra" })).toBe("app");
  });
});

describe("trailing slashes, query strings and fragments", () => {
  it.each([
    ["/privacy/", "privacy"],
    ["/privacy//", "privacy"],
    ["/terms/", "terms"],
    ["/login/", "login"],
    ["/privacy?utm_source=x", "privacy"],
    ["/privacy#section", "privacy"],
    ["/privacy/?a=1#b", "privacy"],
    ["/terms?x=/privacy", "terms"],
    ["/?utm_source=x", "landing"],
    ["/#top", "landing"],
    ["", "landing"],
    ["//", "landing"],
  ] as const)("a signed-out visitor handed the address %j gets %s", (pathname, expected) => {
    expect(view({ pathname })).toBe(expected);
  });

  it("looks only at the path, never at what a query string or fragment spells", () => {
    expect(view({ pathname: "/applications/1", search: "?next=/privacy" })).toBe("login");
    expect(view({ pathname: "/privacy", search: "?next=/applications/1" })).toBe("privacy");
    expect(view({ pathname: "/", search: "?page=/privacy", hash: "#/terms" })).toBe("landing");
  });
});

describe("case and encoding", () => {
  it.each(["/Privacy", "/PRIVACY", "/Terms", "/pRiVaCy"])(
    "%s is not a route: a signed-out visitor gets sign-in, like any unknown path",
    (pathname) => {
      expect(view({ pathname })).toBe("login");
    },
  );

  it("does not decode percent-escapes, so an encoded spelling is not a route either", () => {
    expect(view({ pathname: "/priv%61cy" })).toBe("login");
    expect(view({ pathname: "/privacy%2F" })).toBe("login");
    expect(view({ pathname: "/%70rivacy" })).toBe("login");
  });

  it("does not treat a doubled leading slash as the route", () => {
    expect(view({ pathname: "//privacy" })).toBe("login");
  });
});

describe("the auth server sending a visitor back to the root with an error", () => {
  // The two spellings the auth server uses for an emailed link that expired or was used.
  const deadLinks = [
    "#error=access_denied&error_code=otp_expired&error_description=Email+link+is+invalid+or+has+expired",
    "#error_code=otp_expired",
  ];

  it.each(deadLinks)("keeps a signed-out visitor on sign-in (the dead-link handling) for %s", (hash) => {
    expect(view({ pathname: "/", hash })).toBe("login");
    // The same URL the sign-in page itself reads says it is a dead reset link.
    expect(loginLinkProblem(hash, "", "/")).not.toBeNull();
  });

  it("keeps them there for an error the sign-in page does not call a dead reset link", () => {
    const hash = "#error=access_denied&error_description=Email+link+has+expired";
    expect(view({ pathname: "/", hash })).toBe("login");
    expect(loginLinkProblem(hash, "", "/")).toBeNull();
  });

  it("keeps them there for the same error in the query string, and for an error that is not a reset link", () => {
    expect(view({ pathname: "/", search: "?error=access_denied&error_code=otp_expired" })).toBe("login");
    // A cancelled Google sign-in: sign-in shows the ordinary form, never the landing page.
    expect(view({ pathname: "/", search: "?error=access_denied&error_description=User+cancelled" })).toBe(
      "login",
    );
    expect(view({ pathname: "/", hash: "#error=server_error" })).toBe("login");
  });

  it("does not turn an ordinary address into a sign-in", () => {
    expect(view({ pathname: "/", search: "?utm_source=newsletter&ref=x" })).toBe("landing");
    expect(view({ pathname: "/", hash: "#features" })).toBe("landing");
    // Not an auth error: a parameter that merely contains the word.
    expect(view({ pathname: "/", search: "?q=error" })).toBe("landing");
  });

  it("changes nothing for a signed-in visitor, or on the legal pages", () => {
    expect(view({ signedIn: true, pathname: "/", hash: "#error=access_denied&error_code=otp_expired" })).toBe(
      "app",
    );
    expect(view({ pathname: "/privacy", hash: "#error=access_denied" })).toBe("privacy");
  });

  it("leaves the dead-link URL for the sign-in page to clear to /login, which is still sign-in", () => {
    // Login clears a dead link's error by navigating to LOGIN_PATH; the shell must keep showing
    // the same page there, or the reset form would vanish the moment the URL was cleaned.
    expect(view({ pathname: LOGIN_PATH })).toBe("login");
    expect(view({ pathname: LANDING_PATH })).toBe("landing");
  });
});

describe("the create-account link", () => {
  it("leads to the sign-in page, opened on registration", () => {
    const [path, query] = REGISTER_PATH.split("?");
    expect(path).toBe(LOGIN_PATH);
    expect(view({ pathname: path })).toBe("login");
    expect(initialLoginMode(null, `?${query}`)).toBe("register");
  });
});

describe("the public routes", () => {
  it("are exactly these four, and every one is a bare absolute path", () => {
    expect([LANDING_PATH, LOGIN_PATH, PRIVACY_PATH, TERMS_PATH]).toEqual(["/", "/login", "/privacy", "/terms"]);
  });
});

describe("normalizePath", () => {
  it.each([
    ["/", "/"],
    ["", "/"],
    ["//", "/"],
    ["/privacy", "/privacy"],
    ["/privacy/", "/privacy"],
    ["/privacy///", "/privacy"],
    ["/a/b/", "/a/b"],
    ["/privacy?x=1", "/privacy"],
    ["/privacy/?x=1", "/privacy"],
    ["/privacy#x", "/privacy"],
    ["/?x=1", "/"],
    ["/#x", "/"],
    ["/Privacy", "/Privacy"],
  ])("%j is %j", (input, expected) => {
    expect(normalizePath(input)).toBe(expected);
  });
});

describe("the document title", () => {
  it("is the site name alone for the landing page, and 'Page -- Between Jobs' for the others", () => {
    expect(SITE_NAME).toBe("Between Jobs");
    expect(pageTitle(null)).toBe("Between Jobs");
    expect(pageTitle("Privacy Policy")).toBe("Privacy Policy -- Between Jobs");
  });

  it("names each public screen differently, so two tabs of them can be told apart", () => {
    const titles = [
      documentTitleFor("landing", "/"),
      documentTitleFor("privacy", "/privacy"),
      documentTitleFor("terms", "/terms"),
      loginPageTitle("sign_in"),
      loginPageTitle("register"),
      loginPageTitle("reset"),
    ];
    expect(new Set(titles).size).toBe(titles.length);
    expect(titles).toEqual([
      "Between Jobs",
      "Privacy Policy -- Between Jobs",
      "Terms of Service -- Between Jobs",
      "Sign in -- Between Jobs",
      "Create account -- Between Jobs",
      "Reset your password -- Between Jobs",
    ]);
  });

  it("uses the legal documents' own titles, and the project's ' -- ', never an em dash", () => {
    expect(documentTitleFor("privacy", "/privacy")).toBe(`${PRIVACY.title} -- ${SITE_NAME}`);
    expect(documentTitleFor("terms", "/terms")).toBe(`${TERMS.title} -- ${SITE_NAME}`);
    for (const title of [documentTitleFor("landing", "/"), loginPageTitle("sign_in"), loginPageTitle("register")]) {
      expect(title).not.toContain("—");
    }
  });

  it("leaves the title alone while the session is looked up, on a redirect, and on the sign-in page (which names itself)", () => {
    expect(documentTitleFor("boot", "/privacy")).toBeNull();
    expect(documentTitleFor("home_redirect", "/login")).toBeNull();
    expect(documentTitleFor("login", "/login")).toBeNull();
  });

  it("titles the legal pages inside the signed-in shell too, whatever the spelling of the path", () => {
    expect(documentTitleFor("app", "/privacy")).toBe("Privacy Policy -- Between Jobs");
    expect(documentTitleFor("app", "/privacy/")).toBe("Privacy Policy -- Between Jobs");
    expect(documentTitleFor("app", "/terms")).toBe("Terms of Service -- Between Jobs");
  });

  it("titles the tester-programme page inside the signed-in shell", () => {
    expect(ENROLL_PATH).toBe("/enroll");
    expect(documentTitleFor("app", "/enroll")).toBe("Tester programme -- Between Jobs");
    expect(documentTitleFor("app", "/enroll/")).toBe("Tester programme -- Between Jobs");
    expect(documentTitleFor("app", "/Enroll")).toBe("Between Jobs");
  });

  it("puts the plain title back on every other page of the shell, so leaving the policy does not keep its title", () => {
    for (const pathname of ["/", "/applications", "/applications/123", "/profile/integrations", "/Privacy", "/privacy/extra"]) {
      expect(documentTitleFor("app", pathname), pathname).toBe("Between Jobs");
    }
  });

  it("gives every view a title decision, and the one the view table gives for the same visitor", () => {
    // The title follows the view: whatever appView says for a visitor, documentTitleFor answers.
    for (const [signedIn, pathname, expected] of [
      [false, "/", "Between Jobs"],
      [false, "/privacy", "Privacy Policy -- Between Jobs"],
      [false, "/terms/", "Terms of Service -- Between Jobs"],
      [false, "/login", null],
      [false, "/applications/1", null],
      [true, "/privacy", "Privacy Policy -- Between Jobs"],
      [true, "/discover", "Between Jobs"],
      [true, "/login", null],
    ] as const) {
      const state = appView({ signedIn, loading: false, pathname });
      expect(documentTitleFor(state, pathname), `${signedIn ? "in" : "out"} ${pathname}`).toBe(expected);
    }
  });
});
