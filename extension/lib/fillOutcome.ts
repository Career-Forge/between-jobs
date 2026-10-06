import type { FillOutcomeLabel, FillResult } from "./types";

// What a finished fill reports to the service, and nothing more: how many fields it tried, how
// many it filled, and one word for how it went. Derived from the FillResult the side panel
// already receives, by arithmetic on its lengths and flags -- no label, value, selector, URL
// or other text from the page or the profile is read here, so none can be reported.
//
// A field counts as TRIED when the fill acted on it or left it empty on purpose and said so:
// each selector it wrote to, each field it listed as not filled (a one-word name's last name,
// a country no option matched, ...), and the résumé and cover letter when there was a file to
// attach or a reason one could not be. It counts as FILLED when something is now in it.
// A field the person had already filled (D5) is not tried, so a Refill that finds everything
// in place reports nothing -- there was no outcome.

export const MAX_REPORTED_FIELDS = 1000; // the service refuses more than this in one report

export interface FillCounts {
  fieldsAttempted: number;
  fieldsFilled: number;
  outcome: FillOutcomeLabel;
}

export function summarizeFill(result: FillResult): FillCounts | null {
  // The résumé and the cover letter count by what THIS fill did: a file it wrote, or a reason it
  // could not. A file that was already on the input (D5) is not tried, so it is neither.
  const resumeTried = result.resumeWritten || result.resumeError !== null;
  const coverLetterTried = result.coverLetterWritten || result.coverLetterError !== null;
  const filled = result.filledFields.length + (result.resumeWritten ? 1 : 0) + (result.coverLetterWritten ? 1 : 0);
  const attempted =
    result.filledFields.length + result.skippedFields.length + (resumeTried ? 1 : 0) + (coverLetterTried ? 1 : 0);

  const fieldsAttempted = Math.min(attempted, MAX_REPORTED_FIELDS);
  const fieldsFilled = Math.min(filled, fieldsAttempted);

  if (result.fillError !== null) {
    // The run stopped partway: what was written before that is real, but the run is not "ok".
    return { fieldsAttempted, fieldsFilled, outcome: fieldsFilled > 0 ? "partial" : "failed" };
  }
  if (fieldsAttempted === 0) return null; // nothing was tried, so there is no outcome to report
  if (fieldsFilled === 0) return { fieldsAttempted, fieldsFilled, outcome: "failed" };
  // A signed map that could not be used means part of the form was never even tried.
  const complete = fieldsFilled === fieldsAttempted && result.fieldMapError === null;
  return { fieldsAttempted, fieldsFilled, outcome: complete ? "ok" : "partial" };
}
