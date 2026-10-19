// @vitest-environment jsdom
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { expectNoSeriousA11yViolations } from "../testing/a11y";
import { FakeApiError, callsTo, on, reset } from "../testing/fakeApi";
import { settle, text } from "../testing/forms";
import { buttonNamed, click, query, render } from "../testing/render";
import { TailorPanel } from "./TailorPanel";

vi.mock("../lib/api", async () => (await import("../testing/fakeApi")).apiModule);

const DOCUMENT = { id: "doc-1", profile_version_id: "9f8e7d6c5b4a3210", selected_evidence_fact_ids: [] };
const COVERAGE = {
  step0: { clusters: [] },
  coverage: [
    { name: "Data pipelines", priority: "must_have", keywords: [], coverage_count: 2, matched_fact_ids: ["f1", "f2"] },
    { name: "Mentoring", priority: "preferred", keywords: [], coverage_count: 0, matched_fact_ids: [] },
  ],
  skills: [
    { skill: "Airflow", requested_as: "airflow", state: "verified" },
    { skill: "dbt", requested_as: "dbt", state: "supported" },
    { skill: "Spark", requested_as: "spark", state: "adjacent" },
    { skill: "Kafka", requested_as: "kafka", state: "unsupported" },
  ],
};
const FACTS = [
  { id: "f1", fact_type: "experience", value_json: { title: "Data Engineer", company: "Initech" } },
  { id: "f2", fact_type: "experience", value_json: { title: "Analyst", company: "Hooli" } },
];

function describeReads() {
  on("GET /resume-documents/mine?application_id=a1", DOCUMENT);
  on("POST /resume-documents/doc-1/coverage", COVERAGE);
  on("GET /profile/versions/9f8e7d6c5b4a3210/career-facts", FACTS);
  on("PATCH /resume-documents/doc-1/evidence", {});
}

function panel() {
  return render(
    <MemoryRouter>
      <TailorPanel applicationId="a1" />
    </MemoryRouter>,
  );
}

beforeEach(() => reset());
afterEach(() => vi.restoreAllMocks());

describe("Tailor", () => {
  it("shows a placeholder in its own shape while its reads are out", async () => {
    on("GET /resume-documents/mine?application_id=a1", () => new Promise(() => undefined));
    const { container } = panel();
    await settle(1);
    expect(query(container, "[role='status'][aria-busy='true']").textContent).toContain("Loading tailor panel...");
  });

  it("draws must-have in ivory bold and preferred muted, and each count in words", async () => {
    describeReads();
    const { container } = panel();
    await settle();
    const priorities = [...container.querySelectorAll(".bj-ws-priority")];
    expect(priorities.map((node) => [text(node), node.classList.contains("bj-ws-priority--must")])).toEqual([["Must have", true], ["Preferred", false]]);
    expect([...container.querySelectorAll(".bj-ws-cluster-count")].map((node) => text(node))).toEqual(["2 matches", "0 matches"]);
  });

  it("says each skill in words as well as a mark: Verified ✓, Supported ◆, Adjacent ◇, Gap ○", async () => {
    describeReads();
    const { container } = panel();
    await settle();
    expect([...container.querySelectorAll(".bj-ws-skill")].map((node) => text(node))).toEqual(["✓ Airflow Verified", "◆ dbt Supported", "◇ Spark Adjacent", "○ Kafka Gap"]);
    expect(container.querySelector(".bj-ws-skill--adjacent")).not.toBeNull();
  });

  it("opens a cluster to its evidence, as a disclosure with its state, and says picks do not change the draft yet", async () => {
    describeReads();
    const { container } = panel();
    await settle();
    const row = buttonNamed(container, /Must have/);
    expect(row.getAttribute("aria-expanded")).toBe("false");
    click(row);
    expect(row.getAttribute("aria-expanded")).toBe("true");
    expect([...container.querySelectorAll(".bj-ws-evidence-item")].map((node) => text(node))).toEqual(["Data Engineer -- Initech", "Analyst -- Hooli"]);
    expect(text(container)).toContain("Picks save here. The draft doesn't read them yet.");
    click(row);
    expect(container.querySelector(".bj-ws-evidence")).toBeNull();
  });

  it("saves a pick through the evidence endpoint with the whole selection", async () => {
    describeReads();
    const { container } = panel();
    await settle();
    click(buttonNamed(container, /Must have/));
    click(query(container, ".bj-ws-evidence-item input"));
    await settle();
    expect(callsTo("PATCH /resume-documents/doc-1/evidence")[0].body).toEqual({ evidence_fact_ids: ["f2"] });
  });

  it("keeps the panel when a save fails, says the picks were not saved, and retries the save without asking the model again", async () => {
    describeReads();
    const { container } = panel();
    await settle();
    click(buttonNamed(container, /Must have/));
    on("PATCH /resume-documents/doc-1/evidence", new FakeApiError("The server hit a problem.", "INTERNAL"));
    click(query(container, ".bj-ws-evidence-item input"));
    await settle();
    expect(container.querySelectorAll(".bj-ws-cluster")).toHaveLength(2);
    expect(text(query(container, "[role='alert']"))).toContain("Couldn't save your picks.");
    on("PATCH /resume-documents/doc-1/evidence", {});
    click(buttonNamed(container, "Retry"));
    await settle();
    expect(callsTo("PATCH /resume-documents/doc-1/evidence")).toHaveLength(2);
    expect(callsTo("POST /resume-documents/doc-1/coverage")).toHaveLength(1);
    expect(container.querySelector("[role='alert']")).toBeNull();
  });

  it("names a failed read with a Retry that reads again", async () => {
    on("GET /resume-documents/mine?application_id=a1", new FakeApiError("The server hit a problem.", "INTERNAL"));
    const { container } = panel();
    await settle();
    expect(text(query(container, "[role='alert']"))).toContain("The server hit a problem.");
    describeReads();
    click(buttonNamed(container, "Retry"));
    await settle();
    expect(container.querySelectorAll(".bj-ws-cluster")).toHaveLength(2);
  });

  it("shows the notice with its link, and no Retry, for a missing profile or key", async () => {
    on("GET /resume-documents/mine?application_id=a1", new FakeApiError("Add a model key.", "SETUP_REQUIRED"));
    const { container } = panel();
    await settle();
    expect(text(query(container, ".bj-setup-notice"))).toContain("Add a model key.");
    expect(container.querySelector("button")).toBeNull();
  });

  it("has a trail that says a model extracted the clusters and code matched them, with a receipt of what came back", async () => {
    describeReads();
    const { container } = panel();
    await settle();
    const trigger = query(container, "button.bj-trail-trigger");
    expect(text(trigger)).toContain("clusters extracted by a model · matched to your profile by code");
    click(trigger);
    expect(text(query(container, ".bj-receipt"))).toContain("clusters2");
    expect(text(query(container, ".bj-receipt"))).toContain("profile9f8e7d6c");
  });

  it("passes an accessibility check", async () => {
    describeReads();
    const { container } = panel();
    await settle();
    click(buttonNamed(container, /Must have/));
    await expectNoSeriousA11yViolations(container);
  });
});
