// "Between Jobs" set in the display serif with a small red dot after "Jobs": the lockup of the brand
// mark. The text is the accessible name; the dot is decoration (aria-hidden) and never a status, so
// it does not break the rule that red means failure -- tokens.css documents it as the one exception.
// Its size follows the font size of whatever wraps it (the rail, the public header, the sign-in card).

export function Wordmark({ className }: { className?: string }) {
  return (
    <span className={className === undefined ? "bj-wordmark" : `bj-wordmark ${className}`}>
      Between Jobs
      <span className="bj-wordmark-dot" aria-hidden="true" />
    </span>
  );
}
