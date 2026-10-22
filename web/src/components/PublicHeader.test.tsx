// @vitest-environment jsdom
import { Link, MemoryRouter, useNavigate } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { SITE_TABS } from "../content/siteNav";
import { expectNoSeriousA11yViolations } from "../testing/a11y";
import { buttonNamed, click, installViewport, keydown, query, render } from "../testing/render";
import { PublicHeader } from "./PublicHeader";
import { MAIN_CONTENT_ID } from "./SkipLink";

// The public header as a person uses it: which tab is current, which Create account is the filled one, and the
// menu that holds the same links under 960px (a disclosure: Escape closes it and returns focus to its button,
// choosing a link closes it and lands focus on the page, widening the window closes it).

const text = (element: Element) => (element.textContent ?? "").replace(/\s+/g, " ").trim();

function open(path = "/", filled = true) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <PublicHeader filled={filled} />
      <main id={MAIN_CONTENT_ID} tabIndex={-1}>
        <h1>Page</h1>
      </main>
    </MemoryRouter>,
  ).container;
}

function BackAndForward() {
  const navigate = useNavigate();
  return (
    <>
      <button type="button" onClick={() => navigate(-1)}>
        Back
      </button>
      <button type="button" onClick={() => navigate(1)}>
        Forward
      </button>
    </>
  );
}

const menuButton = (root: ParentNode) => query<HTMLButtonElement>(root, "button.bj-public-menu-button");
const panel = (root: ParentNode) => root.querySelector<HTMLElement>("nav.bj-public-menu");

describe("the tabs", () => {
  it("are the site's four pages in order, in a navigation named Main", () => {
    const root = open();
    const nav = query(root, 'nav[aria-label="Main"]');
    expect([...nav.querySelectorAll("a")].map((link) => [text(link), link.getAttribute("href")])).toEqual([
      ["Product", "/"],
      ["How it works", "/how-it-works"],
      ["Open source", "/open-source"],
      ["Privacy", "/privacy"],
    ]);
    expect(SITE_TABS).toHaveLength(4);
  });

  it.each([
    ["/", "Product"],
    ["/how-it-works", "How it works"],
    ["/how-it-works/", "How it works"],
    ["/open-source", "Open source"],
    ["/privacy", "Privacy"],
  ])("mark %s as the current page: %s, with aria-current and the selection style, and no other tab", (path, label) => {
    const root = open(path);
    const current = [...root.querySelectorAll('nav[aria-label="Main"] a[aria-current]')];
    expect(current.map((link) => text(link))).toEqual([label]);
    expect(current[0].getAttribute("aria-current")).toBe("page");
    expect(current[0].classList.contains("bj-selectable")).toBe(true);
  });

  it("mark no tab on the Terms page, which is a page of the site but not a tab", () => {
    const root = open("/terms");
    expect(root.querySelector("[aria-current]")).toBeNull();
  });

  it("go to the home page, and to a hash on it, with the same current mark", () => {
    const root = open("/?utm_source=x#top");
    expect([...root.querySelectorAll("[aria-current]")].map((link) => text(link))).toEqual(["Product"]);
  });
});

describe("the way in", () => {
  it("is Sign in as a plain link and Create account as the page's one cyan button where the page has no hero", () => {
    const root = open("/how-it-works", true);
    const nav = query(root, 'nav[aria-label="Account"]');
    const [signIn, create] = [...nav.querySelectorAll("a")];
    expect([text(signIn), signIn.getAttribute("href"), signIn.className]).toEqual(["Sign in", "/login", "bj-public-signin"]);
    expect([text(create), create.getAttribute("href")]).toEqual(["Create account", "/login?mode=register"]);
    expect(create.classList.contains("bj-button-link-primary")).toBe(true);
    expect(root.querySelectorAll(".bj-button-link-primary")).toHaveLength(1);
  });

  it("makes Create account outlined on the home page, whose hero holds the page's one cyan fill", () => {
    const root = open("/", false);
    expect(root.querySelector(".bj-button-link-primary")).toBeNull();
    expect(text(query(root, "a.bj-button-link"))).toBe("Create account");
  });
});

