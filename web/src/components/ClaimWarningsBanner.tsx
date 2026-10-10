// The banner under a generated document that lists the claims the fact check flagged.
//
// Two engines write these warnings, and they mean different things. The separate engine only
// flags: the text stays in the document, so the person has to find it. The built-in engine has
// already acted: a flagged sentence was removed, or the candidate's own wording was put back,
// and the warning says which ("removed before output", "replaced with your original wording").
// The closing line therefore follows the warnings, not the engine: telling someone that
// "nothing was edited" next to "(removed before output)" would send them hunting for a
// sentence that is not in the document.

// A warning the engine says it has already acted on: its disposition is removed or replaced.
export function isRepairedClaim(warning: string): boolean {
  return /^unsupported claim \((?:removed|replaced)/.test(warning);
}

export type ClaimOutcome = "all-repaired" | "none-repaired" | "mixed";

export function claimOutcome(warnings: readonly string[]): ClaimOutcome {
  const repaired = warnings.filter(isRepairedClaim).length;
  if (repaired === 0) return "none-repaired";
  return repaired === warnings.length ? "all-repaired" : "mixed";
}

const CLOSING: Record<ClaimOutcome, string> = {
  "none-repaired":
    "Nothing was auto-edited or blocked -- review these against your own background before you send anything.",
  "all-repaired":
    "Each flagged claim was taken out of the document, or swapped for your own wording, before it was written. The lines above show what was rejected. Still read it against your own background before you send anything.",
  mixed:
    "Where a line says it was removed or replaced, that text is no longer in the document; the rest is still there, so review it against your own background before you send anything.",
};

export function ClaimWarningsBanner({ warnings }: { warnings: readonly string[] }) {
  return (
    <div className="bj-claim-warning-banner">
      <div>⚠️ Claim verification flagged {warnings.length} claim(s):</div>
      <ul className="bj-small">
        {warnings.map((w) => (
          <li key={w}>{w}</li>
        ))}
      </ul>
      <div className="bj-small">{CLOSING[claimOutcome(warnings)]}</div>
    </div>
  );
}
