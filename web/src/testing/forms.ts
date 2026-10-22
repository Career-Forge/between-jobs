// Helpers for tests that fill in and submit forms (the jsdom environment, with render.tsx). React's
// controlled inputs ignore an assigned `.value`, so the value goes through the element's own setter
// and an `input` event, the way a keystroke would.

import { act } from "react";

export function typeInto(element: HTMLInputElement | HTMLTextAreaElement, value: string): void {
  const prototype = element instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
  const setter = Object.getOwnPropertyDescriptor(prototype, "value")?.set;
  if (setter === undefined) throw new Error("no value setter");
  act(() => {
    setter.call(element, value);
    element.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

export function choose(element: HTMLSelectElement, value: string): void {
  const setter = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "value")?.set;
  if (setter === undefined) throw new Error("no value setter");
  act(() => {
    setter.call(element, value);
    element.dispatchEvent(new Event("change", { bubbles: true }));
  });
}

// Presses a radio the way a click does (the checked state and the change event).
export function pick(element: HTMLInputElement): void {
  act(() => {
    element.click();
  });
}

export function submit(form: HTMLFormElement): void {
  act(() => {
    form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
  });
}

// Lets pending promises (a mocked request, a state update after it) settle.
export async function settle(times = 3): Promise<void> {
  for (let i = 0; i < times; i++) {
    await act(async () => {
      await Promise.resolve();
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  }
}

export function text(element: Element): string {
  return (element.textContent ?? "").replace(/\s+/g, " ").trim();
}

// The words a sighted person reads: `text` without what is visually hidden for a screen reader (the
// provider's name after "Replace key", say). A test that finds a button by its label uses this, so it
// still matches the whole visible label exactly.
export function visibleText(element: Element): string {
  const copy = element.cloneNode(true) as Element;
  for (const hidden of copy.querySelectorAll(".bj-visually-hidden")) hidden.remove();
  return text(copy);
}
