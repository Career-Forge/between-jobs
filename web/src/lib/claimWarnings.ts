// A claim warning read back into its parts. The two resume engines report the claims their check
// flagged as warnings written
//
//   unsupported claim (<what was done>): "<the claim>" -- <why> [<where>]
//
// with the parenthesis and the bracket optional (an older engine writes neither). What was done is
// "contradicted" or "unverifiable" for the separate engine, which only flags; the built-in engine has
// already acted, and says so ("removed before output", "replaced with your original wording",
// "removed"). A warning that does not read this way is not a claim row: the caller shows it as plain
// text, never as a quotation it cannot vouch for.
//
// The claim is the engine's own words and is never reworded here. Nothing is derived that the warning
// did not say: no verdict is invented for a repaired claim, which is drawn as unverifiable (nothing the
// check could see backs it) with what was done to it in the reason.

import type { ClaimStatus } from "../components/paper/ClaimRow";

export interface ParsedClaim {
  /** Contradicted when the engine said so; everything else it flagged is unverifiable. */
  status: Extract<ClaimStatus, "unverifiable" | "contradicted">;
  /** The claim as the engine quoted it, without the quotation marks. */
  claim: string;
  /** Why it was flagged, in the engine's words; empty when it gave none. */
  reason: string;
  /** What the engine did about it, in words, when it acted; null when it only flagged. */
  action: string | null;
  /** The part of the document it was in, when the warning named one in words; else null. */
  where: string | null;
}

const WARNING = /^unsupported claim(?:\s*\(([^)]*)\))?:\s*"([\s\S]*?)"\s*--\s*([\s\S]*)$/;
const LOCATION = /\s*\[([^\]]*)\]\s*$/;

const WHERE: Readonly<Record<string, string>> = {
  summary: "In the summary",
  "cover letter": "In the cover letter",
};

const ACTIONS: ReadonlyArray<readonly [RegExp, string]> = [
  [/^removed before output$/i, "Taken out before the document was written."],
  [/^replaced with your original wording$/i, "Replaced with your original wording before the document was written."],
  [/^removed$/i, "Taken out of the document."],
];

export function parseClaimWarning(warning: string): ParsedClaim | null {
  const match = WARNING.exec(warning.trim());
  if (match === null) return null;
  const disposition = (match[1] ?? "").trim();
  const claim = match[2].trim();
  if (claim === "") return null;
  let reason = match[3].trim();
  let where: string | null = null;
  const location = LOCATION.exec(reason);
  if (location !== null) {
    reason = reason.slice(0, location.index).trim();
    // A bracket that names a place in the document (a section) is a pointer for code, not for a person.
    where = WHERE[location[1].trim().toLowerCase()] ?? null;
  }
  const action = ACTIONS.find(([pattern]) => pattern.test(disposition))?.[1] ?? null;
  return {
    status: disposition.toLowerCase() === "contradicted" ? "contradicted" : "unverifiable",
    claim,
    reason,
    action,
    where,
  };
}

// The warnings of a generation that are about claims, in the order they came.
export function isClaimWarning(warning: string): boolean {
  return warning.startsWith("unsupported claim");
}

// "unverifiable" and "contradicted" counts, for the claim block's trail and the band's why line.
export function flaggedCount(warnings: readonly string[]): number {
  return warnings.filter(isClaimWarning).length;
}
