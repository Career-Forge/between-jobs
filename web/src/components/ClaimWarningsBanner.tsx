import { useState } from "react";
import { Link } from "react-router-dom";
import { copyToClipboard } from "../lib/clipboard";
import { parseClaimWarning } from "../lib/claimWarnings";
import { ClaimList, ClaimRow } from "./paper/ClaimRow";
import "../styles/workspace.css";

// The claim check: the claims the fact check flagged in a generated document, each as a row with what
// was found and what the person can do about it.
//
// Two engines write these warnings, and they mean different things. The separate engine only flags: the
// text stays in the document, so the person has to find it. The built-in engine has already acted: a
// flagged sentence was removed, or the candidate's own wording was put back, and the warning says which
// ("removed before output", "replaced with your original wording"). The closing line therefore follows
// the warnings, not the engine: telling someone that "nothing was edited" next to "(removed before
// output)" would send them hunting for a sentence that is not in the document.
//
// What the block always says, so the list is never read as a verdict:
//   - the legend: only flagged claims are listed, and one that was not flagged is not "supported";
//   - with nothing flagged: "No claims flagged in this run", never a count of grounded claims, and the
//     caveat that not flagged is not verified, because a check that did not run reads the same way
//     (the response does not say whether it ran);
//   - the footer: flags only, unless a line says it was removed or replaced.
// Each row's actions are the two things a person can do with a claim: add it to their profile if it is
// true (the profile is where evidence lives), or copy it.

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

export const CLAIM_CLOSING: Record<ClaimOutcome, string> = {
  "none-repaired":
    "Flags only. Nothing was auto-edited or blocked -- review these against your own background before you send anything.",
  "all-repaired":
    "Each flagged claim was taken out of the document, or swapped for your own wording, before it was written. The lines above show what was rejected. Still read it against your own background before you send anything.",
  mixed:
    "Where a line says it was removed or replaced, that text is no longer in the document; the rest is still there, so review it against your own background before you send anything.",
};

export const CLAIM_LEGEND = "◆ Unverifiable · ✕ Contradicted · claims that weren't flagged aren't listed";
export const ZERO_FLAG_CAVEAT =
  "Not flagged isn't verified: the check compares claims to the achievements picked for this draft, and a check that didn't run reads the same way.";

function CopyClaim({ claim }: { claim: string }) {
  const [state, setState] = useState<"idle" | "copied" | "failed">("idle");
  return (
    <>
      <button
        type="button"
        onClick={() => {
          void copyToClipboard(claim).then((ok) => setState(ok ? "copied" : "failed"));
        }}
      >
        Copy claim
      </button>
      <span className="bj-ws-status-line" role="status" aria-live="polite">
        {state === "copied" ? "Copied." : state === "failed" ? "Couldn't copy: select the claim and copy it yourself." : ""}
      </span>
    </>
  );
}

export function ClaimWarningsBanner({ warnings }: { warnings: readonly string[] }) {
  const parsed = warnings.map((warning) => ({ warning, claim: parseClaimWarning(warning) }));
  const rows = parsed.flatMap((entry) => (entry.claim === null ? [] : [{ warning: entry.warning, claim: entry.claim }]));
  const others = parsed.filter((entry) => entry.claim === null).map((entry) => entry.warning);
  const flagged = warnings.length;

  return (
    <div className="bj-ws-claims">
      <h3 className="bj-ws-block-title">Claim check</h3>
      <p className="bj-ws-legend">{CLAIM_LEGEND}</p>
      {flagged === 0 ? (
        <>
          <p className="bj-ws-zero">No claims flagged in this run</p>
          <p className="bj-ws-caption">{ZERO_FLAG_CAVEAT}</p>
        </>
      ) : (
        <>
          <p className="bj-ws-count">
            {flagged === 1 ? "1 claim flagged" : `${flagged} claims flagged`}
          </p>
          {rows.length > 0 && (
            <ClaimList label="Flagged claims">
              {rows.map(({ warning, claim }) => (
                <ClaimRow
                  key={warning}
                  status={claim.status}
                  claim={claim.claim}
                  reason={[claim.reason, claim.where, claim.action].filter((part): part is string => part !== null && part !== "").join(" ")}
                  actions={
                    <>
                      <Link to="/profile">If it's true, add it to your profile</Link>
                      <CopyClaim claim={claim.claim} />
                    </>
                  }
                />
              ))}
            </ClaimList>
          )}
          {others.length > 0 && (
            <ul className="bj-ws-plain-list bj-small" aria-label="Other claim notes">
              {others.map((warning) => (
                <li key={warning}>{warning}</li>
              ))}
            </ul>
          )}
          <p className="bj-ws-caption">{CLAIM_CLOSING[claimOutcome(warnings)]}</p>
        </>
      )}
    </div>
  );
}
