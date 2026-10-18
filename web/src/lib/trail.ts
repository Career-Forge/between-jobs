// The trail line: one line under a block that says who acted, on what basis, and when.
//
//   [glyph] actor · basis · when
//
// This module is the grammar and nothing else -- pure, no React, no clock. Callers pass the words
// for each slot and this decides what the line is. Three rules are enforced here rather than left
// to each caller:
//
//  - THE OMIT RULE. A slot whose field the API did not return is left out together with its
//    separator. Nothing is guessed, no "unknown model" is written, no date is approximated. A line
//    with no words left is `null`, and the caller renders no trail at all.
//  - GLYPHS. A fixed set, each with one color and one meaning. A line has a lead glyph and at most
//    one more (`also`), so "at most two colored glyphs" holds by construction. Glyphs are
//    decoration (drawn aria-hidden); the words always carry the meaning, so a glyph is never what
//    tells a reader what happened.
//  - WORDS NOT TO SAY. A trail never says a link was "verified" (a link check means "not found
//    closed", never "verified live") and never names a model it was not told. A slot that does is
//    left out, the same as a missing field; `findForbiddenWord` is exported so a page's tests can
//    pin their own strings. The ban is on the word, on purpose, so that it can be checked by a
//    plain search: "unverified" and "not verified" are left out too. A trail that has to say what
//    was not done says "not checked" or "not found closed"; wording that needs "verified" belongs
//    in the receipt, where this rule does not apply.
//
// "Not set" has no trail and no glyph: `notSet()` is the plain muted word.

export type TrailGlyphName = "model" | "engine" | "code" | "you" | "evidence" | "failed" | "passed";

export type GlyphTone = "violet" | "muted" | "ivory" | "gold" | "danger" | "emerald";

export interface TrailGlyph {
  char: string;
  tone: GlyphTone;
  /** What the glyph means, for the legend and for tests; never read out (the glyph is aria-hidden). */
  meaning: string;
  /** "engine" is drawn as the muted word followed by the violet star, never as the star alone. */
  word?: string;
}

export const TRAIL_GLYPHS: Readonly<Record<TrailGlyphName, TrailGlyph>> = {
  model: { char: "✦", tone: "violet", meaning: "A model produced this output" },
  engine: {
    char: "✦",
    tone: "violet",
    word: "engine",
    meaning: "Code math over signals a model extracted",
  },
  code: { char: "○", tone: "muted", meaning: "Plain code, no model involved; or not checked" },
  you: { char: "●", tone: "ivory", meaning: "You did it" },
  evidence: {
    char: "◆",
    tone: "gold",
    meaning: "Evidence, uncertain, held back for your decision, waiting on your review, or a step to do",
  },
  failed: { char: "✕", tone: "danger", meaning: "Failed, contradicted, or not generated" },
  passed: { char: "✓", tone: "emerald", meaning: "A deterministic pass, a validated key, a done step" },
};

// Text that is drawn in the mono face: dates, times, run ids, hashes.
export interface MonoText {
  readonly mono: string;
}

export function mono(text: string): MonoText {
  return { mono: text };
}

export type SlotPiece = string | MonoText;
// What a caller hands a slot: words, mono text, a mix, or nothing (the field was not returned).
export type Slot = SlotPiece | readonly SlotPiece[] | null | undefined;

export type TrailPart = string | MonoText | { readonly glyph: TrailGlyphName };

export interface TrailInput {
  /** The lead glyph. Without one the line is plain words. */
  glyph?: TrailGlyphName | null;
  /** "Drafted by your model", "Parsed by code", "You tracked this", "via registry". */
  actor?: Slot;
  /** "on your key", "no model involved". */
  basis?: Slot;
  /** A date or time from a real returned field. Always drawn in mono. */
  when?: Slot;
  /** At most one more slot after "when": a count or a confidence ("72%", "1 flagged"). */
  extra?: Slot;
  /** The one other colored glyph a line may carry, with its words ("2 searches failed"). */
  also?: { glyph: TrailGlyphName; text: Slot } | null;
}

export interface TrailModel {
  /** Joined by " · " when drawn. A segment is a run of parts. */
  readonly segments: readonly (readonly TrailPart[])[];
  /** The glyphs on the line, in order, at most two. */
  readonly glyphs: readonly TrailGlyphName[];
  /** The words of the line with the separators, no glyph characters: the accessible name. */
  readonly text: string;
}

export const TRAIL_SEPARATOR = " · ";

// Matches "verified" in any form ("Link verified", "unverified") and "unknown model".
const FORBIDDEN = /verified|unknown model/i;

export function findForbiddenWord(text: string): string | null {
  const match = FORBIDDEN.exec(text);
  return match === null ? null : match[0];
}