describe("the menu", () => {
  it("starts closed: a button named Menu, not expanded, and no panel in the page (so no second copy of the links)", () => {
    const root = open();
    const button = menuButton(root);
    expect(text(button)).toBe("Menu");
    expect(button.type).toBe("button");
    expect(button.getAttribute("aria-expanded")).toBe("false");
    expect(button.hasAttribute("aria-controls")).toBe(false);
    expect(panel(root)).toBeNull();
    expect(root.querySelectorAll("a[href='/privacy']")).toHaveLength(1);
  });

  it("opens on a press into a navigation after the button, with the four tabs, Sign in and Create account", () => {
    const root = open("/open-source");
    const button = menuButton(root);
    click(button);
    const menu = panel(root);
    expect(menu).not.toBeNull();
    expect(button.getAttribute("aria-expanded")).toBe("true");
    expect(button.getAttribute("aria-controls")).toBe(menu?.id);
    expect(menu?.getAttribute("aria-label")).toBe("Menu");
    expect(button.compareDocumentPosition(menu as Node) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect([...(menu?.querySelectorAll("a") ?? [])].map((link) => text(link))).toEqual(["Product", "How it works", "Open source", "Privacy", "Sign in", "Create account"]);
    // The current page is marked here too.
    expect([...(menu?.querySelectorAll("[aria-current]") ?? [])].map((link) => text(link))).toEqual(["Open source"]);
    // A press on the button again closes it.
    click(button);
    expect(panel(root)).toBeNull();
    expect(button.getAttribute("aria-expanded")).toBe("false");
  });

  it("keeps the one cyan Create account in the page's markup in both states where the page has no hero", () => {
    const root = open("/privacy", true);
    click(menuButton(root));
    // The wide layout's copy is hidden by the stylesheet under 960px, so a reader meets one of the two.
    expect(panel(root)?.querySelectorAll(".bj-button-link-primary")).toHaveLength(1);
  });

  it("closes on Escape from inside the panel and puts focus back on the button", () => {
    const root = open();
    const button = menuButton(root);
    click(button);
    const first = query<HTMLAnchorElement>(panel(root) as HTMLElement, "a");
    first.focus();
    expect(keydown("Escape")).toBe(false);
    expect(panel(root)).toBeNull();
    expect(document.activeElement).toBe(button);
  });

  it("closes on Escape from the button itself, and leaves Escape alone when it is closed", () => {
    const root = open();
    const button = menuButton(root);
    expect(keydown("Escape", { target: button })).toBe(true);
    click(button);
    button.focus();
    expect(keydown("Escape")).toBe(false);
    expect(panel(root)).toBeNull();
    expect(document.activeElement).toBe(button);
  });

  it("closes when a link in it is chosen, and moves focus to the page's main, which has changed under the person", () => {
    const root = open();
    click(menuButton(root));
    const link = [...(panel(root)?.querySelectorAll("a") ?? [])].find((candidate) => text(candidate) === "How it works") as HTMLAnchorElement;
    click(link);
    expect(panel(root)).toBeNull();
    expect(document.activeElement).toBe(query(root, `#${MAIN_CONTENT_ID}`));
  });

  it("closes when the person goes anywhere else: the wordmark, a link in the page, the same page again", () => {
    const root = render(
      <MemoryRouter initialEntries={["/privacy"]}>
        <PublicHeader filled />
        <main id={MAIN_CONTENT_ID} tabIndex={-1}>
          <Link to="/open-source">In the page</Link>
        </main>
      </MemoryRouter>,
    ).container;
    const wordmark = query<HTMLAnchorElement>(root, "a.bj-public-wordmark");
    const inPage = [...root.querySelectorAll("main a")].find((link) => text(link) === "In the page") as HTMLAnchorElement;

    click(menuButton(root));
    expect(panel(root)).not.toBeNull();
    click(wordmark);
    expect(panel(root)).toBeNull();
    expect(menuButton(root).getAttribute("aria-expanded")).toBe("false");

    click(menuButton(root));
    click(inPage);
    expect(panel(root)).toBeNull();

    // The same page again is still a navigation, and closes it.
    click(menuButton(root));
    click(wordmark);
    expect(panel(root)).toBeNull();
  });

  it("closes when the browser goes back or forward", () => {
    const root = render(
      <MemoryRouter initialEntries={["/privacy", "/open-source"]} initialIndex={1}>
        <PublicHeader filled />
        <BackAndForward />
        <main id={MAIN_CONTENT_ID} tabIndex={-1} />
      </MemoryRouter>,
    ).container;
    click(menuButton(root));
    expect(panel(root)).not.toBeNull();
    click(buttonNamed(root, "Back"));
    expect(panel(root)).toBeNull();
    click(menuButton(root));
    click(buttonNamed(root, "Forward"));
    expect(panel(root)).toBeNull();
  });

  it("closes when the window grows to the wide layout, which draws the links itself, and not before", () => {
    const viewport = installViewport(600);
    const root = open();
    click(menuButton(root));
    viewport.setWidth(800);
    expect(panel(root)).not.toBeNull();
    viewport.setWidth(1200);
    expect(panel(root)).toBeNull();
    expect(menuButton(root).getAttribute("aria-expanded")).toBe("false");
  });

  it("has no serious or critical axe violation, closed or open", async () => {
    const root = open("/how-it-works");
    await expectNoSeriousA11yViolations(root);
    click(menuButton(root));
    await expectNoSeriousA11yViolations(root);
  });
});
