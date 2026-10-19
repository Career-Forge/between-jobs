import type { ReactNode } from "react";
import { TRAIL_GLYPHS, type TrailGlyphName } from "../../lib/trail";
import "../../styles/paper-doc.css";

// One claim from a document and what is known about it: a status chip, the claim in the words it was
// written in, the reason, and the actions the person can take.
//
//   <ClaimRow status="unverifiable"
//             claim="Reduced query costs by 40%"
//             reason="None of the achievements picked for this draft mention query costs."
//             actions={<><Link to="/profile">If it's true, add it to your profile</Link>
//                        <button type="button">Copy claim</button></>} />
//
// The four statuses each have a word AND a mark AND an outline, so color is never the only thing
// telling them apart (the two gold chips share a mark and differ in the word and the outline's style):
//
//   supported      ◆ Supported     solid gold outline. The claim matches something the page can name;
//                                  the reason says what. It is not "verified" and is never green.
//   unverifiable   ◆ Unverifiable  dashed gold outline. Flagged: nothing the check could see supports it.
//   contradicted   ✕ Contradicted  red outline. Flagged: something the check could see says otherwise.
//   unchecked      ○ Not checked   muted outline. Nobody has looked; that is not a pass.
//
// The claim check lists only what it flagged (unverifiable and contradicted); a claim it did not flag
// is not listed and must not be drawn as `supported`, because not flagged is not the same as backed.
// `supported` is for a list where something does back each claim. The claim is shown in curly quotes
// in the italic serif: it is quoted source text, never reworded here. On a phone the chip stacks above
// the claim.
//
// The honest copy around a list is the page's to write, not this file's: the claim check's legend
// ("claims that weren't flagged aren't listed"), its "Flags only -- nothing was auto-edited or blocked"
// footer, and the zero-flag line with "Not flagged isn't verified". See the web README.

export type ClaimStatus = "supported" | "unverifiable" | "contradicted" | "unchecked";

interface ChipSpec {
  glyph: TrailGlyphName;
  word: string;
}

export const CLAIM_CHIPS: Readonly<Record<ClaimStatus, ChipSpec>> = {
  supported: { glyph: "evidence", word: "Supported" },
  unverifiable: { glyph: "evidence", word: "Unverifiable" },
  contradicted: { glyph: "failed", word: "Contradicted" },
  unchecked: { glyph: "code", word: "Not checked" },
};

export interface ClaimRowProps {
  status: ClaimStatus;
  /** The claim, verbatim and without quotation marks: they are drawn here. */
  claim: string;
  /** Why it has this status, in plain words. */
  reason?: string;
  /** Secondary actions: links and outlined buttons. */
  actions?: ReactNode;
  /** An <li> inside a ClaimList (the default) or a plain <div>. */
  as?: "li" | "div";
  className?: string;
}

export function ClaimRow({ status, claim, reason, actions, as: Tag = "li", className }: ClaimRowProps) {
  const chip = CLAIM_CHIPS[status];
  const glyph = TRAIL_GLYPHS[chip.glyph];
  const classes = ["bj-claimrow", `bj-claimrow--${status}`];
  if (className !== undefined) classes.push(className);
  return (
    <Tag className={classes.join(" ")}>
      <span className={`bj-claimrow-chip bj-claimrow-chip--${status}`}>
        <span className={`bj-trail-glyph bj-trail-glyph--${glyph.tone}`} aria-hidden="true">
          {glyph.char}
        </span>{" "}
        {chip.word}
      </span>
      <div className="bj-claimrow-body">
        <p className="bj-claimrow-claim">
          {"“"}
          {claim}
          {"”"}
        </p>
        {reason !== undefined && reason !== "" && <p className="bj-claimrow-reason">{reason}</p>}
        {actions !== undefined && actions !== null && <div className="bj-claimrow-actions">{actions}</div>}
      </div>
    </Tag>
  );
}

// The list the rows sit in, named for assistive tech.
export function ClaimList({ label, children, className }: { label: string; children: ReactNode; className?: string }) {
  return (
    <ul aria-label={label} className={className === undefined ? "bj-claimlist" : `bj-claimlist ${className}`}>
      {children}
    </ul>
  );
}
