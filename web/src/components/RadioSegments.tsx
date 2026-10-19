import { useRef, type KeyboardEvent } from "react";
import "../styles/profile-studio.css";

// A short choice (two to four words) drawn as a group of segments, the way the rest of the app draws a
// resume setting: a radio group, so the chosen segment carries aria-checked and the shared selection
// fill (.bj-selectable, tokens.css), inside the 1px outline that makes a maroon fill read as a group.
//
// It follows the ARIA radio pattern: one tab stop (the chosen segment, or the first when none is
// chosen), Arrow keys move to the next or previous segment and choose it, Home and End go to the ends,
// Space chooses the focused one. Arrow keys wrap, as a radio group's do.

export interface RadioSegmentsProps<T extends string> {
  /** The id of the element that names the group (a visible label). */
  labelledBy: string;
  value: T;
  options: readonly { value: T; label: string }[];
  onChange: (value: T) => void;
  disabled?: boolean;
}

export function RadioSegments<T extends string>({ labelledBy, value, options, onChange, disabled = false }: RadioSegmentsProps<T>) {
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  const chosen = options.findIndex((option) => option.value === value);
  const stop = chosen === -1 ? 0 : chosen;

  function choose(index: number) {
    const option = options[index];
    refs.current[index]?.focus();
    if (option !== undefined && option.value !== value) onChange(option.value);
  }

  function onKeyDown(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    if (disabled) return;
    const last = options.length - 1;
    let target: number | null = null;
    switch (event.key) {
      case "ArrowRight":
      case "ArrowDown":
        target = index === last ? 0 : index + 1;
        break;
      case "ArrowLeft":
      case "ArrowUp":
        target = index === 0 ? last : index - 1;
        break;
      case "Home":
        target = 0;
        break;
      case "End":
        target = last;
        break;
    }
    if (target === null) return;
    event.preventDefault();
    choose(target);
  }

  return (
    <div role="radiogroup" aria-labelledby={labelledBy} className="bj-segmented bj-radio-segments">
      {options.map((option, index) => (
        <button
          key={option.value}
          ref={(element) => {
            refs.current[index] = element;
          }}
          type="button"
          role="radio"
          aria-checked={option.value === value}
          tabIndex={index === stop ? 0 : -1}
          className="bj-selectable"
          disabled={disabled}
          onClick={() => choose(index)}
          onKeyDown={(event) => onKeyDown(event, index)}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}
