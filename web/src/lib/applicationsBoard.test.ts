import { describe, expect, it } from "vitest";
import type { Application, ApplicationStatus } from "./applicationsTypes";
import {
  CROSS_NAV_HASH,
  CROSS_NAV_ITEMS,
  formatAppliedDate,
  groupByStatus,
  otherStatuses,
  STATUS_LABELS,
  statusCounts,
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

  it("falls back a row with an unrecognized status (a row older than the enforced vocabulary) into Saved rather than dropping it off the board", () => {
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
  const now = new Date("2026-10-05T12:00:00Z");

  it("returns null when date_applied is unset, the common case for a 'saved' application", () => {
    expect(formatAppliedDate(null, now)).toBeNull();
  });

  it("writes a real date_applied the way every trail writes a day", () => {
    expect(formatAppliedDate("2026-09-30T12:00:00Z", now, "UTC")).toBe("Applied Sep 30");
    expect(formatAppliedDate("2026-09-30", now)).toBe("Applied Sep 30");
  });

  it("adds the year when the day is not in the current year", () => {
    expect(formatAppliedDate("2025-12-01", now)).toBe("Applied Dec 1, 2025");
  });

  it("leaves out a value that is not a date rather than drawing 'Invalid Date'", () => {
    expect(formatAppliedDate("not a date", now)).toBeNull();
    expect(formatAppliedDate("2026-02-31", now)).toBeNull();
  });
});

describe("statusCounts", () => {
  it("counts every one of the 7 statuses, zero for the empty ones", () => {
    const counts = statusCounts([
      makeApplication({ id: "a", status: "saved" }),
      makeApplication({ id: "b", status: "saved" }),
      makeApplication({ id: "c", status: "offer" }),
    ]);
    expect(counts).toEqual({ saved: 2, applied: 0, screening: 0, interviewing: 0, offer: 1, rejected: 0, withdrawn: 0 });
  });

  it("counts a row with an unrecognized status under Saved, the column the board puts it in", () => {
    expect(statusCounts([makeApplication({ status: "ghosted" as unknown as ApplicationStatus })]).saved).toBe(1);
  });
});

describe("CROSS_NAV_ITEMS", () => {
  it("has exactly the 7 named cross-nav destinations, in menu order, and no Tailor entry", () => {
    expect(CROSS_NAV_ITEMS).toEqual([
      { hash: "generate", label: "Generate Docs" },
      { hash: "company-intel", label: "Research Company" },
      { hash: "contacts", label: "Find Contacts" },
      { hash: "warm-path-events", label: "Find Events" },
      { hash: "hiring-posts", label: "Find Hiring Posts" },
      { hash: "interview-practice", label: "Practice Interview" },
      { hash: "your-play", label: "Your Play" },
    ]);
    expect(CROSS_NAV_ITEMS.some((item) => item.hash === "tailor")).toBe(false);
  });

  it("draws every menu hash from CROSS_NAV_HASH, the constant the workspace panels' ids also use", () => {
    // A hash typed directly into the menu could drift from the panel id it is
    // meant to scroll to, and a missing scroll target fails silently.
    const known = new Set<string>(Object.values(CROSS_NAV_HASH));
    for (const item of CROSS_NAV_ITEMS) {
      expect(known.has(item.hash)).toBe(true);
    }
    expect(new Set(CROSS_NAV_ITEMS.map((item) => item.hash)).size).toBe(CROSS_NAV_ITEMS.length);
    expect(CROSS_NAV_HASH.hiringPosts).toBe("hiring-posts");
  });
});

// Every hash the menus and the next-step band link to must be an element the workspace renders: a
// missing target fails silently (the link still navigates, the scroll just never finds anything).
// Read from the workspace's source, not rendered, because the panels behind them need a whole
// application to mount.
const sources = import.meta.glob<string>(["../pages/*.tsx", "../components/**/*.tsx", "./workspaceSections.ts", "!../**/*.test.tsx"], {
  query: "?raw",
  import: "default",
  eager: true,
});

// The workspace gives its sections their ids through SECTION_IDS (lib/workspaceSections.ts), whose values are
// CROSS_NAV_HASH's: `id={SECTION_IDS.review}` is a use of CROSS_NAV_HASH.review.
const sectionAliases = (name: string): string[] => {
  const list = Object.entries(sources).find(([path]) => path.endsWith("workspaceSections.ts"))?.[1] ?? "";
  return [...list.matchAll(new RegExp(`\\b(\\w+): CROSS_NAV_HASH\\.${name}\\b`, "g"))].map((match) => match[1]);
};

describe("CROSS_NAV_HASH sections", () => {
  for (const [name, hash] of Object.entries(CROSS_NAV_HASH)) {
    it(`gives ${hash} (CROSS_NAV_HASH.${name}) to an element a page or component renders`, () => {
      const direct = `id=\\{CROSS_NAV_HASH\\.${name}\\}`;
      const viaSections = sectionAliases(name).map((key) => `id=\\{SECTION_IDS\\.${key}\\}`);
      const pattern = new RegExp(`\\b(?:${[direct, ...viaSections].join("|")})`);
      const users = Object.entries(sources).filter(([, source]) => pattern.test(source));
      expect(users.map(([path]) => path)).not.toEqual([]);
    });
  }
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
