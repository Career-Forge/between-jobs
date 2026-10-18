// A two-or-more-way switch between views of the same page (List / Board, Sections / Raw JSON). The
// chosen one is announced with aria-pressed, and that ARIA state is also what the stylesheet draws
// the selection from (.bj-view-toggle in app.css) -- there is no class to forget.

export interface ViewToggleProps<T extends string> {
  /** What the group is called to assistive technology. */
  label: string;
  options: readonly { value: T; label: string }[];
  value: T;
  onChange: (value: T) => void;
}

export function ViewToggle<T extends string>({ label, options, value, onChange }: ViewToggleProps<T>) {
  return (
    <div className="bj-view-toggle" role="group" aria-label={label}>
      {options.map((option) => (
        <button key={option.value} type="button" aria-pressed={option.value === value} onClick={() => onChange(option.value)}>
          {option.label}
        </button>
      ))}
    </div>
  );
}
