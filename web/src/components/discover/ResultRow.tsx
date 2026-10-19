import { Link } from "react-router-dom";
import { formatSalary, locationLabel } from "../../lib/discover";
import type { JobCard } from "../../lib/discoverTypes";
import { SPONSORSHIP_TAG, fitViewOf, hasSponsorshipSignal, rowReceipt, rowTrail, sponsorshipWords } from "../../lib/discoverRow";
import { FailureState } from "../paper/StateViews";
import { TrailLine } from "../paper/TrailLine";
import { FitBlock } from "./FitBlock";
import "../../styles/discover.css";

// One search result as a list row: who and where, what the posting says, the fit block, the row's
// actions, and its trail as the last line (inline at every width, as a list-like surface's trails are).
//
// An address that came from a search provider or a scraped page is untrusted text: only an http(s)
// address becomes a link, and any other is left out of the row, never repaired.

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
}

function safeHref(url: string): string | null {
  return /^https?:\/\//i.test(url) ? url : null;
}

export function ResultRow({ job, scoringStrategy, now, trackedApplicationId, tracking, trackError, onTrack, timeZone }: ResultRowProps) {
  const salary = formatSalary(job.salary_min, job.salary_max, job.salary_currency);
  const fit = fitViewOf(job, scoringStrategy);
  const href = safeHref(job.apply_url);
  const company = job.company ?? "Unknown company";
  const what = `${job.title} at ${company}`;
  return (
    <li className="bj-disc-row">
      <div className="bj-disc-main">
        <h3 className="bj-disc-title">{job.title}</h3>
        <p className="bj-disc-meta">
          {company} {"--"} {locationLabel(job)}
        </p>
        {salary !== null && <p className="bj-disc-meta">{salary}</p>}
        {job.snippet !== "" && <p className="bj-disc-snippet">{job.snippet}</p>}
        {hasSponsorshipSignal(job.sponsorship_signal) && (
          <p className="bj-disc-signal">
            {sponsorshipWords(job.sponsorship_signal)}{" "}
            <span className="bj-disc-signal-tag">
              <span className="bj-trail-glyph bj-trail-glyph--muted" aria-hidden="true">
                {"○"}
              </span>{" "}
              {SPONSORSHIP_TAG}
            </span>
          </p>
        )}
      </div>
      <FitBlock view={fit} />
      <div className="bj-disc-actions">
        {href !== null && (
          <a href={href} target="_blank" rel="noopener noreferrer" aria-label={`View posting for ${what} (opens in a new tab)`}>
            View posting
          </a>
        )}
        {trackedApplicationId !== null ? (
          <Link to={`/applications/${trackedApplicationId}`} aria-label={`Open workspace for ${what}`}>
            Open workspace
          </Link>
        ) : (
          <button type="button" aria-disabled={tracking ? true : undefined} aria-label={`${tracking ? "Tracking..." : "Track"} ${what}`} onClick={() => !tracking && onTrack(job)}>
            {tracking ? "Tracking..." : "Track"}
          </button>
        )}
      </div>
      {trackError !== null && (
        <div className="bj-disc-problem">
          <FailureState message={`Couldn't track ${what}: ${trackError}`} onRetry={() => !tracking && onTrack(job)} />
        </div>
      )}
      <div className="bj-disc-trail">
        <TrailLine trail={rowTrail(job, now, timeZone)} receipt={rowReceipt(job, scoringStrategy)} receiptTitle={`Where this result came from: ${job.title}`} />
      </div>
    </li>
  );
}
