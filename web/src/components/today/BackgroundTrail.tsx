import { useId, useState } from "react";
import { Link } from "react-router-dom";
import { SETTINGS_PATH } from "../../lib/settingsPaths";
import { checkedWords, searchesTrail, type BackgroundView } from "../../lib/todayFeed";
import { notSet } from "../../lib/trail";
import { FailureState, LoadingState, StaleNote } from "../paper/StateViews";
import { StaticTrailLine, TrailLine, TrailText } from "../paper/TrailLine";
import "../../styles/today.css";

// "In the background": what runs without a click and on whose key. Saved searches are matched and
// scored by a model on the person's own key, and Gmail replies are read the same way, so the summary
// carries the model mark. From 1280px it is the page's right margin (placement "margin"); under
// that it is one line under the band that opens inline (placement "inline"). Both hold the same
// lines, built by lib/todayFeed.ts from what GET /saved-searches and GET /credentials returned.

export interface BackgroundTrailProps {
  view: BackgroundView;
  now: Date;
  placement: "margin" | "inline";
  /** Re-reads the account. */
  onRetry: () => void;
  timeZone?: string;
}

function Lines({ view, now, onRetry, timeZone }: Omit<BackgroundTrailProps, "placement">) {
  const searches = view.searches;
  const gmail = view.gmail;
  return (
    <div className="bj-bg-lines">
      {searches.state === "loading" && <LoadingState rows={1} rowHeight={44} label="Loading saved searches..." />}
      {searches.state === "failed" && <FailureState message="Your saved searches couldn't be read." onRetry={onRetry} />}
      {searches.state === "ready" && searches.total === 0 && (
        <p className="bj-bg-line">
          No saved searches, so nothing is being matched. A saved search is scored by your model on your key.{" "}
          <Link to="/discover">Save one in Discover</Link>
        </p>
      )}
      {searches.state === "ready" && searches.total > 0 && <SearchesLine view={searches} now={now} timeZone={timeZone} />}

      {gmail.state === "ready" && gmail.connection === "connected" && (
        <StaticTrailLine trail={{ glyph: "model", actor: "Gmail replies read by your model", basis: "on your key" }} />
      )}
      {gmail.state === "ready" && gmail.connection === "reconnect_needed" && (
        <>
          <StaticTrailLine trail={{ glyph: "evidence", actor: "Gmail needs reconnecting", basis: "replies aren't being read" }} />
          <p className="bj-bg-line">
            <Link to={SETTINGS_PATH}>Reconnect Gmail</Link>
          </p>
        </>
      )}
      {gmail.state === "ready" && gmail.connection === "none" && (
        <p className="bj-bg-line">
          <span className="bj-bg-line-label">Gmail replies</span> {"·"} <TrailText model={notSet("Not connected")} />
        </p>
      )}
      {gmail.state === "failed" && <StaticTrailLine trail={{ glyph: "failed", actor: "Your Gmail connection couldn't be read" }} />}
    </div>
  );
}

function SearchesLine({
  view,
  now,
  timeZone,
}: {
  view: Extract<BackgroundView["searches"], { state: "ready" }>;
  now: Date;
  timeZone?: string;
}) {
  const line = searchesTrail(view, now, timeZone);
  if (line === null) return null;
  return (
    <>
      <TrailLine trail={line.trail} receipt={line.receipt} receiptTitle="Your saved searches" />
      {view.checkedAt === null && view.active > 0 && <StaleNote>These haven&apos;t been checked yet.</StaleNote>}
    </>
  );
}

// The one line the inline form opens from: "In the background" and the counts.
function summaryOf(view: BackgroundView, now: Date, timeZone?: string): string {
  const parts: string[] = [];
  const searches = view.searches;
  if (searches.state === "loading") parts.push("loading");
  else if (searches.state === "failed") parts.push("saved searches couldn't be read");
  else if (searches.total === 0) parts.push("no saved searches");
  else {
    parts.push(`${searches.active} saved ${searches.active === 1 ? "search" : "searches"} active`);
    if (searches.paused > 0) parts.push(`${searches.paused} paused`);
    const when = searches.checkedAt === null ? null : checkedWords(searches.checkedAt, now, timeZone);
    parts.push(when ?? "not checked yet");
  }
  // Gmail is named only when replies are really being read.
  if (view.gmail.state === "ready" && view.gmail.connection === "connected") parts.push("Gmail replies read on your key");
  return parts.join(" · ");
}

export function BackgroundTrail({ view, now, placement, onRetry, timeZone }: BackgroundTrailProps) {
  const [open, setOpen] = useState(false);
  const bodyId = useId();

  if (placement === "margin") {
    return (
      <aside className="bj-today-margin" aria-labelledby={`${bodyId}-title`}>
        <h2 className="bj-bg-title" id={`${bodyId}-title`}>
          <span className="bj-trail-glyph bj-trail-glyph--violet" aria-hidden="true">
            {"✦"}
          </span>{" "}
          In the background
        </h2>
        <Lines view={view} now={now} onRetry={onRetry} timeZone={timeZone} />
      </aside>
    );
  }
  return (
    <div className="bj-bg">
      <button type="button" className="bj-bg-toggle" aria-expanded={open} aria-controls={open ? bodyId : undefined} onClick={() => setOpen(!open)}>
        <span className="bj-bg-toggle-words">
          <span className="bj-trail-glyph bj-trail-glyph--violet" aria-hidden="true">
            {"✦"}
          </span>{" "}
          In the background {"·"} {summaryOf(view, now, timeZone)}
        </span>
        <span aria-hidden="true">{open ? "⌃" : "›"}</span>
      </button>
      {open && (
        <div id={bodyId} className="bj-bg-body">
          <Lines view={view} now={now} onRetry={onRetry} timeZone={timeZone} />
        </div>
      )}
    </div>
  );
}
