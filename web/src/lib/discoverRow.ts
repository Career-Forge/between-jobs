// What one Discover result row says, worked out from the fields the API returned and nothing else:
// its fit block, its trail line and the receipt behind it. Pure -- no React, no clock (the caller
// passes `now`), no request.
//
// THE OMIT RULE. A slot whose field was not returned is left out, never guessed: a posting with no
// `posted_at` has no "when", a score with no bottleneck has no "Lowest part", a salary that was not
// listed has no salary line. "Unknown" is said as unknown (a location the model could not place
// reads "can't tell"), never as a no.
//
// LINK WORDS. `link_checked` is true on every registry row (stamped from the poller's own
// absence tracking, not a probe) and on every live-search row the liveness check kept, including
// rows whose probe was inconclusive or timed out: it means "not found closed", never "verified
// live". So a registry row says where it came from and when it was last polled, a live-search row
// that was kept says "Link checked", and one that never reached the check says it was not checked.
// The word the trail never says is not in this file (lib/trail.ts refuses it as well).

import { subScoreLabel } from "./discover";
import type { JobCard, JobScore, LocationMatch, ScoreBin } from "./discoverTypes";
import { formatTrailDate, type ReceiptRowInput, type TrailInput } from "./trail";

// The ladder's rungs, lowest first, as the engine names its bins.
export const FIT_LEVELS: readonly ScoreBin[] = ["Poor", "Mixed", "Good", "Strong"];

export const NEUTRAL_FALLBACK = "neutral_fallback";

export function isFallback(strategy: string): boolean {
  return strategy === NEUTRAL_FALLBACK;
}

// ── the source ─────────────────────────────────────────────────────────────

// The engine's URL tiers, in words: 1 is the address of a company's own applicant-tracking board,
// 1.5 a structured job-search API, 2 a curated job board, 2.5 a careers page. The tiers that rank
// below those (an aggregator, an address nothing is known about) are dropped before a search
// answers, so a row never carries one; a number this table does not know is shown as a number alone.
const TIER_WORDS: ReadonlyArray<readonly [number, string]> = [
  [1, "a company's job board"],
  [1.5, "a job-search provider's listing"],
  [2, "a curated job board"],
  [2.5, "a company careers page"],
];

export function tierWords(tier: number): string | null {
  if (!Number.isFinite(tier)) return null;
  const known = TIER_WORDS.find(([value]) => value === tier);
  return `tier ${tier}${known === undefined ? "" : ` -- ${known[1]}`}`;
}

// ── the trail ──────────────────────────────────────────────────────────────

export const REGISTRY_BASIS = "on the company's job board at last poll";

// The row's last line. Registry rows say where they came from and that it was the company's board at
// the last poll (no time: the card carries `posted_at`, not when the poller last saw the posting).
// A live-search row says "Link checked" only when the liveness check kept it, and otherwise that its
// link was not checked yet. `when` is the posting's own date, and is left out when it is not returned.
export function rowTrail(job: JobCard, now: Date, timeZone?: string): TrailInput {
  const date = job.posted_at === null ? null : formatTrailDate(job.posted_at, { now, timeZone });
  const when = date === null ? null : `Posted ${date}`;
  if (job.provider === "registry") {
    return { glyph: "code", actor: "via registry", basis: REGISTRY_BASIS, when };
  }
  return {
    glyph: job.link_checked ? "passed" : "code",
    actor: job.link_checked ? "Link checked" : "Link not checked yet",
    basis: `via ${job.provider}`,
    when,
  };
}

// ── location and sponsorship ───────────────────────────────────────────────

const LOCATION_MATCHES: readonly LocationMatch[] = ["match", "mismatch", "unknown"];

function detectedOf(score: JobScore): string | null {
  return typeof score.detected_location === "string" && score.detected_location.trim() !== "" ? score.detected_location.trim() : null;
}

// The model's three-way reading of where a job is against the person's location: a match, a
// mismatch, or "can't tell" (which is what an unreadable value is too). `detected` is where the
// model read the job to be, shown beside it when it said.
export function locationReading(score: JobScore): { state: LocationMatch; words: string } {
  const state: LocationMatch = LOCATION_MATCHES.includes(score.location_match) ? score.location_match : "unknown";
  const detected = detectedOf(score);
  const head = state === "match" ? "Location matches" : state === "mismatch" ? "Location doesn't match" : "Location: can't tell";
  return { state, words: detected === null ? head : `${head} -- the model read it as ${detected}` };
}

export const SPONSORSHIP_TAG = "text match, no model";

// A match over the posting's own text (what the card holds of it), never a model's judgement and
// never a statement about the employer. "Not stated" is what no match looks like, and says so.
export function sponsorshipWords(signal: string): string {
  if (signal === "explicit_yes") return "The posting text mentions visa sponsorship";
  if (signal === "explicit_no") return "The posting text rules out visa sponsorship";
  return "Visa sponsorship is not stated in the text read";
}

export function hasSponsorshipSignal(signal: string): boolean {
  return signal === "explicit_yes" || signal === "explicit_no";
}

// ── the fit block ──────────────────────────────────────────────────────────

export interface FitBar {
  key: string;
  label: string;
  value: number;
  /** Compensation, when it counts, is a constant today and says what it is not. */
  detail: string | null;
}

