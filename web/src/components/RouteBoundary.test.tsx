// @vitest-environment jsdom
import { act, lazy, type ComponentType } from "react";
import { Link, MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { expectNoSeriousA11yViolations } from "../testing/a11y";
import { settle, text } from "../testing/forms";
import { buttonNamed, click, query, render } from "../testing/render";
import { RouteBoundary } from "./RouteBoundary";

// What the shell draws where a lazily loaded page goes: the page's frame in miniature while its file is on the way
// (so "Skip to next step" has something to point at, and nothing jumps), and a plain failure with a way out if the
// file never comes. The pages themselves are loaded on first visit by App.tsx.

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

afterEach(() => vi.restoreAllMocks());

describe("while a page's file is on the way", () => {
  it("draws the band's eyebrow and Loading... with the element the skip link points at, and placeholder rows, on a page that has a band", async () => {
    const wait = deferred<{ default: ComponentType }>();
    const { container } = app("/discover", () => wait.promise);
    const band = query(container, 'section[aria-label="Next step"]');
    expect(text(band)).toContain("Loading...");
    expect(container.querySelectorAll("#next-step-focus")).toHaveLength(1);
    expect(query(container, ".bj-state--loading").getAttribute("aria-busy")).toBe("true");
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
