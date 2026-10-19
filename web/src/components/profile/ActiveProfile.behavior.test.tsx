// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ProfileVersion } from "../../lib/profileTypes";
import { settle, text } from "../../testing/forms";
import { buttonNamed, click, query, render } from "../../testing/render";
import { ActiveProfile } from "./ActiveProfile";

// What the active profile's toolbar does: export the profile as a file, copy its JSON, and switch to the raw
// view. Everything else the page draws is HeaderComposer, SectionedProfile and the other cards, each tested
// on its own.

const download = vi.hoisted(() => ({ downloadJson: vi.fn() }));
vi.mock("../../lib/profileDownload", async (importOriginal) => ({ ...(await importOriginal<typeof import("../../lib/profileDownload")>()), downloadJson: download.downloadJson }));
vi.mock("../../lib/api", () => ({ apiFetch: vi.fn(() => new Promise(() => undefined)), ApiError: class ApiError extends Error {} }));
vi.mock("../HeaderComposer", () => ({ HeaderComposer: () => null }));
vi.mock("../SectionOrderEditor", () => ({ SectionOrderEditor: () => null }));
vi.mock("../ShapeSettingsPanel", () => ({ ShapeSettingsPanel: () => null }));

const VERSION: ProfileVersion = {
  id: "v1",
  canonical_json: { personal: { name: "Asha Verma", links: { scholar: "scholar.example.test/asha" } }, achievements: ["Team award."] },
  activated_at: "2026-10-12T10:15:00Z",
  version_count: 4,
};

const page = () => render(<ActiveProfile version={VERSION} onEdited={() => Promise.resolve()} now={new Date("2026-10-19T12:00:00Z")} />);

afterEach(() => {
  download.downloadJson.mockReset();
  vi.unstubAllGlobals();
});

describe("the toolbar", () => {
  it("exports the profile's own JSON as between-jobs-profile.json", () => {
    const { container } = page();
    click(buttonNamed(container, "Export JSON"));
    expect(download.downloadJson).toHaveBeenCalledTimes(1);
    expect(download.downloadJson).toHaveBeenCalledWith(VERSION.canonical_json, "between-jobs-profile.json");
  });

  it("offers Copy only in the raw view, and copies exactly the JSON shown", async () => {
    const write = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal("navigator", { clipboard: { writeText: write } });
    const { container } = page();
    expect([...container.querySelectorAll("button")].some((button) => text(button) === "Copy")).toBe(false);
    click(buttonNamed(container, "Raw JSON"));
    expect(text(query(container, ".bj-json-view"))).toContain('"name": "Asha Verma"');
    click(buttonNamed(container, "Copy"));
    await settle();
    expect(write).toHaveBeenCalledWith(JSON.stringify(VERSION.canonical_json, null, 2));
    expect(text(buttonNamed(container, "Copied ✓"))).toBe("Copied ✓");
  });

  it("goes back to the sections from the raw view", () => {
    const { container } = page();
    click(buttonNamed(container, "Raw JSON"));
    click(buttonNamed(container, "Sections"));
    expect(container.querySelector(".bj-json-view")).toBeNull();
    expect(container.querySelector("#section-achievements")).not.toBeNull();
  });

  it("links the Scholar address with a scheme added, safely, in a new tab", () => {
    const { container } = page();
    const chip = query<HTMLAnchorElement>(container, "a.bj-contact-chip");
    expect(chip.getAttribute("href")).toBe("https://scholar.example.test/asha");
    expect(chip.getAttribute("rel")).toBe("noreferrer");
    expect(chip.getAttribute("target")).toBe("_blank");
  });
});
