import { useEffect, useRef, useState } from "react";
import "../../styles/settings.css";

// "Remove key" asks once before it acts: removing a key stops Between Jobs using it, and the person
// may have pressed it by mistake. It says what removing does and does not do, then offers the real
// action and a way back. The first press moves focus to the answer; Keep it (or Escape) returns it.
// The confirming button is an ordinary outlined one: red is for failure, and removing a healthy key
// that the person chose to remove is not one.

export interface RemoveKeyProps {
  /** "OpenRouter key", "Brave Search key": what is being removed, in the confirmation's words. */
  what: string;
  /** Who still holds the key after we forget it. */
  holder: string;
  busy: boolean;
  onRemove: () => void;
}

export function RemoveKey({ what, holder, busy, onRemove }: RemoveKeyProps) {
  const [asking, setAsking] = useState(false);
  const opener = useRef<HTMLButtonElement>(null);
  const confirm = useRef<HTMLButtonElement>(null);

  const wasAsking = useRef(false);

  // Focus follows the question: to the answer when it opens, back to the button when it is declined.
  useEffect(() => {
    if (asking) confirm.current?.focus();
    else if (wasAsking.current) opener.current?.focus();
    wasAsking.current = asking;
  }, [asking]);

  if (!asking) {
    return (
      <button ref={opener} type="button" onClick={() => setAsking(true)}>
        Remove key
      </button>
    );
  }
  return (
    <div
      className="bj-set-confirm"
      role="group"
      aria-label={`Remove the ${what}?`}
      onKeyDown={(event) => {
        if (event.key === "Escape") setAsking(false);
      }}
    >
      <p>
        Remove the {what}? Between Jobs stops using it and deletes our copy. It stays valid at {holder} until you delete it there.
      </p>
      <div className="bj-set-actions">
        <button ref={confirm} type="button" aria-disabled={busy} onClick={() => !busy && onRemove()}>
          {busy ? "Removing..." : "Remove key"}
        </button>
        <button type="button" disabled={busy} onClick={() => setAsking(false)}>
          Keep it
        </button>
      </div>
    </div>
  );
}