function piecesOf(slot: Slot): SlotPiece[] {
  if (slot === null || slot === undefined) return [];
  const list: readonly unknown[] = typeof slot === "string" || !Array.isArray(slot) ? [slot] : slot;
  const pieces: SlotPiece[] = [];
  for (const item of list) {
    if (typeof item === "string") {
      if (item.trim() !== "") pieces.push(item.trim());
    } else if (typeof item === "object" && item !== null && "mono" in item) {
      const text = (item as MonoText).mono;
      if (typeof text === "string" && text.trim() !== "") pieces.push(mono(text.trim()));
    }
  }
  return pieces;
}

function textOfPieces(pieces: readonly SlotPiece[]): string {
  return pieces.map((piece) => (typeof piece === "string" ? piece : piece.mono)).join(" ");
}

// A slot as a run of parts, or null when there is nothing in it or it says something a trail never
// says. Adjacent plain pieces stay separate parts; the drawer puts a space between parts.
function partsOf(slot: Slot, allMono = false): SlotPiece[] | null {
  const pieces = piecesOf(slot);
  if (pieces.length === 0) return null;
  if (findForbiddenWord(textOfPieces(pieces)) !== null) return null;
  return allMono ? pieces.map((piece) => (typeof piece === "string" ? mono(piece) : piece)) : pieces;
}

export function buildTrail(input: TrailInput): TrailModel | null {
  const actor = partsOf(input.actor);
  const basis = partsOf(input.basis);
  const when = partsOf(input.when, true);
  const extra = partsOf(input.extra);
  const alsoText = input.also === null || input.also === undefined ? null : partsOf(input.also.text);

  const segments: TrailPart[][] = [];
  const glyphs: TrailGlyphName[] = [];

  // A glyph is a mark beside the words that say what it means ("Drafted by your model", "on your
  // key"). With neither an actor nor a basis left, a date or a count would carry it alone, so it is
  // not drawn: the line keeps what it has, without a claim about who or what did it.
  const lead = actor !== null || basis !== null ? (input.glyph ?? null) : null;
  const words = [actor, basis, when, extra];
  if (words.every((slot) => slot === null) && alsoText === null) return null;

  // "engine" is the label of the lead, so it is its own first segment: "engine ✦ · code-weighted ...".
  if (lead === "engine") {
    segments.push([{ glyph: "engine" }]);
    glyphs.push("engine");
  }
  let leadPending = lead !== null && lead !== "engine" ? lead : null;
  for (const slot of words) {
    if (slot === null) continue;
    const segment: TrailPart[] = [];
    if (leadPending !== null) {
      segment.push({ glyph: leadPending });
      glyphs.push(leadPending);
      leadPending = null;
    }
    segment.push(...slot);
    segments.push(segment);
  }
  // A lead glyph whose own words were all omitted has nothing to sit beside and is not drawn.
  if (input.also !== null && input.also !== undefined && alsoText !== null) {
    segments.push([{ glyph: input.also.glyph }, ...alsoText]);
    glyphs.push(input.also.glyph);
  }

  const text = segments
    .map((segment) =>
      segment
        .map((part) => (typeof part === "string" ? part : "mono" in part ? part.mono : (TRAIL_GLYPHS[part.glyph].word ?? "")))
        .filter((piece) => piece !== "")
        .join(" "),
    )
    .join(TRAIL_SEPARATOR);

  return { segments, glyphs, text };
}

// "Not set" (or "Not connected"): the words alone, muted, no glyph and no trail. A key or a
// connection that is not set produced nothing, so there is nothing to attribute.
export function notSet(word: "Not set" | "Not connected" = "Not set"): TrailModel {
  return { segments: [[word]], glyphs: [], text: word };
}

// ---------------------------------------------------------------------------------------------
// Dates and times. A trail's "when" comes from a real returned field; these turn one into the
// words a trail uses, and say nothing (null) for anything they cannot read exactly.

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"] as const;

export interface TrailTimeOptions {
  /** Zone to read an instant in. Omit for the viewer's own; tests pass "UTC". */
  timeZone?: string;
}

export interface TrailDateOptions extends TrailTimeOptions {
  /**
   * The moment "this year" is read from. Required: a date from another year than this one is
   * written with its year, and without a clock to compare with, "Sep 22" could be any September.
   * (This module has no clock of its own; the caller passes `new Date()`.)
   */
  now: Date;
}

const DATE_ONLY = /^(\d{4})-(\d{2})-(\d{2})$/;

function dayWords(year: number, monthIndex: number, day: number, options: TrailDateOptions): string {
  const words = `${MONTHS[monthIndex]} ${day}`;
  const nowYear = Number(new Intl.DateTimeFormat("en-US", { year: "numeric", timeZone: options.timeZone }).format(options.now));
  return nowYear === year ? words : `${words}, ${year}`;
}

