import { useEffect, useId, useRef } from "react";
import { Link } from "react-router-dom";
import { formatSalary, locationLabel } from "../../lib/discover";
import { tierOf } from "../../lib/discoverArrange";
import type { JobCard, ScoreBin } from "../../lib/discoverTypes";
import { fitViewOf, hasSponsorshipSignal, rowReceipt, rowTrail, sponsorshipTag, sponsorshipWords } from "../../lib/discoverRow";
import { FailureState } from "../paper/StateViews";
import { TrailLine } from "../paper/TrailLine";
import { FitDetail, FitReading, FitScore, FitToggle } from "./FitBlock";
import "../../styles/discover.css";

// One search result as a list row, in the order a person reads it: who and where (title, company and
// place, pay), then the fit score in a gutter on the left, what the posting says, the model's one line
// and what the score left out, the row's actions, and its trail as the last line (inline at every
// width, as a list-like surface's trails are).
//
// A row is one of three kinds, by what it has to say:
//
//   scored        the gutter, the posting text, the model's line, "Why this score" and its detail
//   placeholder   the scoring model's reply could not be read: a hollow ladder that says "Placeholder",
//                 the posting text and the actions, with no number and nothing to open
//   unscored      past the scoring batch: one band -- identity and the words "Not scored" with the
//                 actions -- with no gutter and no detail
//
// The Strong rows are lifted onto a slightly lighter surface; the other tiers carry only a class so a
// later rule can find them. A tier class comes from a fixed table, never from the response's own text.
//
// An address that came from a search provider or a scraped page is untrusted text: only an http(s)
// address becomes a link, and any other is left out of the row, never repaired.

const TIER_CLASS: Readonly<Record<ScoreBin, string>> = {
  Strong: "bj-disc-row--strong",
  Good: "bj-disc-row--good",
  Mixed: "bj-disc-row--mixed",
  Poor: "bj-disc-row--poor",
};

export interface ResultRowProps {
  job: JobCard;
  /** The scoring strategy of the whole response: only "neutral_fallback" changes what a row says. */
  scoringStrategy: string;
  now: Date;
  /** The application a Track in this visit created, when it did. */
  trackedApplicationId: string | null;
  tracking: boolean;
  /** A failed Track, shown on this row with its own Retry. */
  trackError: string | null;
  onTrack: (job: JobCard) => void;
  timeZone?: string;
  /** "Why this score" is open on this row. Closed by default. */
  detailsOpen?: boolean;
  onToggleDetails?: () => void;
}

function safeHref(url: string): string | null {
  return /^https?:\/\//i.test(url) ? url : null;
}

export function ResultRow({ job, scoringStrategy, now, trackedApplicationId, tracking, trackError, onTrack, timeZone, detailsOpen = false, onToggleDetails }: ResultRowProps) {
  // Track is replaced by "Open workspace" when it succeeds. The button a person pressed is then gone
  // and focus would fall to the page, so focus follows to the link that took its place. Only a row
  // whose Track was pressed does this, and only when focus really was lost (it is on the page itself),
  // never when the person has moved on to something else.
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
  const salary = formatSalary(job.salary_min, job.salary_max, job.salary_currency);
  const fit = fitViewOf(job, scoringStrategy);
  const href = safeHref(job.apply_url);
  const company = job.company ?? "Unknown company";
  const what = `${job.title} at ${company}`;
  const detailId = useId();
  const open = fit.kind === "scored" && detailsOpen;
  const tier = fit.kind === "scored" ? tierOf(job) : null;
  const rowClass = [
    "bj-disc-row",
    fit.kind === "placeholder" ? "bj-disc-row--placeholder" : fit.kind === "unscored" ? "bj-disc-row--unscored" : tier === null ? null : TIER_CLASS[tier],
    open ? "bj-disc-row--open" : null,
  ]
    .filter((name) => name !== null)
    .join(" ");
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
  // The posting text and the sponsorship line share one area. On a row past the scoring batch the
  // sponsorship line leads, and the text under it is a single muted line.
  const body =
    fit.kind === "unscored" ? (
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
  return (
    <li className={rowClass}>
      <div className="bj-disc-main">
        <h3 className="bj-disc-title">{job.title}</h3>
        <p className="bj-disc-meta">
          {company} {"--"} {locationLabel(job)}
        </p>
        {salary !== null && <p className="bj-disc-meta">{salary}</p>}
      </div>
      <FitScore view={fit} />
      {(snippet !== null || signal !== null) && <div className="bj-disc-body">{body}</div>}
      <FitReading view={fit} />
      <div className="bj-disc-actions">
        {href !== null && (
          <a href={href} target="_blank" rel="noopener noreferrer" aria-label={`View posting for ${what} (opens in a new tab)`}>
            View posting
          </a>
        )}
        {trackedApplicationId !== null ? (
          <Link ref={workspaceLink} to={`/applications/${trackedApplicationId}`} aria-label={`Open workspace for ${what}`}>
            Open workspace
          </Link>
        ) : (
          <button type="button" aria-disabled={tracking ? true : undefined} aria-label={`${tracking ? "Tracking..." : "Track"} ${what}`} onClick={track}>
            {tracking ? "Tracking..." : "Track"}
          </button>
        )}
      </div>
      {fit.kind === "scored" && <FitToggle open={open} detailId={detailId} label={`Why this score for ${what}`} onToggle={() => onToggleDetails?.()} />}
      {fit.kind === "scored" && open && <FitDetail view={fit} id={detailId} />}
      {trackError !== null && (
        <div className="bj-disc-problem">
          <FailureState message={`Couldn't track ${what}: ${trackError}`} onRetry={track} />
        </div>
      )}
      <div className="bj-disc-trail">
        <TrailLine trail={rowTrail(job, now, timeZone)} receipt={rowReceipt(job, scoringStrategy)} receiptTitle={`Where this result came from: ${job.title}`} />
      </div>
    </li>
  );
}
