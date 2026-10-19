// The trail lines and receipts of the workspace's blocks, built only from fields the API returned.
// A slot whose field did not come back is left out with its separator (lib/trail.ts is the grammar),
// and nothing is written that the page cannot know:
//
//  - a draft names no model and no time: neither is in the response. It says "on your model key"
//    because every generation runs on the person's own key, whichever provider they chose;
//  - a restored result says it was restored, with no date -- it was stored, and no timestamp came back;
//  - the claim check says what it compares, never that it ran: a check that did not run reads the same
//    as one that found nothing;
//  - the ATS score is code over signals a model extracted ("engine ✦"), and the export checks are plain
//    code, no model.

import type { ReceiptRowInput, TrailInput } from "./trail";
import { formatTrailDate } from "./trail";
import type { ForgeFitResult, PrepareApplicationResult } from "./generateTypes";
import { STATUS_LABELS } from "./applicationsBoard";
import { sourceWords } from "./applicationsView";
import type { ApplicationStatus } from "./applicationsTypes";
import type { WorkspaceApplication } from "./workspaceTypes";

// ── the page's own trail ──────────────────────────────────────────────────

// "● You tracked this · from Discover · Sep 22": the whole page's trail, under the job's identity. How
// the application got here is worded by sourceWords (lib/applicationsView.ts), the same as on its card
// in the Applications list; a channel it does not name is left out rather than guessed.
export function headerTrail(application: Pick<WorkspaceApplication, "source_channel" | "created_at">, now: Date): TrailInput {
  const channel = application.source_channel;
  return {
    glyph: "you",
    actor: "You tracked this",
    basis: sourceWords(channel),
    when: formatTrailDate(application.created_at, { now }),
  };
}

// ── a generation ──────────────────────────────────────────────────────────

const RUN_PREFIX = 8;
const HASH_PREFIX = 12;

function prefix(value: string | undefined | null, length: number): string | null {
  return typeof value === "string" && value.trim() !== "" ? value.trim().slice(0, length) : null;
}

export interface BlockTrailParts {
  trail: TrailInput;
  receipt: readonly ReceiptRowInput[];
  receiptTitle: string;
}

// The attempts' scores, first to final ("64 -> 72"), or the one score.
function atsWords(result: PrepareApplicationResult): string | null {
  const scores = result.ats_attempts.map((attempt) => attempt.overall_score).filter((score) => Number.isFinite(score));
  if (scores.length === 0) return null;
  const first = scores[0];
  const last = scores[scores.length - 1];
  return scores.length > 1 && first !== last ? `${first} -> ${last}` : String(last);
}

export function generationTrail(result: PrepareApplicationResult, options: { restored: boolean; flagged: number; earlierResume: boolean }): BlockTrailParts {
  const drafted = result.resume !== null;
  const gate = [result.gate_outcome ?? null, ...(result.gate_cautions ?? [])].filter((part): part is string => typeof part === "string" && part !== "");
  const receipt: ReceiptRowInput[] = [
    { label: "run", value: prefix(result.run_id, RUN_PREFIX) },
    { label: "pdf", value: prefix(result.resume?.sha256, HASH_PREFIX) },
    { label: "ATS", value: atsWords(result) },
    { label: "gate", value: gate.length === 0 ? null : gate.join(" · ") },
    { note: options.restored ? "Restored from your last stored generation. No time came back with it." : null },
    { note: options.earlierResume ? "From an earlier successful generation." : null },
  ];
  if (drafted) {
    return {
      trail: {
        glyph: "model",
        actor: "Drafted by your model",
        basis: "on your model key",
        extra: options.flagged > 0 ? `${options.flagged} flagged` : null,
      },
      receipt,
      receiptTitle: "Where this draft came from",
    };
  }
  return {
    trail: { glyph: "evidence", actor: "Held back by the fit check", basis: "no resume was generated" },
    receipt,
    receiptTitle: "Where this reading came from",
  };
}

// The ATS score: code over the signals a model extracted.
export function scoreTrail(result: PrepareApplicationResult): BlockTrailParts {
  const attempts = result.ats_attempts;
  const final = attempts[attempts.length - 1];
  return {
    trail: { glyph: "engine", actor: "code-weighted from your model's sub-scores" },
    receipt: [
      { label: "attempts", value: attempts.length === 0 ? null : attempts.length },
      { label: "ATS", value: atsWords(result) },
      { label: "confidence", value: final?.confidence ?? null },
      { label: "rating", value: final?.rating ?? null },
      { label: "scale", value: result.score_scale },
    ],
    receiptTitle: "Where this score came from",
  };
}

export function claimTrail(flagged: number): BlockTrailParts {
  return {
    trail: {
      glyph: "evidence",
      actor: "Claim check",
      basis: "claims compared to the achievements picked for this draft",
      extra: flagged > 0 ? `${flagged} flagged` : null,
    },
    receipt: [
      { note: "Flags only: nothing is edited or blocked unless a line says it was removed or replaced." },
      { note: "A check that did not run reads the same as one that found nothing." },
    ],
    receiptTitle: "What the claim check is",
  };
}

export function checklistTrail(): BlockTrailParts {
  return {
    trail: { glyph: "code", actor: "Checked by code", basis: "no model involved" },
    receipt: [{ note: "These checks recompile the resume's LaTeX each time they run." }],
    receiptTitle: "What the export checks are",
  };
}

export function tailorTrail(counts: { clusters: number; skills: number; profileVersionId: string | null }): BlockTrailParts {
  return {
    trail: { glyph: "engine", actor: "clusters extracted by a model", basis: "matched to your profile by code" },
    receipt: [
      { label: "clusters", value: counts.clusters },
      { label: "skills", value: counts.skills },
      { label: "profile", value: prefix(counts.profileVersionId, RUN_PREFIX) },
      { note: "Tailor uses the profile version this resume was created against." },
    ],
    receiptTitle: "Where this match came from",
  };
}

// ── the compact current state (960-1279px) ────────────────────────────────

export interface StateSummaryParts {
  status: string;
  hasResume: boolean;
  /** "drafted", "held back" or null when nothing is known about the latest attempt. */
  latest: "drafted" | "held_back" | null;
  atsScore: number | null;
  flagged: number | null;
}

// One line of what the margin would hold, from the same facts the band reads.
export function stateSummary(parts: StateSummaryParts): string {
  const label = STATUS_LABELS[parts.status as ApplicationStatus] ?? null;
  const words: string[] = [label === null ? "Status not shown" : `Status ${label.toLowerCase()}`];
  if (parts.latest === "held_back") words.push(parts.hasResume ? "latest attempt held back, earlier resume kept" : "latest attempt held back, no resume");
  else if (parts.hasResume) words.push("resume drafted");
  else words.push("no resume yet");
  if (parts.atsScore !== null) words.push(`ATS ${parts.atsScore} / 100`);
  if (parts.flagged !== null && parts.latest === "drafted") words.push(parts.flagged === 1 ? "1 claim flagged" : `${parts.flagged} claims flagged`);
  return words.join(" · ");
}

export function fitCeiling(fit: ForgeFitResult | null | undefined): number | null {
  return fit !== null && fit !== undefined && Number.isFinite(fit.overall_score) ? fit.overall_score : null;
}
