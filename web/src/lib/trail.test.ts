import { describe, expect, it } from "vitest";
import {
  TRAIL_GLYPHS,
  TRAIL_SEPARATOR,
  buildReceipt,
  buildTrail,
  findForbiddenWord,
  formatTrailDate,
  formatTrailTime,
  mono,
  notSet,
  type Slot,
  type TrailGlyphName,
  type TrailInput,
} from "./trail";

const GLYPHS = Object.keys(TRAIL_GLYPHS) as TrailGlyphName[];
const SLOTS = ["actor", "basis", "when", "extra"] as const;

// A seeded generator, so a failure replays.
function mulberry32(seed: number): () => number {
  let state = seed;
  return () => {
    state = (state + 0x6d2b79f5) | 0;
    let t = Math.imul(state ^ (state >>> 15), 1 | state);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

describe("the glyph set", () => {
  it("has the seven glyphs, each with the color and meaning the design gives it", () => {
    expect([...GLYPHS].sort()).toEqual(["code", "engine", "evidence", "failed", "model", "passed", "you"]);
    expect(TRAIL_GLYPHS.model).toMatchObject({ char: "✦", tone: "violet" });
    expect(TRAIL_GLYPHS.engine).toMatchObject({ char: "✦", tone: "violet", word: "engine" });
    expect(TRAIL_GLYPHS.code).toMatchObject({ char: "○", tone: "muted" });
    expect(TRAIL_GLYPHS.you).toMatchObject({ char: "●", tone: "ivory" });
    expect(TRAIL_GLYPHS.evidence).toMatchObject({ char: "◆", tone: "gold" });
    expect(TRAIL_GLYPHS.failed).toMatchObject({ char: "✕", tone: "danger" });
    expect(TRAIL_GLYPHS.passed).toMatchObject({ char: "✓", tone: "emerald" });
  });

  it("puts the word 'engine' beside the star and never lets the star stand alone for it", () => {
    const model = buildTrail({ glyph: "engine", actor: "code-weighted from your model's sub-scores" });
    expect(model?.text).toBe("engine · code-weighted from your model's sub-scores");
    expect(model?.segments[0]).toEqual([{ glyph: "engine" }]);
  });
});

describe("the grammar", () => {
  it("joins the slots with a middle dot: glyph actor, basis, when, in that order", () => {
    const model = buildTrail({
      glyph: "model",
      actor: "Drafted by your model",
      basis: "on your OpenRouter key",
      when: "Sep 22",
      extra: "72%",
    });
    expect(model?.text).toBe("Drafted by your model · on your OpenRouter key · Sep 22 · 72%");
    expect(model?.glyphs).toEqual(["model"]);
    expect(model?.segments[0][0]).toEqual({ glyph: "model" });
  });

  it("draws the 'when' slot in mono, and mono pieces inside other slots", () => {
    const model = buildTrail({ actor: ["Run", mono("a1b2c3d4")], when: "Sep 22" });
    expect(model?.segments[0]).toEqual(["Run", { mono: "a1b2c3d4" }]);
    expect(model?.segments[1]).toEqual([{ mono: "Sep 22" }]);
  });

  it("carries a second colored glyph only through `also`, so a line never has more than two", () => {
    const model = buildTrail({ glyph: "model", actor: "Searched", also: { glyph: "failed", text: "2 searches failed" } });
    expect(model?.glyphs).toEqual(["model", "failed"]);
    expect(model?.text).toBe("Searched · 2 searches failed");
  });

  it("has no line at all when nothing is left to say", () => {
    expect(buildTrail({})).toBeNull();
    expect(buildTrail({ glyph: "model" })).toBeNull();
    expect(buildTrail({ glyph: "engine", actor: null, basis: "", when: "  " })).toBeNull();
  });

  it("writes 'Not set' as the words alone: no glyph, no separator", () => {
    expect(notSet()).toEqual({ segments: [["Not set"]], glyphs: [], text: "Not set" });
    expect(notSet("Not connected").text).toBe("Not connected");
  });
});

// Whether a slot value, as the random test builds them, has any words in it.
function expectedHas(value: Slot): boolean {
  if (value === null || value === undefined) return false;
  const pieces = typeof value === "string" || !Array.isArray(value) ? [value] : value;
  return pieces.some((piece) => (typeof piece === "string" ? piece.trim() !== "" : piece.mono.trim() !== ""));
}

describe("the omit rule", () => {
  // Every combination of present and absent slots, under every lead glyph: the line is exactly the
  // present slots in order, with a separator only between two of them.
  const cases: { present: boolean[]; glyph: TrailGlyphName | null; alsoPresent: boolean }[] = [];
  for (let mask = 0; mask < 16; mask++) {
    for (const glyph of [null, ...GLYPHS]) {
      for (const alsoPresent of [false, true]) {
        cases.push({ present: SLOTS.map((_, index) => (mask & (1 << index)) !== 0), glyph, alsoPresent });
      }
    }
  }

  it.each(cases.map((c) => [JSON.stringify(c), c] as const))("leaves out what was not returned, with its separator: %s", (_name, c) => {
    const input: TrailInput = { glyph: c.glyph };
    const words: string[] = [];
    SLOTS.forEach((slot, index) => {
      if (c.present[index]) {
        input[slot] = `${slot}-word`;
        words.push(`${slot}-word`);
      }
    });
    if (c.alsoPresent) {
      input.also = { glyph: "failed", text: "also-word" };
      words.push("also-word");
    }
    const model = buildTrail(input);
    if (words.length === 0) {
      expect(model).toBeNull();
      return;
    }
    const carriesGlyph = c.present[0] || c.present[1];
    const lead = c.glyph === "engine" && carriesGlyph ? ["engine"] : [];
    expect(model?.text).toBe([...lead, ...words].join(TRAIL_SEPARATOR));
    const expectedLeadGlyphs = c.glyph !== null && carriesGlyph ? [c.glyph] : [];
    expect(model?.glyphs).toEqual([...expectedLeadGlyphs, ...(c.alsoPresent ? ["failed"] : [])]);
    expect(model?.text.startsWith(TRAIL_SEPARATOR.trim())).toBe(false);
    expect(model?.text.endsWith(TRAIL_SEPARATOR.trim())).toBe(false);
    expect(model?.text).not.toContain(`${TRAIL_SEPARATOR}${TRAIL_SEPARATOR}`);
    // Never more than two glyphs, and a missing slot leaves no trace of itself.
    expect(model?.glyphs.length).toBeLessThanOrEqual(2);
    SLOTS.forEach((slot, index) => {
      if (!c.present[index]) expect(model?.text).not.toContain(`${slot}-word`);
    });
  });

  it("holds under random mixes of absent, empty, whitespace and array slots", () => {
    const random = mulberry32(20261018);
    const wordFor = (name: string) => `${name}${Math.floor(random() * 1000)}`;
    for (let run = 0; run < 500; run++) {
      const input: TrailInput = { glyph: random() < 0.2 ? null : GLYPHS[Math.floor(random() * GLYPHS.length)] };
      const expected: string[] = [];
      for (const slot of SLOTS) {
        const roll = random();
        let value: Slot;
        if (roll < 0.2) value = undefined;
        else if (roll < 0.35) value = null;
        else if (roll < 0.5) value = random() < 0.5 ? "" : "   ";
        else if (roll < 0.6) value = ["", "  ", mono("")];
        else if (roll < 0.8) {
          const word = wordFor(slot);
          value = word;
          expected.push(word);
        } else {
          const a = wordFor(slot);
          const b = wordFor(slot);
          value = [a, "", mono(b)];
          expected.push(`${a} ${b}`);
        }
        input[slot] = value;
      }
      const model = buildTrail(input);
      if (expected.length === 0) {
        expect(model).toBeNull();
      } else {
        const carriesGlyph = input.actor !== undefined && expectedHas(input.actor) || input.basis !== undefined && expectedHas(input.basis);
        const lead = input.glyph === "engine" && carriesGlyph ? ["engine"] : [];
        expect(model?.text).toBe([...lead, ...expected].join(TRAIL_SEPARATOR));
      }
    }
  });

  it("drops a glyph that has no actor or basis beside it, rather than let it mark a bare date", () => {
    expect(buildTrail({ glyph: "model", when: "Sep 22" })).toMatchObject({ glyphs: [], text: "Sep 22" });
    expect(buildTrail({ glyph: "engine", extra: "72%" })).toMatchObject({ glyphs: [], text: "72%" });
    expect(buildTrail({ glyph: "model", basis: "on your OpenRouter key" })).toMatchObject({ glyphs: ["model"] });
  });

  it("does not make up a model name or a date for a slot that is missing", () => {
    const model = buildTrail({ glyph: "model", actor: "Drafted by your model" });
    expect(model?.text).toBe("Drafted by your model");
    expect(model?.text).not.toMatch(/\d/);
  });
});

describe("words a trail never says", () => {
  it("finds 'verified' in any form and 'unknown model', whatever the case", () => {
    expect(findForbiddenWord("Link verified")).toBe("verified");
    expect(findForbiddenWord("UNVERIFIED claim")).toBe("VERIFIED");
    expect(findForbiddenWord("Unknown Model")).toBe("Unknown Model");
    expect(findForbiddenWord("Link checked")).toBeNull();
  });

  it("leaves a slot out rather than say them", () => {
    const model = buildTrail({ glyph: "passed", actor: "Link verified", basis: "on the company's job board at last poll" });
    expect(model?.text).toBe("on the company's job board at last poll");
    expect(buildTrail({ glyph: "model", actor: "Drafted by an unknown model" })).toBeNull();
    expect(buildTrail({ actor: ["Checked", mono("verified")] })).toBeNull();
  });

  it("never lets either word through, under any mix of slots", () => {
    const random = mulberry32(7);
    const pool = ["Link verified", "unknown model", "Checked by code", "via registry", "Sep 22", "72%", "UNKNOWN MODEL", "no model involved"];
    for (let run = 0; run < 400; run++) {
      const pick = () => (random() < 0.3 ? undefined : pool[Math.floor(random() * pool.length)]);
      const model = buildTrail({
        glyph: GLYPHS[Math.floor(random() * GLYPHS.length)],
        actor: pick(),
        basis: pick(),
        when: pick(),
        extra: pick(),
        also: random() < 0.3 ? { glyph: "failed", text: pick() } : null,
      });
      if (model !== null) expect(findForbiddenWord(model.text)).toBeNull();
    }
  });
});

describe("dates and times from returned fields", () => {
  it("reads a calendar day without shifting it by a zone", () => {
    expect(formatTrailDate("2026-09-22")).toBe("Sep 22");
    expect(formatTrailDate("2026-01-05", { timeZone: "Pacific/Kiritimati" })).toBe("Jan 5");
  });

  it("reads an instant in the zone asked for", () => {
    expect(formatTrailDate("2026-09-22T23:30:00Z", { timeZone: "UTC" })).toBe("Sep 22");
    expect(formatTrailDate("2026-09-22T23:30:00Z", { timeZone: "Asia/Kolkata" })).toBe("Sep 23");
    expect(formatTrailTime("2026-09-22T06:00:00Z", { timeZone: "UTC" })).toBe("06:00");
    expect(formatTrailTime("2026-09-22T06:00:00-04:00", { timeZone: "UTC" })).toBe("10:00");
  });

  it("adds the year only when another year than `now` is meant", () => {
    const now = new Date("2026-10-18T12:00:00Z");
    expect(formatTrailDate("2026-09-22", { now, timeZone: "UTC" })).toBe("Sep 22");
    expect(formatTrailDate("2025-09-22", { now, timeZone: "UTC" })).toBe("Sep 22, 2025");
  });

  it("says nothing for what it cannot read exactly", () => {
    for (const bad of [null, undefined, 42, "", "yesterday", "2026-02-31", "2026-13-01", "2026-09-22T06:00:00", "09/22/2026", {}]) {
      expect(formatTrailDate(bad)).toBeNull();
    }
    expect(formatTrailTime("2026-09-22")).toBeNull();
    expect(formatTrailTime("not a time")).toBeNull();
  });
});

describe("receipts", () => {
  it("keeps the rows that have a value and drops the rest", () => {
    const receipt = buildReceipt([
      { label: "run", value: "a1b2c3d4" },
      { label: "pdf", value: null },
      { label: "ATS", value: "64 -> 72" },
      { label: "gate", value: "" },
      { label: "attempts", value: 2 },
      { note: "From an earlier successful generation" },
      { note: undefined },
    ]);
    expect(receipt?.rows).toEqual([
      { label: "run", value: "a1b2c3d4" },
      { label: "ATS", value: "64 -> 72" },
      { label: "attempts", value: "2" },
      { note: "From an earlier successful generation" },
    ]);
  });

  it("is null when no row has a value, so nothing offers to open an empty receipt", () => {
    expect(buildReceipt([])).toBeNull();
    expect(buildReceipt([{ label: "run", value: undefined }, { note: "  " }])).toBeNull();
  });
});