// "2026-09-22" -> "Sep 22"; "2026-09-22T14:00:00Z" -> "Sep 22" in the zone asked for; a year other
// than `now`'s is written out. A date-only value is a calendar day and is never shifted by a zone.
// Anything unreadable is null: a day that does not exist (2026-02-31, with or without a time), an
// hour or minute out of range, a zone the runtime does not know, or a missing or invalid `now`.
export function formatTrailDate(value: unknown, options: TrailDateOptions): string | null {
  // `options?.` because an untyped caller can still leave the whole argument out.
  const now: unknown = options?.now;
  if (typeof value !== "string" || !(now instanceof Date) || Number.isNaN(now.getTime())) return null;
  try {
    const text = value.trim();
    const plain = DATE_ONLY.exec(text);
    if (plain !== null) {
      const [year, month, day] = [Number(plain[1]), Number(plain[2]), Number(plain[3])];
      if (!isCalendarDay(year, month, day)) return null;
      return dayWords(year, month - 1, day, options);
    }
    const instant = readInstant(text);
    if (instant === null) return null;
    const parts = zonedParts(instant, options.timeZone);
    return dayWords(parts.year, parts.month - 1, parts.day, options);
  } catch {
    // Intl throws a RangeError for a time zone it does not know.
    return null;
  }
}

// "2026-09-22T06:00:00Z" -> "06:00" (24-hour). A date with no time in it has no time to show.
export function formatTrailTime(value: unknown, options: TrailTimeOptions = {}): string | null {
  if (typeof value !== "string") return null;
  try {
    const text = value.trim();
    if (DATE_ONLY.test(text)) return null;
    const instant = readInstant(text);
    if (instant === null) return null;
    const parts = zonedParts(instant, options.timeZone);
    return `${String(parts.hour).padStart(2, "0")}:${String(parts.minute).padStart(2, "0")}`;
  } catch {
    return null;
  }
}

function isCalendarDay(year: number, month: number, day: number): boolean {
  const probe = new Date(Date.UTC(year, month - 1, day));
  return probe.getUTCFullYear() === year && probe.getUTCMonth() === month - 1 && probe.getUTCDate() === day;
}

const INSTANT = /^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::(\d{2})(?:\.(\d+))?)?(?:Z|([+-])(\d{2}):?(\d{2}))$/;

// An ISO 8601 instant with a zone designator (Z or +hh:mm); a bare local time is not an instant.
// Every field is range-checked rather than handed to Date.parse, which rolls an impossible day
// ("2026-02-31T10:00:00Z") over into the next month instead of refusing it.
function readInstant(text: string): Date | null {
  const match = INSTANT.exec(text);
  if (match === null) return null;
  const [year, month, day, hour, minute] = [match[1], match[2], match[3], match[4], match[5]].map(Number);
  const second = match[6] === undefined ? 0 : Number(match[6]);
  const millis = match[7] === undefined ? 0 : Math.floor(Number(`0.${match[7]}`) * 1000);
  if (!isCalendarDay(year, month, day) || hour > 23 || minute > 59 || second > 59) return null;
  let offsetMinutes = 0;
  if (match[8] !== undefined) {
    const [offsetHour, offsetMinute] = [Number(match[9]), Number(match[10])];
    if (offsetHour > 23 || offsetMinute > 59) return null;
    offsetMinutes = (match[8] === "-" ? -1 : 1) * (offsetHour * 60 + offsetMinute);
  }
  const date = new Date(0);
  date.setUTCFullYear(year, month - 1, day);
  date.setUTCHours(hour, minute, second, millis);
  return new Date(date.getTime() - offsetMinutes * 60_000);
}

function zonedParts(date: Date, timeZone: string | undefined) {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone,
    hourCycle: "h23",
    year: "numeric",
    month: "numeric",
    day: "numeric",
    hour: "numeric",
    minute: "numeric",
  }).formatToParts(date);
  const pick = (type: string) => Number(parts.find((part) => part.type === type)?.value);
  return { year: pick("year"), month: pick("month"), day: pick("day"), hour: pick("hour"), minute: pick("minute") };
}

// ---------------------------------------------------------------------------------------------
// Receipts: what opens under a trail line. Built only from returned fields: a row whose value was
// not returned is left out, and a receipt with no rows left is null (the trail is then plain text,
// since there is nothing to open). Values are drawn in mono. Unlike the trail line itself, a
// receipt's values are shown as returned, so the words-not-to-say rule does not apply to them.

export type ReceiptRowInput =
  | { label: string; value: string | number | null | undefined }
  | { note: string | null | undefined };

export type ReceiptRow = { label: string; value: string } | { note: string };

export interface ReceiptModel {
  readonly rows: readonly ReceiptRow[];
}

export function buildReceipt(rows: readonly ReceiptRowInput[]): ReceiptModel | null {
  const kept: ReceiptRow[] = [];
  for (const row of rows) {
    if ("note" in row) {
      const note = typeof row.note === "string" ? row.note.trim() : "";
      if (note !== "") kept.push({ note });
      continue;
    }
    // NaN and Infinity are what a failed calculation leaves behind, not something that was returned.
    const value =
      typeof row.value === "number" ? (Number.isFinite(row.value) ? String(row.value) : "") : typeof row.value === "string" ? row.value.trim() : "";
    if (value !== "" && row.label.trim() !== "") kept.push({ label: row.label.trim(), value });
  }
  return kept.length === 0 ? null : { rows: kept };
}
