import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import "../../styles/paper.css";

// The states a block can be in besides "has content", as small pieces a page composes. They carry
// no data of their own: every word is handed in, so a page says only what it really knows.
//
//   LoadingState        static placeholder rows in the block's own shape; no shimmer. It reserves its
//                       height up front, so content arriving does not move what is below it.
//   EmptyState          what will appear here, in real item kinds, and where to go to make it happen
//   PartialState        what arrived renders elsewhere; this names what did not, in the source's words
//   StaleNote           shown data with its real age -- only from a returned field, else no age
//   SetupRequiredState  what is missing, each with a link to where it is fixed
//   FailureState        a block (or the page body) that could not be read, with an optional retry
//
// A whole-page failure is the band's job (G1, F1); its body uses FailureState without `onRetry`,
// because the band already holds "Try again". A failure of one block keeps its own Retry.

export interface LoadingStateProps {
  /** How many placeholder rows. */
  rows?: number;
  /** Each row's height in px, matching the real row it stands in for. */
  rowHeight?: number;
  /** What a screen reader hears. */
  label?: string;
  /**
   * False when the page already says the same thing elsewhere in a live region (the next-step band's
   * "Searching..."), so the placeholders are not announced a second time. The block stays aria-busy.
   */
  announce?: boolean;
}

export function LoadingState({ rows = 3, rowHeight = 56, label = "Loading...", announce = true }: LoadingStateProps) {
  return (
    <div className="bj-state bj-state--loading" role={announce ? "status" : undefined} aria-busy="true">
      <span className="bj-visually-hidden">{label}</span>
      <div aria-hidden="true">
        {Array.from({ length: rows }, (_, index) => (
          <div key={index} className="bj-skeleton-row" style={{ height: rowHeight }} />
        ))}
      </div>
    </div>
  );
}

export interface StateLink {
  label: string;
  /** One of the app's own routes. */
  to: string;
}

export interface EmptyStateProps {
  heading: string;
  /** What will appear here, named with real item kinds, and what each needs. */
  children: ReactNode;
  /** The next step as a cyan text link. The band holds the button; this never repeats it. */
  link?: StateLink;
}

export function EmptyState({ heading, children, link }: EmptyStateProps) {
  return (
    <div className="bj-state bj-state--empty">
      <h2 className="bj-state-heading">{heading}</h2>
      <div className="bj-state-body">{children}</div>
      {link !== undefined && (
        <p className="bj-state-link">
          <Link to={link.to}>{link.label}</Link>
        </p>
      )}
    </div>
  );
}

export interface PartialStateProps {
  /** "2 of this run's searches failed:" */
  lead?: string;
  /** Each in the source's own words. */
  notices: readonly string[];
}

export function PartialState({ lead, notices }: PartialStateProps) {
  if (notices.length === 0) return null;
  return (
    <div className="bj-state bj-state--partial">
      {lead !== undefined && <p className="bj-state-lead">{lead}</p>}
      <ul>
        {notices.map((notice, index) => (
          <li key={`${index}-${notice}`}>{notice}</li>
        ))}
      </ul>
    </div>
  );
}

export interface StaleNoteProps {
  children: ReactNode;
  /** The returned date or time, already formatted. Omit it when no field carried one. */
  when?: string | null;
}

export function StaleNote({ children, when }: StaleNoteProps) {
  return (
    <p className="bj-state bj-state--stale">
      {children}
      {when !== undefined && when !== null && when !== "" && (
        <>
          {" "}
          {"·"} <span className="bj-trail-mono">{when}</span>
        </>
      )}
    </p>
  );
}

export interface SetupRequiredStateProps {
  /** What the feature needs, and why. */
  message: string;
  /** What is missing. At least one, each with the place it is fixed: setup-required always has a way forward. */
  missing: readonly [StateLink, ...StateLink[]];
}

export function SetupRequiredState({ message, missing }: SetupRequiredStateProps) {
  return (
    <div className="bj-state bj-state--setup">
      <p>{message}</p>
      <p className="bj-state-missing">
        <span>Missing:</span>
        {missing.map((item) => (
          <Link key={`${item.to}-${item.label}`} to={item.to}>
            {item.label}
          </Link>
        ))}
      </p>
    </div>
  );
}

export interface FailureStateProps {
  /** What could not be read, plainly. */
  message: string;
  /** Present for a block's own failure; leave it out where the band already holds "Try again". */
  onRetry?: () => void;
  retryLabel?: string;
}

export function FailureState({ message, onRetry, retryLabel = "Retry" }: FailureStateProps) {
  return (
    <div className="bj-state bj-state--failure" role="alert">
      <p>
        <span className="bj-state-glyph" aria-hidden="true">
          {"✕"}
        </span>
        {message}
      </p>
      {onRetry !== undefined && (
        <button type="button" onClick={onRetry}>
          {retryLabel}
        </button>
      )}
    </div>
  );
}