export type FitView =
  /** Past the scoring batch: no score block at all. */
  | { kind: "unscored" }
  /** The scoring model's reply could not be read: a rung and a number would be made up. */
  | { kind: "placeholder" }
  | {
      kind: "scored";
      level: ScoreBin;
      score100: number;
      oneLiner: string;
      bars: readonly FitBar[];
      /** "Not applied: Company Health -- company isn't in the tier list": dropped, never drawn as 0. */
      notApplied: readonly string[];
      /** "Lowest part: Experience"; null when the response named none. */
      lowest: string | null;
      location: { state: LocationMatch; words: string };
    };

export const COMPENSATION_DETAIL = "Salary listed; not compared to your expectations";

// Why a dimension was left out of the composite, from the fields the card holds. Only Company
// Health and Compensation can drop; the reason for Company Health is that the company is not in
// the engine's tier list, and Compensation drops when no salary came with the posting.
function notAppliedReason(key: string, job: JobCard): string | null {
  if (key === "company_health") return "company isn't in the tier list";
  if (key === "compensation") return job.salary_min == null ? "no salary listed" : null;
  return null;
}

function isScore(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

export function fitViewOf(job: JobCard, scoringStrategy: string): FitView {
  const score = job.score;
  if (score === null) return { kind: "unscored" };
  if (isFallback(scoringStrategy)) return { kind: "placeholder" };
  const dropped = Array.isArray(score.inapplicable_dims) ? score.inapplicable_dims : [];
  const subScores: Record<string, unknown> = typeof score.sub_scores === "object" && score.sub_scores !== null ? { ...score.sub_scores } : {};
  const bars: FitBar[] = Object.entries(subScores)
    .filter(([key, value]) => !dropped.includes(key) && isScore(value))
    .map(([key, value]) => ({
      key,
      label: subScoreLabel(key),
      value: value as number,
      detail: key === "compensation" ? COMPENSATION_DETAIL : null,
    }));
  return {
    kind: "scored",
    level: score.bin,
    score100: score.score100,
    oneLiner: typeof score.one_liner === "string" ? score.one_liner.trim() : "",
    bars,
    notApplied: dropped.map((key) => {
      const reason = notAppliedReason(key, job);
      return reason === null ? `Not applied: ${subScoreLabel(key)}` : `Not applied: ${subScoreLabel(key)} -- ${reason}`;
    }),
    lowest: typeof score.bottleneck === "string" && score.bottleneck !== "" ? subScoreLabel(score.bottleneck) : null,
    location: locationReading(score),
  };
}

// ── the receipt ────────────────────────────────────────────────────────────

function linkCheckWords(job: JobCard): string {
  if (job.provider === "registry") {
    return "not probed -- a registry row rests on the poller's last pass over the company's board, which is not a check of this link";
  }
  return job.link_checked
    ? "probed and not found closed -- a page that timed out or could not be read also stays"
    : "not checked yet";
}

function locationReceipt(job: JobCard, fallback: boolean): string {
  const parts: string[] = [];
  if (job.location) parts.push(job.location);
  if (job.remote === true) parts.push("remote");
  else if (job.remote === false) parts.push("not marked remote");
  const given = parts.length > 0 ? parts.join(", ") : "none given";
  // Under the fallback the model's reading was never made, so there is no reading to quote.
  if (job.score === null || job.score === undefined || fallback) return given;
  const reading = locationReading(job.score);
  return `${given}. ${reading.words}`;
}

// What opens under the trail line: the fields as returned. Rows with no value are left out by the
// receipt builder, and the scoring strategy is named only when it is the fallback.
export function rowReceipt(job: JobCard, scoringStrategy: string): ReceiptRowInput[] {
  const score = job.score;
  const fallback = isFallback(scoringStrategy);
  const dropped = score !== null && Array.isArray(score.inapplicable_dims) ? score.inapplicable_dims : [];
  const parts =
    score !== null && !fallback && typeof score.sub_scores === "object" && score.sub_scores !== null
      ? Object.entries(score.sub_scores as unknown as Record<string, unknown>)
          .filter(([key, value]) => !dropped.includes(key) && isScore(value))
          .map(([key, value]) => `${subScoreLabel(key)} ${value}`)
          .join(" · ")
      : null;
  return [
    { label: "provider", value: job.provider },
    { label: "source", value: tierWords(job.source_tier) },
    { label: "link check", value: linkCheckWords(job) },
    { label: "location", value: locationReceipt(job, fallback) },
    {
      label: "sponsorship",
      value: `${job.sponsorship_signal} (${SPONSORSHIP_TAG})`,
    },
    { label: "sub-scores", value: parts },
    {
      label: "not applied",
      value: score !== null && !fallback && dropped.length > 0 ? dropped.map((key) => subScoreLabel(key)).join(", ") : null,
    },
    {
      label: "lowest part",
      value: score !== null && !fallback && typeof score.bottleneck === "string" && score.bottleneck !== "" ? subScoreLabel(score.bottleneck) : null,
    },
    {
      label: "scoring",
      value: score !== null && fallback ? `${NEUTRAL_FALLBACK} -- the model's reply could not be read, so every bin is a placeholder` : null,
    },
  ];
}
