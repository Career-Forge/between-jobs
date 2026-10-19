import { useEffect, useId, useState } from "react";

// A masked field for a secret, with a Show / Hide button. The value is held by the parent so it can
// clear the field the moment a key is saved: a key that was stored is never left in the page.
//
// It is built so a password manager does not offer to save or fill it (an API key is not a login),
// the browser does not spell-check or capitalise it, and nothing about it is stored by the page.

export interface KeyFieldProps {
  label: string;
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  hint?: string;
  /** The field cannot be edited (a save is out). Read-only, never disabled: pressing Enter in the field is what starts the save, and a disabled field drops keyboard focus to the top of the page. */
  readOnly?: boolean;
  /** Names the element a screen reader should read after the label (the hint, the error). */
  describedBy?: string;
  inputRef?: (element: HTMLInputElement | null) => void;
}

export function KeyField({ label, value, onChange, placeholder, hint, readOnly, describedBy, inputRef }: KeyFieldProps) {
  const [shown, setShown] = useState(false);
  const inputId = useId();
  const hintId = useId();
  // Once the field is empty (the key was saved, or cleared) it goes back to masked: the next key
  // someone types is not shown just because the last one was.
  useEffect(() => {
    if (value === "") setShown(false);
  }, [value]);
  const described = [hint === undefined ? null : hintId, describedBy ?? null].filter((id): id is string => id !== null).join(" ");
  return (
    <div className="bj-set-field">
      <label htmlFor={inputId}>{label}</label>
      <div className="bj-set-keyline">
        <input
          id={inputId}
          ref={inputRef}
          type={shown ? "text" : "password"}
          className="bj-set-mono-input"
          value={value}
          placeholder={placeholder}
          readOnly={readOnly}
          autoComplete="off"
          autoCapitalize="none"
          autoCorrect="off"
          spellCheck={false}
          data-1p-ignore=""
          data-lpignore="true"
          data-form-type="other"
          aria-describedby={described === "" ? undefined : described}
          onChange={(event) => onChange(event.target.value)}
        />
        <button type="button" className="bj-set-reveal" aria-label={shown ? "Hide key" : "Show key"} onClick={() => setShown((current) => !current)}>
          {shown ? "Hide" : "Show"}
        </button>
      </div>
      {hint !== undefined && (
        <p id={hintId} className="bj-set-hint">
          {hint}
        </p>
      )}
    </div>
  );
}
