import { useCallback, useEffect, useState } from "react";
import { Link, useLocation, useParams } from "react-router-dom";
import { CompanyIntelPanel } from "../components/CompanyIntelPanel";
import { ContactFinderPanel } from "../components/ContactFinderPanel";
import { GeneratePanel } from "../components/GeneratePanel";
import { HeaderComposer } from "../components/HeaderComposer";
import { InterviewPracticePanel } from "../components/InterviewPracticePanel";
import { SectionOrderEditor } from "../components/SectionOrderEditor";
import { ShapeSettingsPanel } from "../components/ShapeSettingsPanel";
import { TailorPanel } from "../components/TailorPanel";
import { WarmPathEventsPanel } from "../components/WarmPathEventsPanel";
import { ApiError, apiFetch } from "../lib/api";
import { CROSS_NAV_HASH } from "../lib/applicationsBoard";
import type { CanonicalProfile } from "../lib/profileTypes";

// Application workspace (Sprint 3.3d) -- Proposal §37.4. The Studio
// embedded here is the SAME HeaderComposer/SectionOrderEditor Profile.tsx
// uses for the master document -- just pointed at this application's own
// resume_document via `applicationId`, per each component's own doc
// comment anticipating exactly this reuse.

interface JobSnapshot {
  id: string;
  title: string;
  company_name: string;
  location_text: string | null;
  description_text: string;
  source_url: string;
}

interface Application {
  id: string;
  status: string;
  snapshot: JobSnapshot | null;
  resume_exists: boolean;
}

interface ProfileVersion {
  id: string;
  canonical_json: CanonicalProfile;
}

type State =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ready"; application: Application; profile: CanonicalProfile | null };

export default function ApplicationWorkspace() {
  const { id } = useParams<{ id: string }>();
  const location = useLocation();
  const [state, setState] = useState<State>({ kind: "loading" });

  const load = useCallback(async () => {
    if (!id) return;
    setState({ kind: "loading" });
    try {
      const application = await apiFetch<Application>(`/applications/${id}`);
      // Company intel, header, and generation all work without a profile --
      // only section reordering needs one, so a missing profile scopes down
      // to that one card instead of blocking the whole workspace.
      let profile: CanonicalProfile | null = null;
      try {
        const profileVersion = await apiFetch<ProfileVersion>("/profile/current");
        profile = profileVersion.canonical_json;
      } catch (e) {
        if (!(e instanceof ApiError && e.status === 404)) {
          throw e;
        }
      }
      setState({ kind: "ready", application, profile });
    } catch (e) {
      setState({ kind: "error", message: e instanceof Error ? e.message : "Failed to load" });
    }
  }, [id]);

  useEffect(() => {
    void load();
  }, [load]);

  // Applications Kanban K3 -- jump to the panel a cross-nav link (Generate
  // Docs / Research Company / Practice Interview) pointed at. Depends on
  // BOTH `state.kind` and `location.hash`, not just one: `state.kind`
  // covers landing here fresh with the hash already in the URL (the
  // targeted `<div id="...">` doesn't exist in the DOM until the ready
  // render happens), and `location.hash` covers clicking a second
  // cross-nav link while already on this same application's workspace --
  // React Router doesn't remount the page for a hash-only navigation, so a
  // mount-only effect would never see that change.
  useEffect(() => {
    if (state.kind !== "ready") return;
    const targetId = location.hash.replace(/^#/, "");
    if (!targetId) return;
    const target = document.getElementById(targetId);
    target?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [state.kind, location.hash]);

  if (state.kind === "loading") {
    return <WorkspaceFrame />;
  }

  if (state.kind === "error") {
    return (
      <WorkspaceFrame>
        <div className="bj-error">{state.message}</div>
      </WorkspaceFrame>
    );
  }

  const { application, profile } = state;
  const snapshot = application.snapshot;

  return (
    <WorkspaceFrame title={snapshot?.title} company={snapshot?.company_name}>
      <div className="bj-workspace-layout">
        <div className="bj-workspace-studio">
          <div className="bj-card">
            <h2>Header</h2>
            <HeaderComposer applicationId={application.id} />
          </div>
          <div className="bj-card">
            <h2>Resume structure</h2>
            {profile ? (
              <>
                <p className="bj-muted bj-small">Drag to reorder, or hide a section.</p>
                <SectionOrderEditor profile={profile} applicationId={application.id} />
              </>
            ) : (
              <p className="bj-muted bj-small">
                No active profile yet -- <Link to="/profile">import one</Link> to edit resume
                structure here.
              </p>
            )}
          </div>
          <div className="bj-card">
            <h2>Resume settings</h2>
            <p className="bj-muted bj-small">
              Overrides for this application only -- unset fields fall back to your profile's
              defaults.
            </p>
            <ShapeSettingsPanel applicationId={application.id} />
          </div>
          <div id={CROSS_NAV_HASH.generate}>
            <GeneratePanel applicationId={application.id} initialHasResume={application.resume_exists} />
          </div>
        </div>
        <div className="bj-workspace-tailor">
          <TailorPanel applicationId={application.id} />
          <div id={CROSS_NAV_HASH.companyIntel}>
            <CompanyIntelPanel applicationId={application.id} />
          </div>
          <div id={CROSS_NAV_HASH.contacts}>
            <ContactFinderPanel applicationId={application.id} />
          </div>
          <div id={CROSS_NAV_HASH.warmPathEvents}>
            <WarmPathEventsPanel applicationId={application.id} />
          </div>
          <div id={CROSS_NAV_HASH.interviewPractice}>
            <InterviewPracticePanel applicationId={application.id} />
          </div>
        </div>
      </div>
    </WorkspaceFrame>
  );
}

function WorkspaceFrame({
  title,
  company,
  children,
}: {
  title?: string;
  company?: string;
  children?: React.ReactNode;
}) {
  return (
    <div>
      <div className="bj-breadcrumb">
        <Link to="/applications">Applications</Link>
        <span aria-hidden="true"> / </span>
        <span>{title ?? "Loading..."}</span>
      </div>
      <h1>{title ?? "Application"}</h1>
      {company && <div className="bj-muted">{company}</div>}
      {children}
    </div>
  );
}
