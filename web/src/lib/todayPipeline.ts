// The Pipeline card on Today: how many of the person's applications sit at each stage, counted from
// the rows of GET /applications that Today has already read (readAccount in firstRun.ts). Pure -- no
// React, no request, no clock.
//
// THE STAGES ARE THE BOARD'S. The order and the words are the ones the Applications board draws its
// columns from (ALL_STATUSES in applicationsTypes.ts, STATUS_LABELS in applicationsBoard.ts); this
// file holds no list of its own, so a stage added there appears here.
//
// NOTHING IS GUESSED. A row whose status is not one of those stages (an older row, a value this build
// does not know, a status that is missing or not text) is counted under "Other": it is never dropped,
// which would make the lines add up to less than the person has, and never filed under a stage it
// did not say. A stage that holds nothing has no line. While the read is out, or when it failed, there
// are no counts at all: a zero is only ever a number that was read.
//
// There is no "not touched in N days" line. The row's `updated_at` is set by the database whenever the
// row itself changes, which is a stage move (or an account merge); generating a resume, researching
// the company or finding contacts writes other tables and leaves it alone. It would call an
// application the person worked on yesterday untouched, so the line is left out.

import { STATUS_LABELS } from "./applicationsBoard";
import { ALL_STATUSES, type ApplicationStatus } from "./applicationsTypes";
import type { Fetched } from "./firstRun";

export const OTHER_STAGE_LABEL = "Other";

export interface PipelineLine {
  /** The stage, or "other" for every row whose status is not a stage the board has. */
  key: ApplicationStatus | "other";
  label: string;
  count: number;
}

export type PipelineView =
  | { state: "loading" }
  | { state: "failed" }
  /** `lines` holds only the stages that have applications, in the board's order, with Other last. */
  | { state: "ready"; total: number; lines: readonly PipelineLine[] };

function isStage(value: unknown): value is ApplicationStatus {
  return typeof value === "string" && (ALL_STATUSES as readonly string[]).includes(value);
}

export function pipelineView(applications: Fetched<readonly Record<string, unknown>[]>): PipelineView {
  if (applications.kind === "loading") return { state: "loading" };
  if (applications.kind === "failed") return { state: "failed" };
  const counts = new Map<ApplicationStatus, number>();
  let other = 0;
  for (const row of applications.value) {
    if (isStage(row.status)) counts.set(row.status, (counts.get(row.status) ?? 0) + 1);
    else other += 1;
  }
  const lines: PipelineLine[] = ALL_STATUSES.filter((status) => counts.has(status)).map((status) => ({
    key: status,
    label: STATUS_LABELS[status],
    count: counts.get(status) ?? 0,
  }));
  if (other > 0) lines.push({ key: "other", label: OTHER_STAGE_LABEL, count: other });
  return { state: "ready", total: applications.value.length, lines };
}
