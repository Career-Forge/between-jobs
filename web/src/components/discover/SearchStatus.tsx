import { parseWarning, providerName } from "../../lib/discover";
import type { SearchNotes } from "../../lib/discoverFacts";
import { StaticTrailLine } from "../paper/TrailLine";
import "../../styles/discover.css";

// What the search said about itself, apart from the rows: which providers failed and why, how many
// listings the link check dropped, and how many rows each source returned. One quiet block above the
// toolbar, so a short list is never a silent one and the next-step band does not have to print it.
//
//   failures   one line per warning, "✕ Brave Search couldn't complete that search · its message"
//   dropped    "3 dropped -- no longer live", when the link check dropped listings
//   sources    "Sources: registry 6 · serper 5", the provider ids as the server sent them. A count of
//              the rows each returned, nothing about how good they are
//
// It describes the search as it came back, never the filtered view of it. Each part is left out when it
// has nothing to say, and with nothing at all the block is not drawn.

// "Brave Search couldn't complete that search · request failed (429)": a provider's failure says
// which provider and carries its own message after it; a note that names no provider is shown whole.
export function WarningLines({ warnings }: { warnings: readonly string[] }) {
  if (warnings.length === 0) return null;
  return (
    <ul className="bj-disc-warnings" aria-label="What this search could not do">
      {warnings.map((warning, index) => {
        const line = parseWarning(warning);
        return (
          <li key={`${index}-${warning}`}>
            <StaticTrailLine
              trail={
                line.provider === null
                  ? { glyph: "failed", actor: line.message }
                  : { glyph: "failed", actor: `${providerName(line.provider)} couldn't complete that search`, basis: line.message }
              }
            />
          </li>
        );
      })}
    </ul>
  );
}

export interface SearchStatusProps {
  notes: SearchNotes;
  /** Draw the sources line: false when the search returned no rows to count. */
  showSources: boolean;
}

export function SearchStatus({ notes, showSources }: SearchStatusProps) {
  const hasWarnings = notes.warnings.length > 0;
  const hasDropped = notes.dropped > 0;
  const hasSources = showSources && notes.sources.length > 0;
  if (!hasWarnings && !hasDropped && !hasSources) return null;
  return (
    <div className="bj-disc-status" role="group" aria-label="About this search">
      <WarningLines warnings={notes.warnings} />
      {hasDropped && (
        <p className="bj-disc-note">
          {notes.dropped} dropped {"--"} no longer live
        </p>
      )}
      {hasSources && <p className="bj-disc-sources">Sources: {notes.sources.map((entry) => `${entry.provider} ${entry.count}`).join(" · ")}</p>}
    </div>
  );
}
