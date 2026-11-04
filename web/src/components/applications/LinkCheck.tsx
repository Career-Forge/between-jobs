import { useEffect, useState } from "react";
import type { Application } from "../../lib/applicationsTypes";
import { JUST_NOW_MS, linkCheckLine, type LinkCheckEntry } from "../../lib/linkCheck";
import { nameOf } from "./ApplicationParts";
import "../../styles/applications.css";

// "Check posting" on an application card: a plain outlined button (the page's one cyan fill is the band's)
// and, after it in the same row, what the check said. One press is one request, made by the page
// (pages/Applications.tsx), which holds the answer until the page is reloaded; this only draws it, and
// nothing here touches the card's status or place.
//
// The answer is a live region that is in the row from the start, empty until there is something to say, so
// a screen reader hears it arrive (a region inserted with its words already in it is often missed). A press
// clears the last answer while the new one is out, so the same words twice in a row are still heard. The
// button is aria-disabled, not disabled, while the request is out: focus stays on it, and the press is
// ignored.
//
// WHEN IT WAS CHECKED IS NOT IN THE LIVE REGION. A listed answer says "Checked just now." and, a minute later,
// the clock time. The region is atomic, so any change inside it has a screen reader read the whole answer
// again, unprompted. The time is its own line after the region, which no screen reader is watching: it is
// read when the person goes looking, and its change at one minute is silent.

export interface LinkCheckProps {
  application: Application;
  /** What the page holds for this card: nothing, a request out, or what came back. */
  entry: LinkCheckEntry | undefined;
  onCheck: () => void;
}

export function LinkCheck({ application, entry, onCheck }: LinkCheckProps) {
  const pending = entry?.kind === "pending";
  return (
    <>
      <button
        type="button"
        className="bj-app-check"
        aria-label={`${pending ? "Checking posting" : "Check posting"} for ${nameOf(application)}`}
        aria-disabled={pending ? true : undefined}
        aria-busy={pending ? true : undefined}
        onClick={() => {
          if (!pending) onCheck();
        }}
      >
        {pending ? "Checking..." : "Check posting"}
      </button>
      <CheckResult entry={entry} />
    </>
  );
}

function CheckResult({ entry }: { entry: LinkCheckEntry | undefined }) {
  const [tick, refresh] = useState(0);
  // Only a still-listed answer says when it was checked, and "just now" is true for a minute: the one
  // re-render that turns it into the clock time.
  const freshSince = entry?.kind === "answered" && entry.result.state === "still_listed" ? entry.receivedAt : null;
  useEffect(() => {
    if (freshSince === null) return;
    const wait = JUST_NOW_MS - (Date.now() - freshSince);
    if (wait <= 0) return;
    const timer = setTimeout(() => refresh((count) => count + 1), wait);
    return () => clearTimeout(timer);
  }, [freshSince, tick]);

  const line = linkCheckLine(entry, Date.now());
  return (
    <>
      <p className="bj-app-check-result" role="status" data-state={line?.state}>
        {line?.text}
      </p>
      {line !== null && line.when !== null && <p className="bj-app-check-when">{line.when}</p>}
    </>
  );
}
