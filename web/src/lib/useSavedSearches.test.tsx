// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from "vitest";
import { SavedSearchesCardView } from "../components/SavedSearchesCardView";
import { FakeApiError, on, reset } from "../testing/fakeApi";
import { settle } from "../testing/forms";
import { click, query, render } from "../testing/render";
import { useSavedSearches } from "./useSavedSearches";

vi.mock("./api", async () => (await import("../testing/fakeApi")).apiModule);

// The hook and the card it feeds, on a page that has a next-step band to fall back on.
function Page() {
  const saved = useSavedSearches();
  return (
    <div>
      <button type="button" id="next-step-focus">
        Band
      </button>
      <button type="button" id="elsewhere">
        Elsewhere
      </button>
      <SavedSearchesCardView state={saved.state} actions={{ toggle: saved.toggle, remove: saved.remove, retry: saved.retry }} />
    </div>
  );
}

const row = (id: string, query: string) => ({ id, query, location: null, companies: [], remote_only: false, is_active: true, created_at: "2026-10-01T00:00:00Z", last_matched_at: null });
const deleteButton = (container: HTMLElement, name: string) => query<HTMLButtonElement>(container, `button[aria-label^='Delete the saved search "${name}"']`);

beforeEach(() => {
  reset();
  on("GET /saved-searches", [row("a", "alpha"), row("b", "bravo"), row("c", "charlie")]);
});

// Deleting a row removes the button that had focus, and focus would fall to the top of the page.
describe("keyboard focus when a saved search is deleted", () => {
  it("goes to the Delete button of the row after it, then of the one before it, then to the band", async () => {
    on("DELETE /saved-searches/a", undefined);
    on("DELETE /saved-searches/b", undefined);
    on("DELETE /saved-searches/c", undefined);
    const { container } = render(<Page />);
    await settle();

    deleteButton(container, "alpha").focus();
    click(deleteButton(container, "alpha"));
    await settle();
    expect(document.activeElement).toBe(deleteButton(container, "bravo"));

    // the last row: its neighbour is the one before it
    deleteButton(container, "charlie").focus();
    click(deleteButton(container, "charlie"));
    await settle();
    expect(document.activeElement).toBe(deleteButton(container, "bravo"));

    // the only one: the card draws nothing, so the band takes focus
    click(deleteButton(container, "bravo"));
    await settle();
    expect(container.querySelector(".bj-ss")).toBeNull();
    expect(document.activeElement).toBe(container.querySelector("#next-step-focus"));
  });

  it("stays on the button when the delete failed, and the row is still there", async () => {
    on("DELETE /saved-searches/a", new FakeApiError("down", "INTERNAL", undefined, 500));
    const { container } = render(<Page />);
    await settle();
    const button = deleteButton(container, "alpha");
    button.focus();
    click(button);
    await settle();
    expect(deleteButton(container, "alpha")).toBe(button);
    expect(document.activeElement).toBe(button);
    expect(container.querySelector(".bj-ss-error")).not.toBeNull();
  });

  it("leaves focus alone when the person has moved on while the request was out", async () => {
    let finish: () => void = () => undefined;
    on("DELETE /saved-searches/a", () => new Promise<void>((resolve) => (finish = resolve)));
    const { container } = render(<Page />);
    await settle();
    deleteButton(container, "alpha").focus();
    click(deleteButton(container, "alpha"));
    const elsewhere = query<HTMLButtonElement>(container, "#elsewhere");
    elsewhere.focus();
    finish();
    await settle();
    expect(document.activeElement).toBe(elsewhere);
  });

  it("does not take focus after a pause or resume", async () => {
    on("PATCH /saved-searches/a", () => ({ ...row("a", "alpha"), is_active: false }));
    const { container } = render(<Page />);
    await settle();
    const pause = query<HTMLButtonElement>(container, "button[aria-label^='Pause the saved search \"alpha\"']");
    pause.focus();
    click(pause);
    await settle();
    expect(document.activeElement?.getAttribute("aria-label")).toContain("alpha");
  });
});
