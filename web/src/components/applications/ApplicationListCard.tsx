import type { Application, ApplicationStatus } from "../../lib/applicationsTypes";
import {
  CardActions,
  CardIdentity,
  CardMeta,
  CardProblem,
  CardTrail,
  OpenLink,
  StatusSelect,
  type MoveProblem,
} from "./ApplicationParts";
import "../../styles/applications.css";

// One application as a row of the List view: who it is, its status as a chip with the resume badge
// beside it and the day it was applied, its controls, and its trail as the last line. The
// status select is always here, showing the real status, so a wrong one is corrected in one choice.

export interface ApplicationListCardProps {
  application: Application;
  now: Date;
  busy: boolean;
  problem: MoveProblem | undefined;
  onChangeStatus: (status: ApplicationStatus) => void;
  onRetry: () => void;
}

export function ApplicationListCard({ application, now, busy, problem, onChangeStatus, onRetry }: ApplicationListCardProps) {
  const posting = application.snapshot?.source_url;
  return (
    <li className="bj-app-card" data-app-id={application.id} aria-busy={busy ? true : undefined}>
      <CardIdentity application={application} />
      <CardMeta application={application} now={now} showStatus />
      {problem !== undefined && <CardProblem problem={problem} onRetry={onRetry} />}
      <div className="bj-app-controls">
        <StatusSelect application={application} busy={busy} onChange={onChangeStatus} />
        {busy && <span className="bj-app-moving">Moving...</span>}
        {posting && (
          <a className="bj-app-link" href={posting} target="_blank" rel="noreferrer">
            View posting
          </a>
        )}
        <OpenLink application={application} label="Open workspace" />
        <CardActions application={application} />
      </div>
      <CardTrail application={application} now={now} />
    </li>
  );
}
