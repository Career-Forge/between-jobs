import { describe, expect, it } from "vitest";
import type { Application, ApplicationStatus } from "./applicationsTypes";
import {
  CROSS_NAV_ITEMS,
  formatAppliedDate,
  groupByStatus,
  otherStatuses,
  STATUS_LABELS,
} from "./applicationsBoard";

function makeApplication(overrides: Partial<Application> = {}): Application {
  return {
    id: "app-1",
    status: "saved",
    source_channel: "web",
    created_at: "2026-08-01T00:00:00Z",
    updated_at: "2026-08-01T00:00:00Z",
    date_applied: null,
    snapshot: null,
    resume_exists: false,
    ...overrides,
  };
}

describe("groupByStatus", () => {
  it("buckets every application under its own status", () => {
    const applications = [
      makeApplication({ id: "a", status: "saved" }),
      makeApplication({ id: "b", status: "offer" }),
      makeApplication({ id: "c", status: "saved" }),
    ];
    const groups = groupByStatus(applications);
    expect(groups.saved.map((a) => a.id)).toEqual(["a", "c"]);
    expect(groups.offer.map((a) => a.id)).toEqual(["b"]);
    expect(groups.applied).toEqual([]);
  });

  it("returns every one of the 7 columns, including empty ones, so the board never drops a column", () => {
    const groups = groupByStatus([]);
    expect(Object.keys(groups)).toEqual([
      "saved",
      "applied",
      "screening",
      "interviewing",
      "offer",
      "rejected",
      "withdrawn",
    ]);
    for (const status of Object.keys(groups) as ApplicationStatus[]) {
      expect(groups[status]).toEqual([]);
    }
  });

  it("falls back a row with an unrecognized status (pre-K1 data) into Saved rather than dropping it off the board", () => {
    const applications = [
      makeApplication({ id: "x", status: "ghosted" as unknown as ApplicationStatus }),
    ];
    const groups = groupByStatus(applications);
    expect(groups.saved.map((a) => a.id)).toEqual(["x"]);
  });

  it("preserves each column's relative order rather than re-sorting", () => {
    const applications = [
      makeApplication({ id: "first", status: "interviewing" }),
      makeApplication({ id: "second", status: "interviewing" }),
      makeApplication({ id: "third", status: "interviewing" }),
    ];
    const groups = groupByStatus(applications);
    expect(groups.interviewing.map((a) => a.id)).toEqual(["first", "second", "third"]);
  });
});

describe("otherStatuses", () => {
  it("returns the other 6 statuses in board order, excluding the current one", () => {
    expect(otherStatuses("screening")).toEqual([
      "saved",
      "applied",
      "interviewing",
      "offer",
      "rejected",
      "withdrawn",
    ]);
  });

  it("excludes whichever status is passed, for every one of the 7", () => {
    for (const status of [
      "saved",
      "applied",
      "screening",
      "interviewing",
      "offer",
      "rejected",
      "withdrawn",
    ] as ApplicationStatus[]) {
      const others = otherStatuses(status);
      expect(others).toHaveLength(6);
      expect(others).not.toContain(status);
    }
  });
});

describe("formatAppliedDate", () => {
  it("returns null when date_applied is unset, the common case for a 'saved' application", () => {
    expect(formatAppliedDate(null)).toBeNull();
  });

  it("formats a real date_applied into an 'Applied <date>' caption", () => {
    expect(formatAppliedDate("2026-08-01T00:00:00Z")).toBe(
      `Applied ${new Date("2026-08-01T00:00:00Z").toLocaleDateString()}`,
    );
  });
});

describe("CROSS_NAV_ITEMS", () => {
  it("has exactly the 3 named cross-nav destinations, in menu order, and no Tailor entry", () => {
    expect(CROSS_NAV_ITEMS).toEqual([
      { hash: "generate", label: "Generate Docs" },
      { hash: "company-intel", label: "Research Company" },
      { hash: "interview-practice", label: "Practice Interview" },
    ]);
    expect(CROSS_NAV_ITEMS.some((item) => item.hash === "tailor")).toBe(false);
  });
});

describe("STATUS_LABELS", () => {
  it("maps each of the 7 enforced statuses to its exact display label", () => {
    // Pins the actual label strings, not just the key set -- a typo (e.g.
    // "Intervewing") or a swapped mapping (e.g. screening -> "Interviewing")
    // would still pass a key-set-only assertion since the keys don't change.
    expect(STATUS_LABELS).toEqual({
      saved: "Saved",
      applied: "Applied",
      screening: "Screening",
      interviewing: "Interviewing",
      offer: "Offer",
      rejected: "Rejected",
      withdrawn: "Withdrawn",
    });
  });
});
