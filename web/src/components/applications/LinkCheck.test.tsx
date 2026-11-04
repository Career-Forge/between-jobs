// @vitest-environment jsdom
import { act } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Application } from "../../lib/applicationsTypes";
import { CANT_TELL_REASON_WORDS, GONE_WORDS, STILL_LISTED_WORDS, type LinkCheckEntry, type LinkCheckResult } from "../../lib/linkCheck";
import { expectNoSeriousA11yViolations } from "../../testing/a11y";
import { text } from "../../testing/forms";
import { click, query, render } from "../../testing/render";
import { LinkCheck } from "./LinkCheck";

// The button and the line after it, from props: what each answer reads as, what a request that is out
// looks like, and that the live region is in the row before it has anything to say. The page that makes the
// request is tested in pages/Applications.test.tsx.

const APPLICATION: Application = {
  id: "a1",
  status: "saved",
  source_channel: "web",
  created_at: "2026-09-22T12:00:00Z",
  updated_at: "2026-09-22T12:00:00Z",
  date_applied: null,
  snapshot: { id: "s1", title: "Data Engineer", company_name: "Acme", location_text: null, source_url: "https://jobs.example/de" },
  resume_exists: false,
};

const RESULT: LinkCheckResult = { state: "still_listed", reason: "listed", checked_at: "2026-11-04T14:32:05Z" };
const answered = (result: Partial<LinkCheckResult> = {}, receivedAt = Date.now()): LinkCheckEntry => ({ kind: "answered", result: { ...RESULT, ...result }, receivedAt });
const PENDING: LinkCheckEntry = { kind: "pending" };

// The card's controls row, which is where the component lives.
function row(entry: LinkCheckEntry | undefined, onCheck: () => void = () => undefined, application: Application = APPLICATION) {
  return (
    <div className="bj-app-controls">
      <LinkCheck application={application} entry={entry} onCheck={onCheck} />
    </div>
  );
}

const draw = (entry: LinkCheckEntry | undefined, onCheck?: () => void, application?: Application) => render(row(entry, onCheck, application));

const button = (container: HTMLElement) => query<HTMLButtonElement>(container, "button");
const line = (container: HTMLElement) => query(container, "[role='status']");

afterEach(() => {
  vi.useRealTimers();
});

describe("the button", () => {
  it("is a plain button that reads Check posting and is named for its card", () => {
    const { container } = draw(undefined);
    const control = button(container);
    expect(control.textContent).toBe("Check posting");
    expect(control.getAttribute("aria-label")).toBe('Check posting for "Data Engineer" at Acme');
    expect(control.type).toBe("button");
    expect(control.disabled).toBe(false);
    expect(control.getAttribute("aria-disabled")).toBeNull();
    expect(control.getAttribute("aria-busy")).toBeNull();
  });

  it("is a secondary button: never the page's cyan fill", () => {
    const { container } = draw(undefined);
    expect(container.querySelector(".bj-primary, .bj-button-link-primary")).toBeNull();
    expect(button(container).className).toBe("bj-app-check");
  });

  it("names a card with no snapshot as untitled, and one with no company by its title alone", () => {
    expect(button(draw(undefined, undefined, { ...APPLICATION, snapshot: null }).container).getAttribute("aria-label")).toBe('Check posting for "Untitled"');
    const noCompany = { ...APPLICATION, snapshot: { ...APPLICATION.snapshot!, company_name: "" } };
    expect(button(draw(undefined, undefined, noCompany).container).getAttribute("aria-label")).toBe('Check posting for "Data Engineer"');
  });

  it("asks for one check per press", () => {
    const onCheck = vi.fn();
    const { container } = draw(undefined, onCheck);
    click(button(container));
    expect(onCheck).toHaveBeenCalledTimes(1);
    click(button(container));
    expect(onCheck).toHaveBeenCalledTimes(2);
  });

  it("can be pressed again after an answer, whatever the answer was", () => {
    for (const entry of [answered(), answered({ state: "gone" }), answered({ state: "cant_tell", reason: "blocked" }), { kind: "unanswered", words: "x" } as const]) {
      const onCheck = vi.fn();
      const { container, unmount } = draw(entry, onCheck);
      expect(button(container).getAttribute("aria-disabled")).toBeNull();
      expect(button(container).textContent).toBe("Check posting");
      click(button(container));
      expect(onCheck).toHaveBeenCalledTimes(1);
      unmount();
    }
  });
});

