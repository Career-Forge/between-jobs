import { useState } from "react";
import { groupByStatus, STATUS_LABELS } from "../lib/applicationsBoard";
import { ALL_STATUSES, type Application, type ApplicationStatus } from "../lib/applicationsTypes";
import {
  CardActions,
  CardIdentity,
  CardMeta,
  CardProblem,
  CardTrail,
  MoveSelect,
  OpenLink,
  type MoveProblem,
} from "./applications/ApplicationParts";
import "../styles/applications.css";

// Applications Kanban board -- native HTML5 drag-and-drop between the 7 enforced columns, the same
// technique as HeaderComposer.tsx/SectionOrderEditor.tsx's own row reordering: `draggable` on the
// card, "what's being dragged" held in React state (not `dataTransfer`), and `onDragOver`'s
// `preventDefault()` making a drop target valid. A drop can land in a DIFFERENT column, so what is
// tracked is the dragged application's id and the drop target is a column's status, and the drop
// handler lives on the column container, so an empty column is a valid target too.
//
// Dropping calls the exact same stage-change endpoint (via `onChangeStatus`, owned by
// pages/Applications.tsx) that the List view's status select uses, then relies on the caller
// reloading the whole list -- no optimistic merge of a fake snapshot/resume_exists onto the partial
// stage-change response.
//
// The per-card "Move to..." select is a REQUIRED accessible/keyboard/touch fallback, not optional
// polish: native HTML5 DnD has no keyboard or touch story of its own, so without it a keyboard-only
// or touch-only user could never move a card between columns at all. On a phone this card is also
// the whole story: the board is replaced by one status at a time (StatusPicker.tsx).
//
// A column is a section named by its heading; its cards are list items, each ending in its trail line.
// The columns sit in one row that scrolls inside itself (a region the keyboard can reach), so the page
// never scrolls sideways.

export interface ApplicationsBoardProps {
  applications: readonly Application[];
  now: Date;
  busyIds: ReadonlySet<string>;
  problems: Readonly<Record<string, MoveProblem | undefined>>;
  onChangeStatus: (applicationId: string, status: ApplicationStatus) => void;
  onRetry: (applicationId: string) => void;
}

export default function ApplicationsBoard({ applications, now, busyIds, problems, onChangeStatus, onRetry }: ApplicationsBoardProps) {
  const [draggedId, setDraggedId] = useState<string | null>(null);
  const [dragOverStatus, setDragOverStatus] = useState<ApplicationStatus | null>(null);
  const groups = groupByStatus(applications);

  function handleDrop(status: ApplicationStatus) {
    setDragOverStatus(null);
    const dragged = applications.find((a) => a.id === draggedId);
    setDraggedId(null);
    if (!dragged || dragged.status === status || busyIds.has(dragged.id)) return;
    onChangeStatus(dragged.id, status);
  }

  return (
    <div className="bj-app-board" role="region" aria-label="Applications board" tabIndex={0}>
      {ALL_STATUSES.map((status) => (
        <section
          key={status}
          className={dragOverStatus === status ? "bj-app-column bj-app-column--dragover" : "bj-app-column"}
          aria-labelledby={`bj-app-column-${status}`}
          data-status={status}
          onDragOver={(e) => e.preventDefault()}
          onDragEnter={() => setDragOverStatus(status)}
          onDragLeave={() => setDragOverStatus((current) => (current === status ? null : current))}
          onDrop={(e) => {
            e.preventDefault();
            handleDrop(status);
          }}
        >
          <div className="bj-app-column-head">
            <h2 className="bj-app-column-title" id={`bj-app-column-${status}`}>
              {STATUS_LABELS[status]}
            </h2>
            <span className="bj-app-count">{groups[status].length}</span>
          </div>
          {groups[status].length === 0 ? (
            <p className="bj-app-column-empty">No applications</p>
          ) : (
            <ul className="bj-app-column-cards">
              {groups[status].map((application) => (
                <BoardCard
                  key={application.id}
                  application={application}
                  now={now}
                  busy={busyIds.has(application.id)}
                  problem={problems[application.id]}
                  dragging={draggedId === application.id}
                  onDragStart={() => setDraggedId(application.id)}
                  onDragEnd={() => setDraggedId(null)}
                  onChangeStatus={(next) => onChangeStatus(application.id, next)}
                  onRetry={() => onRetry(application.id)}
                />
              ))}
            </ul>
          )}
        </section>
      ))}
    </div>
  );
}

export interface BoardCardProps {
  application: Application;
  now: Date;
  busy: boolean;
  problem: MoveProblem | undefined;
  onChangeStatus: (status: ApplicationStatus) => void;
  onRetry: () => void;
  /** The drag handlers; left out where a card cannot be dragged (the phone's per-status list). */
  dragging?: boolean;
  onDragStart?: () => void;
  onDragEnd?: () => void;
}

export function BoardCard({ application, now, busy, problem, dragging = false, onDragStart, onDragEnd, onChangeStatus, onRetry }: BoardCardProps) {
  const draggable = onDragStart !== undefined && !busy;
  return (
    <li
      className={dragging ? "bj-app-card bj-app-card--board bj-app-card--dragging" : "bj-app-card bj-app-card--board"}
      data-app-id={application.id}
      aria-busy={busy ? true : undefined}
      draggable={draggable}
      onDragStart={onDragStart}
      onDragEnd={onDragEnd}
    >
      <CardIdentity application={application} />
      <CardMeta application={application} now={now} />
      {problem !== undefined && <CardProblem problem={problem} onRetry={onRetry} />}
      <div className="bj-app-controls">
        <MoveSelect application={application} busy={busy} onChange={onChangeStatus} />
        {busy ? <span className="bj-app-moving">Moving...</span> : <OpenLink application={application} label="Open" />}
      </div>
      <div className="bj-app-controls">
        <CardActions application={application} />
      </div>
      <CardTrail application={application} now={now} />
    </li>
  );
}
