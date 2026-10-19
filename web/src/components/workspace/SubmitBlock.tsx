import { FailureState } from "../paper/StateViews";
import "../../styles/workspace.css";

// The last step is the person's own. Between Jobs drafts and prepares; it never submits an application,
// so this block says where it happens (the employer's own site) and offers a way to record it here
// afterwards through the same stage endpoint the status select uses. It shows while the status is Saved
// and a resume exists, and nothing in it sends anything.

export interface SubmitBlockProps {
  /** Whether the block is on screen at all (status Saved and a resume exists). */
  visible: boolean;
  /** The posting's web address, or null when the application has none. */
  postingUrl: string | null;
  busy: boolean;
  /** A failed attempt to record it, shown with a Retry. */
  problem: string | null;
  onMarkApplied: () => void;
}

export function SubmitBlock({ visible, postingUrl, busy, problem, onMarkApplied }: SubmitBlockProps) {
  if (!visible) return null;
  return (
    <div className="bj-ws-submit">
      <h3 className="bj-ws-block-title">You submit</h3>
      <p className="bj-small">
        Between Jobs never submits an application for you. Open the posting, apply on the employer's site yourself, then record it here.
      </p>
      <div className="bj-actions">
        {postingUrl !== null && (
          <a className="bj-button-link" href={postingUrl} target="_blank" rel="noopener noreferrer">
            Open the posting
            <span className="bj-visually-hidden"> (opens in a new tab)</span>
          </a>
        )}
        <button type="button" aria-disabled={busy ? true : undefined} onClick={() => !busy && onMarkApplied()}>
          {busy ? "Saving..." : "I submitted this -- mark as applied"}
        </button>
      </div>
      {problem !== null && <FailureState message={problem} onRetry={busy ? undefined : onMarkApplied} />}
    </div>
  );
}
