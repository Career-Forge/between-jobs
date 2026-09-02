import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import {
  CROSS_NAV_ITEMS,
  formatAppliedDate,
  groupByStatus,
  otherStatuses,
  STATUS_LABELS,
} from "../lib/applicationsBoard";
import { ALL_STATUSES, type Application, type ApplicationStatus } from "../lib/applicationsTypes";

// Applications Kanban board (K2) -- native HTML5 drag-and-drop between the
// 7 K1-enforced columns, same underlying technique as
// HeaderComposer.tsx/SectionOrderEditor.tsx's own row reordering:
// `draggable` on the card, "what's being dragged" held in React state
// (not `dataTransfer`), and `onDragOver`'s `preventDefault()` making a
// drop target valid. Unlike those two single-array reorders, a drop here
// can land in a DIFFERENT column, so what's tracked is the dragged
// application's id (not a bare index) and the drop target is a column's
// status, not a position -- and the drop handler lives on the column
// container, not just on other cards, so an empty column is a valid
// target too.
//
// Dropping calls the exact same stage-change endpoint (via the
// `onChangeStatus` prop, owned by Applications.tsx) that the List view's
// status `<select>` already used, then relies on the caller reloading the
// whole list -- no optimistic merge of a fake snapshot/resume_exists onto
// the partial stage-change response.
//
// The per-card "Move to..." select is a REQUIRED accessible/keyboard/
// touch fallback, not optional polish: native HTML5 DnD has no keyboard
// or touch story of its own, so without it a keyboard-only or touch-only
// user could never move a card between columns at all.

export default function ApplicationsBoard({
  applications,
  busyIds,
  onChangeStatus,
}: {
  applications: Application[];
  busyIds: ReadonlySet<string>;
  onChangeStatus: (applicationId: string, status: ApplicationStatus) => void;
}) {
  const [draggedId, setDraggedId] = useState<string | null>(null);
  const [dragOverStatus, setDragOverStatus] = useState<ApplicationStatus | null>(null);
  const groups = groupByStatus(applications);

  function handleDrop(status: ApplicationStatus) {
    setDragOverStatus(null);
    const dragged = applications.find((a) => a.id === draggedId);
    setDraggedId(null);
    if (!dragged || dragged.status === status) return;
    onChangeStatus(dragged.id, status);
  }

  return (
    <div className="bj-kanban-board">
      {ALL_STATUSES.map((status) => (
        <div
          key={status}
          className={
            dragOverStatus === status
              ? "bj-kanban-column bj-kanban-column-dragover"
              : "bj-kanban-column"
          }
          onDragOver={(e) => e.preventDefault()}
          onDragEnter={() => setDragOverStatus(status)}
          onDragLeave={() => setDragOverStatus((current) => (current === status ? null : current))}
          onDrop={(e) => {
            e.preventDefault();
            handleDrop(status);
          }}
        >
          <div className="bj-kanban-column-header">
            <span className="bj-kanban-column-title">{STATUS_LABELS[status]}</span>
            <span className="bj-muted bj-small">{groups[status].length}</span>
          </div>
          <div className="bj-kanban-column-cards">
            {groups[status].length === 0 && (
              <div className="bj-muted bj-small">No applications</div>
            )}
            {groups[status].map((application) => (
              <KanbanCard
                key={application.id}
                application={application}
                busy={busyIds.has(application.id)}
                dragging={draggedId === application.id}
                onDragStart={() => setDraggedId(application.id)}
                onDragEnd={() => setDraggedId(null)}
                onChangeStatus={(status2) => onChangeStatus(application.id, status2)}
              />
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

function KanbanCard({
  application,
  busy,
  dragging,
  onDragStart,
  onDragEnd,
  onChangeStatus,
}: {
  application: Application;
  busy: boolean;
  dragging: boolean;
  onDragStart: () => void;
  onDragEnd: () => void;
  onChangeStatus: (status: ApplicationStatus) => void;
}) {
  const snapshot = application.snapshot;
  const appliedLabel = formatAppliedDate(application.date_applied);
  const navigate = useNavigate();
  return (
    <div
      className={dragging ? "bj-kanban-card bj-kanban-card-dragging" : "bj-kanban-card"}
      draggable={!busy}
      onDragStart={onDragStart}
      onDragEnd={onDragEnd}
    >
      <div className="bj-kanban-card-title">{snapshot?.title ?? "Untitled"}</div>
      <div className="bj-muted bj-small">
        {snapshot?.company_name ?? "Unknown company"}
        {snapshot?.location_text ? ` -- ${snapshot.location_text}` : ""}
      </div>
      {(application.resume_exists || appliedLabel) && (
        <div className="bj-actions">
          {application.resume_exists && <span className="bj-badge-emerald">Resume ✓</span>}
          {appliedLabel && <span className="bj-muted bj-small">{appliedLabel}</span>}
        </div>
      )}
      <div className="bj-actions bj-kanban-card-move">
        <select
          aria-label={`Move "${snapshot?.title ?? "Untitled"}" to a different stage`}
          value=""
          disabled={busy}
          onChange={(e) => {
            const value = e.target.value;
            if (value) onChangeStatus(value as ApplicationStatus);
          }}
        >
          <option value="">Move to...</option>
          {otherStatuses(application.status).map((status) => (
            <option key={status} value={status}>
              {STATUS_LABELS[status]}
            </option>
          ))}
        </select>
        {busy ? (
          <span className="bj-muted bj-small">Moving...</span>
        ) : (
          <Link to={`/applications/${application.id}`}>Open</Link>
        )}
      </div>
      <div className="bj-actions bj-kanban-card-actions">
        <select
          aria-label={`Actions for "${snapshot?.title ?? "Untitled"}"`}
          value=""
          onChange={(e) => {
            const hash = e.target.value;
            if (hash) navigate(`/applications/${application.id}#${hash}`);
          }}
        >
          <option value="">Actions...</option>
          {CROSS_NAV_ITEMS.map((item) => (
            <option key={item.hash} value={item.hash}>
              {item.label}
            </option>
          ))}
        </select>
      </div>
    </div>
  );
}
