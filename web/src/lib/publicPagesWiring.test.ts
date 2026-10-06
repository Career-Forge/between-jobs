import { describe, expect, it } from "vitest";
import app from "../App.tsx?raw";
import consentNotice from "../components/ConsentNotice.tsx?raw";
import legalLinks from "../components/LegalLinks.tsx?raw";
import publicLayout from "../components/PublicLayout.tsx?raw";
import login from "../pages/Login.tsx?raw";
import privacyPage from "../pages/Privacy.tsx?raw";
import termsPage from "../pages/Terms.tsx?raw";
import { PRIVACY_PATH, TERMS_PATH } from "./publicRoutes";
import documentTitleHook from "./useDocumentTitle.ts?raw";
import scrollToTopHook from "./useScrollToTopOnNavigate.ts?raw";

// The parts of the public pages that only exist as wiring in files this package cannot render
// with their hooks (App needs the auth context and the router's location; Login needs both).
// What each decides is run elsewhere -- the routing table in publicRoutes.test.ts, what App draws
// for each state in App.test.tsx, the markup in the component tests; these scans pin that the
// pieces are connected, since a deleted line would leave every one of those green.

describe("the shell", () => {
  it("routes the two legal pages for a signed-in visitor, to the same pages the public layout draws", () => {
    expect(app).toContain("<Route path={PRIVACY_PATH} element={<Privacy />} />");
    expect(app).toContain("<Route path={TERMS_PATH} element={<Terms />} />");
    expect(PRIVACY_PATH).toBe("/privacy");
    expect(TERMS_PATH).toBe("/terms");
    expect(app).toContain("<PublicLayout>\n        <Privacy />\n      </PublicLayout>");
    expect(app).toContain("<PublicLayout>\n        <Terms />\n      </PublicLayout>");
    expect(app).toContain("<PublicLayout>\n        <Landing />\n      </PublicLayout>");
  });

  it("links the legal pages from the nav footer, beneath Sign out", () => {
    const footer = app.slice(app.indexOf('<div className="bj-nav-footer">'));
    expect(footer.indexOf("Sign out")).toBeGreaterThan(-1);
    expect(footer.indexOf("<LegalLinks />")).toBeGreaterThan(footer.indexOf("Sign out"));
  });

  it("decides what to draw only through the routing rule, with the session, the path, the query and the fragment", () => {
    expect(app).toContain("appView({");
    for (const field of ["signedIn: session !== null", "loading,", "pathname: location.pathname", "search: location.search", "hash: location.hash"]) {
      expect(app).toContain(field);
    }
    // No test of the session or the path of its own that could disagree with the rule.
    expect(app).not.toMatch(/if \(!session\)/);
  });

  it("sends a signed-in person at /login to Today, and only that", () => {
    expect(app).toContain('if (view === "home_redirect")');
    expect(app).toContain('return <Navigate to="/" replace />;');
  });

  it("starts every new page at the top, through the hook, called above the first early return", () => {
    const call = app.indexOf("useScrollToTopOnNavigate();");
    expect(call).toBeGreaterThan(-1);
    expect(call).toBeLessThan(app.indexOf('if (view === "boot")'));
    expect((app.match(/useScrollToTopOnNavigate\(\)/g) ?? []).length).toBe(1);
  });

  it("titles each screen from the routing module's rule, through the hook, above the first early return", () => {
    const call = app.indexOf("useDocumentTitle(documentTitleFor(view, location.pathname));");
    expect(call).toBeGreaterThan(-1);
    expect(call).toBeLessThan(app.indexOf('if (view === "boot")'));
    // The title follows the view, which is decided before it.
    expect(call).toBeGreaterThan(app.indexOf("const view = appView({"));
    expect(app).not.toMatch(/document\.title\s*=/);
  });
});

describe("the page hooks", () => {
  it("the scroll hook asks the rule with the previous path, the path, the fragment and the navigation type, and scrolls the window to the top", () => {
    expect(scrollToTopHook).toContain("useNavigationType()");
    expect(scrollToTopHook).toContain("shouldScrollToTop({ previousPathname: previousPathname.current, pathname, hash, navigationType })");
    expect(scrollToTopHook).toContain("window.scrollTo(0, 0)");
    // It remembers the path it last saw, after deciding, so a re-run for the same path never scrolls.
    expect(scrollToTopHook.indexOf("window.scrollTo(0, 0)")).toBeLessThan(
      scrollToTopHook.indexOf("previousPathname.current = pathname"),
    );
  });

  it("the title hook writes the title it is given, and nothing for null", () => {
    expect(documentTitleHook).toContain("if (title !== null) document.title = title;");
  });
});

describe("the legal pages", () => {
  it.each([
    ["Privacy", privacyPage],
    ["Terms", termsPage],
  ])("%s scrolls to the section its address names once it is on screen (a reload, or a shared link, has no browser scroll to lean on)", (_name, source) => {
    expect(source).toContain("const { hash } = useLocation();");
    expect(source).toContain("useScrollToHash(true, hash);");
  });
});

describe("the sign-in page", () => {
  it("shows the consent line in both forms, once, above the Google button (which creates an account for a new person too)", () => {
    expect((login.match(/<ConsentNotice/g) ?? []).length).toBe(1);
    expect(login).toContain("<ConsentNotice />");
    // Not behind a mode check: the sign-in form's Google button makes accounts as well.
    expect(login).not.toMatch(/mode === "[a-z_]+" && <ConsentNotice/);
    const notice = login.indexOf("<ConsentNotice />");
    expect(notice).toBeLessThan(login.indexOf('className="bj-google-button"'));
    // In the sign-in/register card, not the password-reset card before it.
    expect(notice).toBeGreaterThan(login.indexOf('if (mode === "reset")'));
  });

  it("names itself in the tab, from the form it is showing, before it can return early", () => {
    const call = login.indexOf("useDocumentTitle(loginPageTitle(mode));");
    expect(call).toBeGreaterThan(-1);
    expect(call).toBeLessThan(login.indexOf('if (mode === "reset")'));
  });

  it("puts the legal links on the card, opening in a new tab so the form is kept", () => {
    expect(login).toContain("<LegalLinks newTab />");
  });

  it("takes a dead link's address back to /login, never to the root (which is the landing page)", () => {
    expect(login).toContain("navigate(LOGIN_PATH, { replace: true })");
    expect(login).not.toContain('navigate("/"');
  });

  it("does not record or block anything on consent yet: no checkbox, no stored flag", () => {
    expect(login).not.toMatch(/type="checkbox"|localStorage|sessionStorage/);
    expect(consentNotice).not.toMatch(/checkbox|localStorage|sessionStorage|onChange|disabled/);
  });
});

describe("the public layout and the legal links", () => {
  it("take their destinations from the routing module, not from literals", () => {
    expect(legalLinks).toContain("PRIVACY_PATH");
    expect(legalLinks).toContain("TERMS_PATH");
    expect(consentNotice).toContain("PRIVACY_PATH");
    expect(consentNotice).toContain("TERMS_PATH");
    expect(publicLayout).toContain("REGISTER_PATH");
    expect(publicLayout).toContain("LOGIN_PATH");
    expect(publicLayout).toContain("<LegalLinks />");
  });
});