describe("while the request is out", () => {
  it("reads Checking..., is busy and aria-disabled, and keeps its place and focus (it is not disabled)", () => {
    const { container } = draw(PENDING);
    const control = button(container);
    expect(control.textContent).toBe("Checking...");
    expect(control.getAttribute("aria-label")).toBe('Checking posting for "Data Engineer" at Acme');
    expect(control.getAttribute("aria-busy")).toBe("true");
    expect(control.getAttribute("aria-disabled")).toBe("true");
    expect(control.disabled).toBe(false);
    control.focus();
    expect(document.activeElement).toBe(control);
  });

  it("ignores a press, so one press is one request", () => {
    const onCheck = vi.fn();
    const { container } = draw(PENDING, onCheck);
    click(button(container));
    click(button(container));
    expect(onCheck).not.toHaveBeenCalled();
  });

  it("has nothing to say yet: the last answer is cleared while the next is out", () => {
    const { container, rerender } = draw(answered({ state: "gone" }));
    expect(text(line(container))).toBe(GONE_WORDS);
    rerender(row(PENDING));
    expect(line(container).textContent).toBe("");
    expect(line(container).getAttribute("data-state")).toBeNull();
  });
});

describe("the live region", () => {
  it("is in the row from the start, empty, so a screen reader hears the answer arrive", () => {
    const { container } = draw(undefined);
    const region = line(container);
    expect(region.tagName).toBe("P");
    expect(region.getAttribute("role")).toBe("status");
    expect(region.textContent).toBe("");
    expect(region.matches(":empty")).toBe(true);
    expect(region.getAttribute("data-state")).toBeNull();
    expect(container.querySelectorAll("[role='status']")).toHaveLength(1);
  });

  it("stays the same element from before the press to after the answer, only its words change", () => {
    const { container, rerender } = draw(undefined);
    const region = line(container);
    const again = (entry: LinkCheckEntry | undefined) => rerender(row(entry));
    again(PENDING);
    expect(line(container)).toBe(region);
    again(answered());
    expect(line(container)).toBe(region);
    expect(text(region)).toBe(`${STILL_LISTED_WORDS} Checked just now.`);
    again(PENDING);
    expect(region.matches(":empty")).toBe(true);
    again(answered({ state: "gone" }));
    expect(line(container)).toBe(region);
  });

  it("is the last thing in the row, after the button", () => {
    const { container } = draw(answered());
    expect(container.querySelector(".bj-app-controls")?.lastElementChild).toBe(line(container));
    expect(container.querySelector(".bj-app-controls")?.firstElementChild).toBe(button(container));
  });
});

describe("what it says", () => {
  it("says a listed posting is on the board, and when it was checked", () => {
    const { container } = draw(answered());
    expect(text(line(container))).toBe("Still on the company's own board. Checked just now.");
    expect(line(container).getAttribute("data-state")).toBe("still_listed");
  });

  it("says a gone posting is no longer on the board, that it usually closed, and that it does not mean a rejection", () => {
    const { container } = draw(answered({ state: "gone", reason: "absent" }));
    expect(text(line(container))).toBe(
      "No longer on the company's own board. That usually means the posting closed. It does not mean you were rejected.",
    );
    expect(line(container).getAttribute("data-state")).toBe("gone");
  });

  it.each([...CANT_TELL_REASON_WORDS])("says why it can't tell for %s, and ends with the suggestion", (reason, words) => {
    const { container } = draw(answered({ state: "cant_tell", reason }));
    expect(text(line(container))).toBe(`${words}. Open the posting to see for yourself.`);
    expect(line(container).getAttribute("data-state")).toBe("cant_tell");
  });

  it("says it can't use a reason it does not know, and ends with the suggestion", () => {
    const { container } = draw(answered({ state: "cant_tell", reason: "from_the_future" }));
    expect(text(line(container))).toBe("The company's board gave an answer we can't use, so we can't say about this job. Open the posting to see for yourself.");
  });

  it("says a press that got no answer in its own words, as unanswered", () => {
    const { container } = draw({ kind: "unanswered", words: "The check didn't go through. Failed to fetch. Open the posting to see for yourself." });
    expect(text(line(container))).toBe("The check didn't go through. Failed to fetch. Open the posting to see for yourself.");
    expect(line(container).getAttribute("data-state")).toBe("unanswered");
  });

  it("draws the words as text, never as markup: a reason or a message cannot add an element", () => {
    const { container } = draw({ kind: "unanswered", words: "<img src=x onerror=alert(1)> <b>bold</b>" });
    expect(line(container).querySelector("img, b")).toBeNull();
    expect(line(container).textContent).toBe("<img src=x onerror=alert(1)> <b>bold</b>");
  });

  it("draws no link, no image and no inline style: the card already has View posting", () => {
    const { container } = draw(answered({ state: "cant_tell", reason: "company_page" }));
    expect(line(container).querySelector("a, img")).toBeNull();
    expect(container.querySelector("[style]")).toBeNull();
  });
});

