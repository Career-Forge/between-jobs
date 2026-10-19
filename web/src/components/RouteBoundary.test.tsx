// @vitest-environment jsdom
import { act, lazy, type ComponentType } from "react";
import { Link, MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { expectNoSeriousA11yViolations } from "../testing/a11y";
import { settle, text } from "../testing/forms";
import { buttonNamed, click, installViewport, query, render } from "../testing/render";
import { AppShell } from "./AppShell";
import { RouteBoundary, isFileFetchFailure } from "./RouteBoundary";

vi.mock("../lib/useHiringSignalsStatus", () => ({ useHiringSignalsEnabled: () => false }));
vi.mock("../lib/api", () => ({ apiFetch: vi.fn() }));

// What the shell draws where a lazily loaded page goes: the page's heading and the band's frame while its file is on
// the way (so the page has its one heading and "Skip to next step" has something to point at), and a plain failure
// with a way out, a heading and no skip link if the file never comes. The pages themselves are loaded on first visit by App.tsx.

function deferred<T>() {
  let resolve: (value: T) => void = () => undefined;
  let reject: (error: unknown) => void = () => undefined;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

const Page: ComponentType = () => (
  <>
    <h1>Loaded page</h1>
    <Link to="/settings">To settings</Link>
  </>
);

function app(path: string, load: () => Promise<{ default: ComponentType }>) {
  const Discover = lazy(load);
  const Settings = lazy(async () => ({ default: () => <h1>Settings page</h1> }));
  return render(
    <MemoryRouter initialEntries={[path]}>
      <RouteBoundary>
        <Routes>
          <Route path="/discover" element={<Discover />} />
          <Route path="/settings" element={<Settings />} />
          <Route path="/other" element={<h1>Not lazy</h1>} />
        </Routes>
      </RouteBoundary>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  installViewport(1000);
});
afterEach(() => vi.restoreAllMocks());

describe("while a page's file is on the way", () => {
  it("draws the band's eyebrow and Loading... with the element the skip link points at, and placeholder rows, on a page that has a band", async () => {
    const wait = deferred<{ default: ComponentType }>();
    const { container } = app("/discover", () => wait.promise);
    const band = query(container, 'section[aria-label="Next step"]');
    expect(text(band)).toContain("Loading...");
    expect(container.querySelectorAll("#next-step-focus")).toHaveLength(1);
    expect(query(container, ".bj-state--loading").getAttribute("aria-busy")).toBe("true");
    // the page's one heading is there from the first frame, ahead of the band, where the page puts it
    expect([...container.querySelectorAll("h1")].map((h) => text(h))).toEqual(["Discover"]);
    expect(query(container, "h1").compareDocumentPosition(band) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    // the band says it, so the placeholders do not say it a second time
    expect(query(container, ".bj-state--loading").getAttribute("role")).toBeNull();
    await act(async () => wait.resolve({ default: Page }));
    await settle();
    expect(text(query(container, "h1"))).toBe("Loaded page");
    expect(container.querySelector(".bj-state--loading")).toBeNull();
  });

  it("draws only the placeholder, announced, at an address that has no band", async () => {
    const wait = deferred<{ default: ComponentType }>();
    const Late = lazy(() => wait.promise);
    const { container } = render(
      <MemoryRouter initialEntries={["/legal"]}>
        <RouteBoundary>
          <Routes>
            <Route path="/legal" element={<Late />} />
          </Routes>
        </RouteBoundary>
      </MemoryRouter>,
    );
    expect(container.querySelector('section[aria-label="Next step"]')).toBeNull();
    expect(container.querySelector("h1")).toBeNull();
    expect(query(container, ".bj-state--loading").getAttribute("role")).toBe("status");
    await act(async () => wait.resolve({ default: Page }));
  });

  it("has no serious accessibility violation", async () => {
    const wait = deferred<{ default: ComponentType }>();
    const { container } = app("/discover", () => wait.promise);
    await expectNoSeriousA11yViolations(container);
  });
});

describe("when the page's file cannot be fetched", () => {
  it("says so plainly, offers a Retry that reloads the site, and clears when the person moves to another page", async () => {
    vi.spyOn(console, "error").mockImplementation(() => undefined);
    const reload = vi.fn();
    Object.defineProperty(window, "location", { configurable: true, value: { ...window.location, reload } });
    const wait = deferred<{ default: ComponentType }>();
    wait.promise.catch(() => undefined);
    const Failing = lazy(() => wait.promise);
    const { container } = render(
      <MemoryRouter initialEntries={["/discover"]}>
        <Link to="/other">Elsewhere</Link>
        <RouteBoundary>
          <Routes>
            <Route path="/discover" element={<Failing />} />
            <Route path="/other" element={<h1>Not lazy</h1>} />
          </Routes>
        </RouteBoundary>
      </MemoryRouter>,
    );
    await act(async () => wait.reject(new Error("Failed to fetch dynamically imported module")));
    await settle();
    expect(text(query(container, "[role='alert']"))).toContain("This page could not be loaded.");
    await expectNoSeriousA11yViolations(container);
    click(buttonNamed(container, "Retry"));
    expect(reload).toHaveBeenCalledTimes(1);

    click(query(container, "a"));
    await settle();
    expect(container.querySelector("[role='alert']")).toBeNull();
    expect(text(query(container, "h1"))).toBe("Not lazy");
  });
});

describe("when the page's file cannot be fetched, inside the shell", () => {
  function failedInShell(path: string, error: Error) {
    vi.spyOn(console, "error").mockImplementation(() => undefined);
    const Failing = lazy(() => Promise.reject(error));
    return render(
      <MemoryRouter initialEntries={[path]}>
        <AppShell email={null} onSignOut={() => undefined}>
          <RouteBoundary>
            <Routes>
              <Route path="/discover" element={<Failing />} />
              <Route path="/applications/:id" element={<Failing />} />
            </Routes>
          </RouteBoundary>
        </AppShell>
      </MemoryRouter>,
    );
  }
  const links = (container: HTMLElement) => [...container.querySelectorAll(".bj-skip-link")].map((link) => text(link));

  it("has the page's one heading and offers no 'Skip to next step' to a band that is not there", async () => {
    const wait = deferred<{ default: ComponentType }>();
    wait.promise.catch(() => undefined);
    const Late = lazy(() => wait.promise);
    const { container } = render(
      <MemoryRouter initialEntries={["/discover"]}>
        <AppShell email={null} onSignOut={() => undefined}>
          <RouteBoundary>
            <Routes>
              <Route path="/discover" element={<Late />} />
            </Routes>
          </RouteBoundary>
        </AppShell>
      </MemoryRouter>,
    );
    // while the file is on the way the band is drawn, so its skip link is offered and has a target
    expect(links(container)).toEqual(["Skip to next step", "Skip to content"]);
    expect(container.querySelectorAll("#next-step-focus")).toHaveLength(1);
    vi.spyOn(console, "error").mockImplementation(() => undefined);
    await act(async () => wait.reject(new Error("Failed to fetch dynamically imported module: /assets/Discover-1.js")));
    await settle();
    expect(text(query(container, "[role='alert']"))).toContain("This page could not be loaded. Check your connection");
    expect([...container.querySelectorAll("main h1")].map((h) => text(h))).toEqual(["Discover"]);
    expect(container.querySelectorAll("#next-step-focus")).toHaveLength(0);
    expect(links(container)).toEqual(["Skip to content"]);
  });

  it("says a crash while a page draws is not the connection, with the same heading, Retry and no skip link", async () => {
    const { container } = failedInShell("/applications/abc", new TypeError("x is undefined"));
    await settle();
    const alert = text(query(container, "[role='alert']"));
    expect(alert).toContain("Something went wrong on this page.");
    expect(alert).not.toContain("connection");
    expect([...container.querySelectorAll("main h1")].map((h) => text(h))).toEqual(["Application"]);
    expect(links(container)).toEqual(["Skip to content"]);
    expect(buttonNamed(container, "Retry")).toBeTruthy();
    await expectNoSeriousA11yViolations(container);
  });
});

describe("isFileFetchFailure", () => {
  it("recognises a page file that could not be fetched, in each browser's words", () => {
    for (const message of [
      "Failed to fetch dynamically imported module: https://between-jobs.example/assets/Discover-1a2b.js",
      "error loading dynamically imported module",
      "Importing a module script failed.",
      "Unable to preload CSS for /assets/Profile-9.css",
      "Loading chunk 12 failed.",
    ]) {
      expect(isFileFetchFailure(new TypeError(message)), message).toBe(true);
    }
    const chunk = new Error("x");
    chunk.name = "ChunkLoadError";
    expect(isFileFetchFailure(chunk)).toBe(true);
  });

  it("does not take a bug in a page for a connection problem", () => {
    expect(isFileFetchFailure(new TypeError("Cannot read properties of undefined (reading 'map')"))).toBe(false);
    expect(isFileFetchFailure(new Error("Failed to fetch"))).toBe(false);
    expect(isFileFetchFailure("Importing a module script failed.")).toBe(false);
    expect(isFileFetchFailure(null)).toBe(false);
  });
});
