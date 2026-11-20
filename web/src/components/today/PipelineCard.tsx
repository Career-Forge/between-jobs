import { useId } from "react";
import { Link } from "react-router-dom";
import type { PipelineView } from "../../lib/todayPipeline";
import { FailureState, LoadingState } from "../paper/StateViews";
import "../../styles/today.css";

// "Pipeline": how many applications sit at each stage, one line a stage, each line a link to the
// Applications page. What it says is decided in lib/todayPipeline.ts from the applications Today has
// already read; this file only draws it.
//
//   loading   a placeholder, and no number: nothing has been read yet
//   failed    that the applications couldn't be read, with a Retry that reads the account again
//   none      one sentence, with a link to Discover, where a job is found and tracked
//   some      the stages that hold applications, in the Applications board's order, then "Other" for
//             any row whose status is not one of them
//
// From 1280px it sits in Today's right column, between the setup checklist and "In the background"
// (placement "side"); under that it is the page's last section (placement "inline"), under a heading
// the size of the feed's own.

export interface PipelineCardProps {
  view: PipelineView;
  placement: "side" | "inline";
  /** Reads the account again. */
  onRetry: () => void;
}

export function PipelineCard({ view, placement, onRetry }: PipelineCardProps) {
  const titleId = useId();
  return (
    <section className={placement === "side" ? "bj-pipeline" : "bj-pipeline bj-today-section"} aria-labelledby={titleId}>
      <h2 className={placement === "side" ? "bj-bg-title" : "bj-today-heading"} id={titleId}>
        Pipeline
      </h2>
      {view.state === "loading" && <LoadingState rows={2} rowHeight={28} label="Loading your applications..." />}
      {view.state === "failed" && <FailureState message="Your applications couldn't be read, so there are no counts to show." onRetry={onRetry} />}
      {view.state === "ready" && view.total === 0 && (
        <p className="bj-pipeline-note">
          No applications are tracked yet: <Link to="/discover">find a job in Discover</Link>.
        </p>
      )}
      {view.state === "ready" && view.total > 0 && (
        <ul className="bj-pipeline-lines">
          {view.lines.map((line) => (
            <li key={line.key}>
              <Link className="bj-pipeline-line" to="/applications">
                <span className="bj-pipeline-stage">{line.label}</span> <span className="bj-pipeline-count">{line.count}</span>
                <span className="bj-visually-hidden"> {line.count === 1 ? "application" : "applications"}</span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