describe("how old a listed answer reads", () => {
  it("turns just now into the clock time of the check once, a minute after the answer, and stays that", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-11-04T14:32:07Z"));
    const { container } = draw(answered({}, Date.now()));
    expect(text(line(container))).toBe("Still on the company's own board. Checked just now.");
    act(() => void vi.advanceTimersByTime(59_000));
    expect(text(line(container))).toBe("Still on the company's own board. Checked just now.");
    act(() => void vi.advanceTimersByTime(1_000));
    expect(text(line(container))).toBe("Still on the company's own board. Checked at 14:32.");
    expect(vi.getTimerCount()).toBe(0);
    act(() => void vi.advanceTimersByTime(3_600_000));
    expect(text(line(container))).toBe("Still on the company's own board. Checked at 14:32.");
  });

  it("changes only the time's own span, so a screen reader is not read the whole answer again", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-11-04T14:32:07Z"));
    const { container } = draw(answered({}, Date.now()));
    const when = query(line(container), "span");
    expect(when.textContent).toBe("Checked just now.");
    act(() => void vi.advanceTimersByTime(60_000));
    expect(query(line(container), "span")).toBe(when);
    expect(when.textContent).toBe("Checked at 14:32.");
  });

  it("starts no timer for an answer that makes no claim about time, and clears the one it started", () => {
    vi.useFakeTimers();
    for (const entry of [answered({ state: "gone" }), answered({ state: "cant_tell", reason: "blocked" }), { kind: "unanswered", words: "x" } as const, PENDING, undefined]) {
      const { unmount } = draw(entry);
      expect(vi.getTimerCount(), JSON.stringify(entry)).toBe(0);
      unmount();
    }
    const listed = draw(answered());
    expect(vi.getTimerCount()).toBe(1);
    listed.unmount();
    expect(vi.getTimerCount()).toBe(0);
  });

  it("starts the minute again for a new answer, and drops it for a press that is out", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-11-04T14:32:07Z"));
    const { container, rerender } = draw(answered({}, Date.now()));
    const again = (entry: LinkCheckEntry) => rerender(row(entry));
    again(PENDING);
    expect(vi.getTimerCount()).toBe(0);
    act(() => void vi.advanceTimersByTime(30_000));
    again(answered({}, Date.now()));
    act(() => void vi.advanceTimersByTime(59_000));
    expect(text(line(container))).toContain("Checked just now.");
    act(() => void vi.advanceTimersByTime(1_000));
    expect(text(line(container))).toContain("Checked at 14:32.");
  });
});

describe("accessibility", () => {
  it.each([
    ["idle", undefined],
    ["pending", PENDING],
    ["listed", answered()],
    ["gone", answered({ state: "gone" })],
    ["can't tell", answered({ state: "cant_tell", reason: "robots_disallowed" })],
    ["unanswered", { kind: "unanswered", words: "The check didn't go through. Open the posting to see for yourself." } as const],
  ])("has no serious violation when %s", async (_name, entry) => {
    const { container } = draw(entry);
    await expectNoSeriousA11yViolations(container);
  });
});
