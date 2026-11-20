import { describe, expect, it } from "vitest";
import { STATUS_LABELS } from "./applicationsBoard";
import { ALL_STATUSES } from "./applicationsTypes";
import type { Fetched } from "./firstRun";
import { OTHER_STAGE_LABEL, pipelineView } from "./todayPipeline";
import source from "./todayPipeline.ts?raw";

// The Pipeline card's counts, as a table: what the applications read said, and the lines that come out.

type Rows = Fetched<readonly Record<string, unknown>[]>;
const ok = (rows: readonly Record<string, unknown>[]): Rows => ({ kind: "ok", value: rows });
const rows = (...statuses: unknown[]) => statuses.map((status, index) => ({ id: `app-${index + 1}`, status }));
const lines = (read: Rows) => {
  const view = pipelineView(read);
  return view.state === "ready" ? view.lines.map((line) => `${line.label} ${line.count}`) : view.state;
};

describe("the stages", () => {
  it("are the Applications board's own, in its order, with its words", () => {
    const one = pipelineView(ok(rows(...ALL_STATUSES)));
    expect(one.state).toBe("ready");
    if (one.state !== "ready") return;
    expect(one.lines.map((line) => line.key)).toEqual([...ALL_STATUSES]);
    expect(one.lines.map((line) => line.label)).toEqual(ALL_STATUSES.map((status) => STATUS_LABELS[status]));
    expect(one.lines.every((line) => line.count === 1)).toBe(true);
    expect(one.total).toBe(ALL_STATUSES.length);
  });

  it("come from that list and no second one: the file names no stage of its own", () => {
    const code = source.replace(/\/\/.*$/gm, "");
    for (const status of ALL_STATUSES) expect(code, status).not.toContain(`"${status}"`);
    expect(code).toContain("ALL_STATUSES");
    expect(code).toContain("STATUS_LABELS");
  });

  it("keep the board's order whatever order the rows came in", () => {
    expect(lines(ok(rows("offer", "saved", "interviewing", "saved", "applied", "saved")))).toEqual(["Saved 3", "Applied 1", "Interviewing 1", "Offer 1"]);
  });

  it("leave out a stage that holds nothing", () => {
    expect(lines(ok(rows("applied", "applied", "rejected")))).toEqual(["Applied 2", "Rejected 1"]);
  });
});

describe("a status the board does not have", () => {
  it("is counted under Other, last, and never under a stage it did not say", () => {
    const view = pipelineView(ok(rows("saved", "ghosted", "on_hold", "applied")));
    expect(view).toEqual({
      state: "ready",
      total: 4,
      lines: [
        { key: "saved", label: "Saved", count: 1 },
        { key: "applied", label: "Applied", count: 1 },
        { key: "other", label: OTHER_STAGE_LABEL, count: 2 },
      ],
    });
  });

  it("is never dropped: the lines always add up to the applications read", () => {
    const mixed = rows("saved", "Saved", " applied", "", null, undefined, 3, {}, ["offer"], "withdrawn");
    const view = pipelineView(ok([...mixed, { id: "no-status" }]));
    expect(view.state).toBe("ready");
    if (view.state !== "ready") return;
    expect(view.lines.reduce((sum, line) => sum + line.count, 0)).toBe(view.total);
    expect(view.total).toBe(11);
    // Only the two rows that said a stage exactly are filed under one.
    expect(lines(ok([...mixed, { id: "no-status" }]))).toEqual(["Saved 1", "Withdrawn 1", `${OTHER_STAGE_LABEL} 9`]);
  });

  it("can be the only line", () => {
    expect(lines(ok(rows("ghosted")))).toEqual([`${OTHER_STAGE_LABEL} 1`]);
  });

  it("does not trust a status that only looks like a property of an object", () => {
    expect(lines(ok(rows("constructor", "toString", "__proto__")))).toEqual([`${OTHER_STAGE_LABEL} 3`]);
  });
});

describe("what was not read", () => {
  it("has no lines and no total while the read is out", () => {
    expect(pipelineView({ kind: "loading" })).toEqual({ state: "loading" });
  });

  it("has no lines and no total when the read failed: a zero is only ever a number that was read", () => {
    expect(pipelineView({ kind: "failed" })).toEqual({ state: "failed" });
  });

  it("says none only when the read answered with none", () => {
    expect(pipelineView(ok([]))).toEqual({ state: "ready", total: 0, lines: [] });
  });
});
