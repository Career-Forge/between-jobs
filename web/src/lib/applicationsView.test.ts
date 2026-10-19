import { describe, expect, it } from "vitest";
import { ALL_STATUSES, type Application, type ApplicationStatus } from "./applicationsTypes";
import {
  PHONE_STATUS_KEY,
  STATUS_CHIPS,
  cardTrail,
  firstOccupiedStatus,
  phoneStatus,
  readStoredStatus,
  sourceWords,
  statusOptionLabel,
  storeStatus,
  type StatusStorage,
} from "./applicationsView";
import { buildTrail, findForbiddenWord } from "./trail";

const NOW = new Date("2026-10-05T12:00:00Z");

function app(overrides: Partial<Application> = {}): Application {
  return {
    id: "app-1",
    status: "saved",
    source_channel: "discover",
    created_at: "2026-09-22T12:00:00Z",
    updated_at: "2026-09-22T12:00:00Z",
    date_applied: null,
    snapshot: null,
    resume_exists: false,
    ...overrides,
  };
}

const counts = (overrides: Partial<Record<ApplicationStatus, number>> = {}) =>
  Object.fromEntries(ALL_STATUSES.map((status) => [status, overrides[status] ?? 0])) as Record<ApplicationStatus, number>;

describe("the status chips", () => {
  it("give every status its own shape, and only Offer is emerald", () => {
    expect(Object.keys(STATUS_CHIPS)).toEqual([...ALL_STATUSES]);
    expect(new Set(Object.values(STATUS_CHIPS).map((chip) => chip.glyph)).size).toBe(7);
    expect(Object.entries(STATUS_CHIPS).filter(([, chip]) => chip.tone === "emerald").map(([status]) => status)).toEqual(["offer"]);
    expect(Object.entries(STATUS_CHIPS).filter(([, chip]) => chip.tone === "muted").map(([status]) => status)).toEqual(["rejected", "withdrawn"]);
  });
});

describe("the card's trail", () => {
  const text = (application: Application) => buildTrail(cardTrail(application, NOW, "UTC"))?.text ?? null;

  it("says who tracked it, from where and when, from the fields the list returned", () => {
    expect(text(app())).toBe("You tracked this · from Discover · Sep 22");
    expect(text(app({ source_channel: "web" }))).toBe("You tracked this · added on the web · Sep 22");
    expect(text(app({ source_channel: "telegram" }))).toBe("You tracked this · from Telegram · Sep 22");
  });

  it("leaves out a channel it does not know rather than guessing from its name", () => {
    expect(text(app({ source_channel: "extension" }))).toBe("You tracked this · Sep 22");
    expect(text(app({ source_channel: "" }))).toBe("You tracked this · Sep 22");
    expect(sourceWords(undefined)).toBeNull();
    expect(sourceWords("constructor")).toBeNull();
  });

  it("leaves out a date that was not returned or cannot be read, never approximating one", () => {
    expect(text(app({ created_at: "" }))).toBe("You tracked this · from Discover");
    expect(text(app({ created_at: "yesterday" }))).toBe("You tracked this · from Discover");
    expect(text(app({ created_at: "2026-02-31T00:00:00Z" }))).toBe("You tracked this · from Discover");
  });

  it("writes a day in another year with its year", () => {
    expect(text(app({ created_at: "2025-11-30T12:00:00Z" }))).toBe("You tracked this · from Discover · Nov 30, 2025");
  });

  it("uses the 'you' glyph, never a model's, and never says a link was checked or a status confirmed", () => {
    const trail = buildTrail(cardTrail(app(), NOW, "UTC"));
    expect(trail?.glyphs).toEqual(["you"]);
    expect(findForbiddenWord(trail?.text ?? "")).toBeNull();
    expect(trail?.text).not.toMatch(/checked|confirmed|reviewed|model/i);
  });

  it("does not depend on the fields that only describe a draft or an application date", () => {
    expect(text(app({ resume_exists: true, date_applied: "2026-09-30" }))).toBe(text(app()));
  });
});

describe("the phone's status picker", () => {
  it("writes each option as the status and its count", () => {
    expect(statusOptionLabel("saved", 2)).toBe("Saved (2)");
    expect(statusOptionLabel("screening", 0)).toBe("Screening (0)");
  });

  it("opens on the first status, in pipeline order, that has an application", () => {
    expect(firstOccupiedStatus(counts({ interviewing: 1, offer: 2 }))).toBe("interviewing");
    expect(firstOccupiedStatus(counts({ saved: 1, offer: 2 }))).toBe("saved");
  });

  it("opens on Saved when nothing is tracked", () => {
    expect(firstOccupiedStatus(counts())).toBe("saved");
  });

  it("prefers the status this browser remembered, even when it is empty now", () => {
    expect(phoneStatus("rejected", counts({ saved: 3 }))).toBe("rejected");
    expect(phoneStatus(null, counts({ applied: 1 }))).toBe("applied");
  });
});

describe("the remembered status", () => {
  function memory(initial: Record<string, string> = {}): StatusStorage & { map: Map<string, string> } {
    const map = new Map(Object.entries(initial));
    return { map, getItem: (key) => map.get(key) ?? null, setItem: (key, value) => void map.set(key, value) };
  }

  it("round-trips one of the seven statuses", () => {
    const storage = memory();
    storeStatus(storage, "offer");
    expect(storage.map.get(PHONE_STATUS_KEY)).toBe("offer");
    expect(readStoredStatus(storage)).toBe("offer");
  });

  it("never reads back anything but a status", () => {
    for (const stored of ["", "ghosted", "Saved", "__proto__", "1"]) {
      expect(readStoredStatus(memory({ [PHONE_STATUS_KEY]: stored }))).toBeNull();
    }
    expect(readStoredStatus(memory())).toBeNull();
  });

  it("works without storage at all, and when storage throws", () => {
    const throwing: StatusStorage = {
      getItem: () => {
        throw new Error("blocked");
      },
      setItem: () => {
        throw new Error("blocked");
      },
    };
    expect(readStoredStatus(null)).toBeNull();
    expect(readStoredStatus(throwing)).toBeNull();
    expect(() => storeStatus(null, "saved")).not.toThrow();
    expect(() => storeStatus(throwing, "saved")).not.toThrow();
  });
});
