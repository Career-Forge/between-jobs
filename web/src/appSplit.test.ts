import { describe, expect, it } from "vitest";
import appSource from "./App.tsx?raw";

// Which pages are in the first download, read from the source: the split is a property of the imports, which no
// rendering test sees (appPages.test.tsx loads every page up front, App.test.tsx calls App as a plain function).
// A public page turned into lazy() would make a stranger who only reads it wait for a file fetch; a signed-in page
// turned back into a static import would put its code in everyone's first download.
//
// vite's `?raw` imports, not `node:fs`: @types/node is not installed here.

const LAZY_PAGES = ["Today", "Discover", "HiringSignals", "Applications", "ApplicationWorkspace", "Practice", "Profile", "Settings"];
// NotFound is small and drawn only at an address nobody routes; Enroll is drawn by the tester-programme gate at any address.
const FIRST_DOWNLOAD_PAGES = ["Enroll", "Landing", "Login", "NotFound", "Privacy", "Terms", "UpdatePassword"];

describe("App.tsx's page split", () => {
  it.each(LAZY_PAGES)("loads the signed-in page %s the first time it is opened", (name) => {
    expect(appSource).toContain(`const ${name} = lazy(() => import("./pages/${name}"));`);
    expect(appSource).not.toMatch(new RegExp(`^import ${name} from`, "m"));
  });

  it.each(FIRST_DOWNLOAD_PAGES)("keeps %s in the first download", (name) => {
    expect(appSource).toContain(`import ${name} from "./pages/${name}";`);
    expect(appSource).not.toMatch(new RegExp(`const ${name} = lazy`));
  });

  it("lazy-loads exactly those pages, and no others", () => {
    const lazyNames = [...appSource.matchAll(/^const (\w+) = lazy\(/gm)].map((match) => match[1]);
    expect(lazyNames).toEqual(LAZY_PAGES);
  });

  it("draws the lazy pages only inside the boundary that shows the loading frame and the failure", () => {
    const open = appSource.indexOf("<RouteBoundary>");
    const close = appSource.indexOf("</RouteBoundary>");
    expect(open).toBeGreaterThan(-1);
    const inside = appSource.slice(open, close);
    for (const name of LAZY_PAGES) expect(inside, name).toContain(`<${name} />`);
    expect(appSource.slice(0, open) + appSource.slice(close)).not.toMatch(new RegExp(`<(${LAZY_PAGES.join("|")}) />`));
  });
});
