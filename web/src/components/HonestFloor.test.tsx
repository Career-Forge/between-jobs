// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ForgeFitResult } from "../lib/generateTypes";
import { expectNoSeriousA11yViolations } from "../testing/a11y";
import { FakeApiError, callsTo, on, reset } from "../testing/fakeApi";
import { settle, text } from "../testing/forms";
import { buttonNamed, click, query, render } from "../testing/render";
import { HonestFloor } from "./HonestFloor";

vi.mock("../lib/api", async () => (await import("../testing/fakeApi")).apiModule);

const FIT: ForgeFitResult = {
  overall_score: 3.64,
  dimensions: { skills_match: 4, experience_relevance: 3.5 },
  gaps: ["No Kafka in your profile"],
  strengths: ["Strong batch pipelines"],
  recommendation: "Caution",
  keyword_gaps: ["Kafka"],
  keyword_hits: ["dbt"],
  visa_flag: null,
};

beforeEach(() => reset());
afterEach(() => vi.restoreAllMocks());

describe("the honest read", () => {
  it("shows the ceiling on its own 0 to 10 scale, never as a /100, with a heading that can take focus", () => {
    const { container } = render(<HonestFloor applicationId="a1" fit={FIT} onGenerateAnyway={() => undefined} />);
    expect(text(query(container, ".bj-ws-ceiling"))).toBe("Ceiling 3.6 / 10");
    expect(text(container)).not.toContain("/ 100");
    const heading = query(container, "#honest-read-heading");
    expect(heading.tagName).toBe("H3");
    expect(heading.getAttribute("tabindex")).toBe("-1");
    expect(text(query(container, ".bj-ws-tag"))).toBe("Borderline fit");
  });

  it("draws a dimension as a segmented bar out of ten, and a dimension the model did not return not at all", () => {
    const { container } = render(<HonestFloor applicationId="a1" fit={FIT} onGenerateAnyway={() => undefined} />);
    expect([...container.querySelectorAll(".bj-segbar-label")].map((node) => text(node))).toEqual(["Skills match · 25% weight", "Experience relevance · 25% weight"]);
    expect([...container.querySelectorAll(".bj-segbar-value")].map((node) => text(node))).toEqual(["4 / 10", "3.5 / 10"]);
  });

  it("marks a keyword the profile lacks as Missing in words with a muted dashed outline, never red", () => {
    const { container } = render(<HonestFloor applicationId="a1" fit={FIT} onGenerateAnyway={() => undefined} />);
    const chips = [...container.querySelectorAll(".bj-ws-chip")].map((node) => text(node));
    expect(chips).toEqual(["dbtMatched", "KafkaMissing"]);
    expect(container.querySelector(".bj-ws-chip--missing")).not.toBeNull();
    expect(container.innerHTML).not.toMatch(/danger|bj-badge-/);
  });

  it("shows the sponsorship mismatch as the one solid callout, only when the fit read says so", () => {
    const quiet = render(<HonestFloor applicationId="a1" fit={FIT} onGenerateAnyway={() => undefined} />);
    expect(quiet.container.querySelector(".bj-ws-callout--danger")).toBeNull();
    quiet.unmount();
    const flagged = render(<HonestFloor applicationId="a1" fit={{ ...FIT, visa_flag: true }} onGenerateAnyway={() => undefined} />);
    expect(text(query(flagged.container, ".bj-ws-callout--danger"))).toContain("doesn't mention sponsorship");
  });

  it("offers Generate anyway and Park, both outlined, and says the call is the person's", () => {
    const onGenerateAnyway = vi.fn();
    const { container } = render(<HonestFloor applicationId="a1" fit={FIT} onGenerateAnyway={onGenerateAnyway} />);
    expect(container.querySelector(".bj-primary")).toBeNull();
    click(buttonNamed(container, "Generate anyway"));
    expect(onGenerateAnyway).toHaveBeenCalledTimes(1);
    expect(text(container)).toContain("Your call either way -- the platform advises, it never blocks.");
  });

  it("parks the application through the stage endpoint, tells the page, and says where to find it", async () => {
    on("POST /applications/a1/stage", {});
    const onParked = vi.fn();
    const { container } = render(<HonestFloor applicationId="a1" fit={FIT} onGenerateAnyway={() => undefined} onParked={onParked} />);
    click(buttonNamed(container, "Park this application"));
    await settle();
    expect(callsTo("POST /applications/a1/stage")[0].body).toMatchObject({ new_status: "withdrawn" });
    expect(onParked).toHaveBeenCalledTimes(1);
    expect(text(container)).toContain('Parked -- find it again under "withdrawn" on Applications anytime.');
    expect(container.querySelector("#honest-read-heading")).not.toBeNull();
  });

  it("shows a failed park and leaves the choice open", async () => {
    on("POST /applications/a1/stage", new FakeApiError("The server hit a problem.", "INTERNAL"));
    const onParked = vi.fn();
    const { container } = render(<HonestFloor applicationId="a1" fit={FIT} onGenerateAnyway={() => undefined} onParked={onParked} />);
    click(buttonNamed(container, "Park this application"));
    await settle();
    expect(text(query(container, "[role='alert']"))).toBe("The server hit a problem.");
    expect(onParked).not.toHaveBeenCalled();
    expect(buttonNamed(container, "Park this application")).toBeTruthy();
  });

  it("passes an accessibility check", async () => {
    const { container } = render(<HonestFloor applicationId="a1" fit={{ ...FIT, visa_flag: true }} onGenerateAnyway={() => undefined} />);
    await expectNoSeriousA11yViolations(container);
  });
});
