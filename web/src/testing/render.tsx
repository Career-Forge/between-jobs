// A small DOM harness for component tests that need real events, focus and a document (the
// jsdom environment: put `// @vitest-environment jsdom` on the first line of the test file). It is
// React's own `createRoot` and `act` with a few event helpers; there is no testing-library in this
// package, and the components under test are small enough not to need one.

import { act, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach } from "vitest";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const mounted: { root: Root; container: HTMLElement }[] = [];

export interface Rendered {
  container: HTMLElement;
  rerender: (ui: ReactNode) => void;
  unmount: () => void;
}

export function render(ui: ReactNode): Rendered {
  const container = document.createElement("div");
  document.body.append(container);
  const root = createRoot(container);
  mounted.push({ root, container });
  act(() => root.render(ui));
  return {
    container,
    rerender: (next) => act(() => root.render(next)),
    unmount: () => act(() => root.unmount()),
  };
}

afterEach(() => {
  for (const { root, container } of mounted.splice(0)) {
    act(() => root.unmount());
    container.remove();
  }
  document.body.innerHTML = "";
  document.body.removeAttribute("style");
});

export function click(element: Element): void {
  act(() => {
    element.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true }));
  });
}

// Dispatches a keydown on the focused element (or `target`) and returns whether it was left
// un-prevented, the way `dispatchEvent` does.
export function keydown(key: string, options: { shift?: boolean; target?: Element } = {}): boolean {
  let notPrevented = true;
  act(() => {
    const target = options.target ?? document.activeElement ?? document.body;
    notPrevented = target.dispatchEvent(
      new KeyboardEvent("keydown", { key, shiftKey: options.shift === true, bubbles: true, cancelable: true }),
    );
  });
  return notPrevented;
}

export function query<T extends Element = HTMLElement>(root: ParentNode, selector: string): T {
  const found = root.querySelector<T>(selector);
  if (found === null) throw new Error(`nothing matches ${selector}`);
  return found;
}

export function buttonNamed(root: ParentNode, name: RegExp | string): HTMLButtonElement {
  const match = [...root.querySelectorAll("button")].find((button) => {
    const label = button.getAttribute("aria-label") ?? button.textContent ?? "";
    return typeof name === "string" ? label.trim() === name : name.test(label);
  });
  if (match === undefined) throw new Error(`no button named ${String(name)}`);
  return match;
}

// A stand-in for window.matchMedia that answers `(max-width: Npx)` and `(min-width: Npx)` from a
// width the test controls, and tells listeners when `setWidth` crosses a query.
export function installViewport(initialWidth: number): { setWidth: (width: number) => void } {
  let width = initialWidth;
  const listeners = new Set<{ query: string; list: MediaQueryList; run: () => void }>();
  const matches = (query: string): boolean => {
    const max = /\(max-width:\s*(\d+(?:\.\d+)?)px\)/.exec(query);
    if (max !== null) return width <= Number(max[1]);
    const min = /\(min-width:\s*(\d+(?:\.\d+)?)px\)/.exec(query);
    if (min !== null) return width >= Number(min[1]);
    return false;
  };
  window.matchMedia = ((query: string) => {
    const handlers = new Set<EventListenerOrEventListenerObject>();
    const list = {
      media: query,
      get matches() {
        return matches(query);
      },
      addEventListener: (_: string, handler: EventListenerOrEventListenerObject) => handlers.add(handler),
      removeEventListener: (_: string, handler: EventListenerOrEventListenerObject) => handlers.delete(handler),
      addListener: () => undefined,
      removeListener: () => undefined,
      dispatchEvent: () => true,
      onchange: null,
    } as unknown as MediaQueryList;
    listeners.add({
      query,
      list,
      run: () => handlers.forEach((handler) => (typeof handler === "function" ? handler(new Event("change")) : handler.handleEvent(new Event("change")))),
    });
    return list;
  }) as typeof window.matchMedia;
  return {
    setWidth: (next) => {
      width = next;
      act(() => listeners.forEach((entry) => entry.run()));
    },
  };
}
