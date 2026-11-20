import { useEffect, useRef, type ReactNode, type RefObject } from "react";
import { Link } from "react-router-dom";
import { formatSalary, locationLabel } from "../../lib/discover";
import type { JobCard, ScoreBin } from "../../lib/discoverTypes";
import { hasSponsorshipSignal, sponsorshipTag, sponsorshipWords, type FitView } from "../../lib/discoverRow";
import "../../styles/discover.css";

// The parts every drawing of one search result shares: the full row of a narrow screen (ResultRow.tsx),
// the compact row of a wide one (CompactRow.tsx) and the pane that shows the chosen row in full
// (DetailPane.tsx). They are here once so that the three cannot say different things about the same
// posting: which class a bin gets, which address may become a link, what the posting's text and the
// sponsorship line are, and what Track does.
//
// An address that came from a search provider or a scraped page is untrusted text: only an http(s)
// address becomes a link, and any other is left out, never repaired. The posting's own words are drawn
// as text only.

// The classes are named for the engine's bins, not for the grades a person is shown, and come from this
// fixed table, never from the response's own text.
export const BIN_CLASS: Readonly<Record<ScoreBin, string>> = {
  Strong: "bj-disc-row--strong",
  Good: "bj-disc-row--good",
  Mixed: "bj-disc-row--mixed",
  Poor: "bj-disc-row--poor",
};

/** The class of a row for what its fit has to say: its kind, or the bin of a scored one. */
export function rowKindClass(fit: FitView): string {
  return fit.kind === "placeholder" ? "bj-disc-row--placeholder" : fit.kind === "unscored" ? "bj-disc-row--unscored" : BIN_CLASS[fit.level];
}

export function safeHref(url: string): string | null {
  return /^https?:\/\//i.test(url) ? url : null;
}

/** The name the row's controls use for it: "Data Engineer at Acme". */
export function whatOf(job: JobCard): string {
  return `${job.title} at ${job.company ?? "Unknown company"}`;
}

/** Company and place, then pay when it was listed: the lines under a title. */
export function IdentityMeta({ job }: { job: JobCard }) {
  const salary = formatSalary(job.salary_min, job.salary_max, job.salary_currency);
  return (
    <>
      <p className="bj-disc-meta">
        {job.company ?? "Unknown company"} {"--"} {locationLabel(job)}
      </p>
      {salary !== null && <p className="bj-disc-meta">{salary}</p>}
    </>
  );
}

// The posting text and the sponsorship line share one block. On a row past the scoring batch the
// sponsorship line leads. Null when the posting came with neither.
export function postingBody(job: JobCard, kind: FitView["kind"]): ReactNode | null {
  const snippet = job.snippet !== "" ? <p className="bj-disc-snippet">{job.snippet}</p> : null;
  const signal = hasSponsorshipSignal(job.sponsorship_signal) ? (
    <p className="bj-disc-signal">
      {sponsorshipWords(job.sponsorship_signal, job.provider)}{" "}
      <span className="bj-disc-signal-tag">
        <span className="bj-trail-glyph bj-trail-glyph--muted" aria-hidden="true">
          {"○"}
        </span>{" "}
        {sponsorshipTag(job.sponsorship_signal, job.provider)}
      </span>
    </p>
  ) : null;
  if (snippet === null && signal === null) return null;
  return kind === "unscored" ? (
    <>
      {signal}
      {snippet}
    </>
  ) : (
    <>
      {snippet}
      {signal}
    </>
  );
}

export interface TrackPress {
  /** Asks for the posting to be tracked, unless a request for it is already out. */
  track: () => void;
  /** Goes on the "Open workspace" link that takes Track's place. */
  workspaceLink: RefObject<HTMLAnchorElement | null>;
}

// Track is replaced by "Open workspace" when it succeeds. The button a person pressed is then gone
// and focus would fall to the page, so focus follows to the link that took its place. Only the place
// whose Track was pressed does this (a row, or the pane, each with its own), and only when focus really
// was lost (it is on the page itself), never when the person has moved on to something else.
export function useTrackPress(job: JobCard, tracking: boolean, trackedApplicationId: string | null, onTrack: (job: JobCard) => void): TrackPress {
  const trackPressed = useRef(false);
  const workspaceLink = useRef<HTMLAnchorElement>(null);
  const track = () => {
    if (tracking) return;
    trackPressed.current = true;
    onTrack(job);
  };
  useEffect(() => {
    if (trackedApplicationId === null || !trackPressed.current) return;
    trackPressed.current = false;
    const active = document.activeElement;
    if (active === null || active === document.body) workspaceLink.current?.focus();
  }, [trackedApplicationId]);
  return { track, workspaceLink };
}

export interface RowActionsProps {
  job: JobCard;
  trackedApplicationId: string | null;
  tracking: boolean;
  press: TrackPress;
}

/** View posting (only for an http(s) address), then Track, or Open workspace once it is tracked. */
export function RowActions({ job, trackedApplicationId, tracking, press }: RowActionsProps) {
  const href = safeHref(job.apply_url);
  const what = whatOf(job);
  return (
    <div className="bj-disc-actions">
      {href !== null && (
        <a href={href} target="_blank" rel="noopener noreferrer" aria-label={`View posting for ${what} (opens in a new tab)`}>
          View posting
        </a>
      )}
      {trackedApplicationId !== null ? (
        <Link ref={press.workspaceLink} to={`/applications/${trackedApplicationId}`} aria-label={`Open workspace for ${what}`}>
          Open workspace
        </Link>
      ) : (
        <button type="button" aria-disabled={tracking ? true : undefined} aria-label={`${tracking ? "Tracking..." : "Track"} ${what}`} onClick={press.track}>
          {tracking ? "Tracking..." : "Track"}
        </button>
      )}
    </div>
  );
}
