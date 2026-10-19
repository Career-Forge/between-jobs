// @vitest-environment jsdom
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { expectNoSeriousA11yViolations } from "../testing/a11y";
import { settle, text } from "../testing/forms";
import { buttonNamed, click, query, render } from "../testing/render";
import Practice from "./Practice";

// The Practice pointer page: static copy and a link. The one thing it reads is what the next-step
// band's global rows need (a profile, a model key); none of its own data exists to load or fail.

const api = vi.hoisted(() => ({ apiFetch: vi.fn() }));
vi.mock("../lib/api", () => ({ apiFetch: api.apiFetch }));

class FakeApiError extends Error {
  constructor(
    public readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

let replies: Record<string, unknown> = {};
const calls = () => api.apiFetch.mock.calls.map(([path, init]) => `${init?.method ?? "GET"} ${path}`);

function answer(extra: Record<string, unknown> = {}) {
  replies = {
    "/profile/current": { id: "pv-1" },
    "/credentials": [{ service: "llm", provider: "openrouter", is_validated: true, model: "vendor/model" }],
    ...extra,
  };
}

async function draw() {
  const rendered = render(
    <MemoryRouter>
      <Practice />
    </MemoryRouter>,
  );
  await settle();
  return rendered;
}

const band = (container: HTMLElement) => query(container, 'section[aria-label="Next step"]');

beforeEach(() => {
  api.apiFetch.mockReset();
  api.apiFetch.mockImplementation(async (path: string) => {
    const found = replies[path];
    if (found === undefined) throw new Error(`no reply for ${path}`);
    if (found instanceof Error) throw found;
    return found;
  });
  answer();
});

describe("what the page asks for", () => {
  it("reads the profile and the saved keys for the band's global rows, and nothing of its own", async () => {
    await draw();
    expect(calls().sort()).toEqual(["GET /credentials", "GET /profile/current"]);
  });
});

describe("the page", () => {
  it("says practice lives inside each application, with one link on to Applications as the page's one cyan fill", async () => {
    const { container } = await draw();
    expect(text(query(container, "h1"))).toBe("Practice");
    expect(text(query(band(container), ".bj-band-sentence"))).toBe("Practice lives inside each application.");
    expect(text(query(band(container), ".bj-band-why"))).toBe("why: practice questions cite each company's interview process when one was found");
    const link = query<HTMLAnchorElement>(band(container), "a.bj-button-link-primary");
    expect(link.textContent).toBe("Go to Applications");
    expect(link.getAttribute("href")).toBe("/applications");
    expect(container.querySelectorAll(".bj-primary, .bj-button-link-primary")).toHaveLength(1);
  });

  it("explains what a session is grounded in and why it starts from an application, without a second copy of the link", async () => {
    const { container } = await draw();
    expect(text(query(container, ".bj-state--empty h2"))).toBe("How a practice session works");
    const body = text(query(container, ".bj-state--empty"));
    expect(body).toContain("five questions about one job");
    expect(body).toContain("resume evidence");
    expect(body).toContain("There is no company-blind practice here");
    expect(query(container, ".bj-state--empty").querySelector("a, button")).toBeNull();
  });

  it("shows only Loading... in the band until the setup reads have answered", async () => {
    let release: (value: unknown) => void = () => {};
    answer({ "/profile/current": new Promise((resolve) => (release = resolve)) });
    api.apiFetch.mockImplementation(async (path: string) => replies[path]);
    const { container } = await draw();
    expect(text(band(container))).toBe("Next stepLoading...");
    expect(container.querySelectorAll(".bj-primary, .bj-button-link-primary")).toHaveLength(0);
    release({ id: "pv-1" });
    await settle();
    expect(text(query(band(container), ".bj-band-sentence"))).toBe("Practice lives inside each application.");
  });

  it("sends a person with no active profile to their profile first", async () => {
    answer({ "/profile/current": new FakeApiError(404, "no profile") });
    const { container } = await draw();
    expect(text(query(band(container), ".bj-band-sentence"))).toBe("Everything here starts from your profile. Import it first.");
    expect(query(band(container), "a.bj-button-link-primary").getAttribute("href")).toBe("/profile");
  });

  it("asks for a model key before pointing anywhere when there is none", async () => {
    answer({ "/credentials": [] });
    const { container } = await draw();
    expect(text(query(band(container), ".bj-band-sentence"))).toBe("Add a model key so Between Jobs can draft and score.");
  });

  it("says the setup check failed, and Try again reads the two facts again", async () => {
    answer({ "/credentials": new FakeApiError(500, "boom") });
    const { container } = await draw();
    expect(text(query(band(container), ".bj-band-sentence"))).toBe("✕Couldn't check your setup. You didn't do anything wrong, and nothing was lost.");
    answer();
    api.apiFetch.mockClear();
    buttonNamed(band(container), "Try again").focus();
    click(buttonNamed(band(container), "Try again"));
    await settle();
    expect(calls().sort()).toEqual(["GET /credentials", "GET /profile/current"]);
    expect(text(query(band(container), ".bj-band-sentence"))).toBe("Practice lives inside each application.");
    // The button that was pressed went with the failure: focus is on the band's step, not lost.
    expect(document.activeElement).toBe(query(band(container), "a.bj-button-link-primary"));
  });

  it("has no serious accessibility violation", async () => {
    const { container } = await draw();
    await expectNoSeriousA11yViolations(container);
  });
});
