import { PageHeader } from "../paper/PageHeader";
import { StaticTrailLine } from "../paper/TrailLine";
import { formatAppliedDate } from "../../lib/applicationsBoard";
import { headerTrail } from "../../lib/workspaceTrails";
import type { WorkspaceApplication } from "../../lib/workspaceTypes";
import type { ApplicationStatus } from "../../lib/applicationsTypes";
import { StatusSelect } from "./StatusSelect";
import type { ReactNode } from "react";
import "../../styles/workspace.css";

// The top of the workspace: the job's identity (title, company, place), the status select, and under
// them one line about the whole page -- how the application was tracked and when -- plus the dates and
// the link the application carries. The trail line here sits inline at every width: it describes the
// page, not one block, so it never goes in the margin. The date is worded by formatAppliedDate, which
// the Applications cards use too: it is when the application first became Applied, and the server keeps
// it when it is moved back to Saved, so there it reads "First applied".

export interface WorkspaceHeaderProps {
  application: WorkspaceApplication;
  now: Date;
  statusBusy: boolean;
  onStatus: (status: ApplicationStatus) => void;
  /** A status change that did not go through, shown under the header. */
  problem?: ReactNode;
}

// A posting address that is a web address, or null: the link is data from a scraped page.
export function webAddress(value: string | undefined | null): string | null {
  return typeof value === "string" && /^https?:\/\//i.test(value.trim()) ? value.trim() : null;
}

export function WorkspaceHeader({ application, now, statusBusy, onStatus, problem }: WorkspaceHeaderProps) {
  const snapshot = application.snapshot;
  const title = snapshot?.title ?? "Application";
  const place = [snapshot?.company_name, snapshot?.location_text].filter((part): part is string => typeof part === "string" && part.trim() !== "");
  const posting = webAddress(snapshot?.source_url);
  const appliedOn = formatAppliedDate(application.date_applied, now, undefined, application.status);
  return (
    <>
      <PageHeader
        title={title}
        breadcrumb={[{ label: "Applications", to: "/applications" }, { label: title }]}
        subtitle={place.length === 0 ? undefined : place.join(" · ")}
        actions={<StatusSelect status={application.status} busy={statusBusy} onChange={onStatus} />}
      />
      <div className="bj-ws-meta">
        <StaticTrailLine trail={headerTrail(application, now)} />
        {(appliedOn !== null || posting !== null) && (
          <p className="bj-ws-facts">
            {appliedOn !== null && <span>{appliedOn}</span>}
            {posting !== null && (
              <a href={posting} target="_blank" rel="noopener noreferrer">
                View posting
                <span className="bj-visually-hidden"> (opens in a new tab)</span>
              </a>
            )}
          </p>
        )}
        {problem}
      </div>
    </>
  );
}
