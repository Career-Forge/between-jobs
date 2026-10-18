import { connectFailureView } from "../../lib/settings/settingsErrors";
import type { ConnectState } from "../../lib/settings/openrouterOAuth";
import "../../styles/settings.css";

// What became of a return from OpenRouter, while the page is waiting on it or when it did not work.
// A connection that worked is not drawn here: the page moves on to choosing a model.
//
// Every failure says what happened in plain words and that nothing was connected, and offers the way
// forward: connect again, finish the exchange again when it only failed to complete, or paste a key
// instead (the form is right below).

export interface ConnectBannerProps {
  result: ConnectState<unknown>;
  onAgain: () => void;
  onRetry: () => void;
  onDismiss: () => void;
  busy: boolean;
}

export function ConnectBanner({ result, onAgain, onRetry, onDismiss, busy }: ConnectBannerProps) {
  if (result.phase === "exchanging") {
    return (
      <p className="bj-set-notice" role="status">
        Finishing the connection with OpenRouter...
      </p>
    );
  }
  if (result.phase !== "failed") return null;
  const view = connectFailureView(result.failure);
  return (
    <div className="bj-set-notice bj-set-notice--failure" role="alert">
      <p>
        <span className="bj-state-glyph" aria-hidden="true">
          {"✕"}
        </span>
        {view.message}
      </p>
      <div className="bj-set-actions">
        {view.next === "retry" ? (
          <button type="button" onClick={onRetry}>
            Finish connecting again
          </button>
        ) : (
          <button type="button" aria-disabled={busy} onClick={() => !busy && onAgain()}>
            Connect with OpenRouter again
          </button>
        )}
        <button type="button" className="bj-quiet" onClick={onDismiss}>
          Dismiss
        </button>
      </div>
    </div>
  );
}
