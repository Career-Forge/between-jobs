import type { ReactNode } from "react";
import { HeaderComposer } from "../HeaderComposer";
import { SectionOrderEditor } from "../SectionOrderEditor";
import { ShapeSettingsPanel } from "../ShapeSettingsPanel";
import { TailorPanel } from "../TailorPanel";
import { GeneratePanel, type GeneratePanelProps } from "../GeneratePanel";
import { DocumentBlock } from "../paper/DocumentLayout";
import { SectionChapter } from "../paper/SectionChapter";
import { LoadingState, SetupRequiredState, type StateLink } from "../paper/StateViews";
import type { CanonicalProfile } from "../../lib/profileTypes";
import { SECTION_IDS } from "../../lib/workspaceSections";
import { PostingBlock } from "./PostingBlock";
import "../../styles/workspace.css";

// The resume part of the workspace: Tailor (the posting and how the profile covers it), Draft (the
// resume's header, structure and settings) and Generate. Review follows in ReviewSection.
//
// The three Studio cards -- HeaderComposer, SectionOrderEditor and ShapeSettingsPanel -- are embedded as
// they are: the same components the Profile page edits the master document with, pointed at this
// application's own resume document by `applicationId`. They keep their own card frame; this page only
// gives each a heading.
//
// A part that needs something the person has not set up says what is missing, with the link that fixes
// it, and fires no request while it is known to be missing: Draft and Tailor need an active profile,
// Tailor (a model call on mount) and Generate also need a usable model key. Until the credentials read
// has answered, "known to be missing" is not known either way, so Tailor is not mounted and Generate's
// button is not drawn: a model call must never go out ahead of the answer that could forbid it.

export type Missing = { message: string; missing: readonly [StateLink, ...StateLink[]] };

export interface ResumeSectionsProps {
  applicationId: string;
  descriptionText: string | undefined | null;
  /** The active profile, or null when there is none (the page's 404 is tolerated, not an error). */
  profile: CanonicalProfile | null;
  /** What Tailor and Generate are missing, if the page knows; null when they can run. */
  modelSetup: Missing | null;
  /** The credentials read has not answered: Tailor and the Generate button wait for it (an answer of
   *  "failed" is an answer; it claims nothing, and they run). */
  modelPending?: boolean;
  generate: Omit<GeneratePanelProps, "applicationId" | "setupMissing">;
}

const NEEDS_PROFILE: Missing = {
  message: "This part works from your profile, and no profile is active yet.",
  missing: [{ label: "Add your profile", to: "/profile" }],
};

function Titled({ title, children }: { title: string; children: ReactNode }) {
  return (
    <DocumentBlock>
      <SectionChapter level={3} title={title} />
      {children}
    </DocumentBlock>
  );
}

export function ResumeSections({ applicationId, descriptionText, profile, modelSetup, modelPending = false, generate }: ResumeSectionsProps) {
  const generateSetup: Missing | null = profile === null ? NEEDS_PROFILE : modelSetup;
  return (
    <>
      <div className="bj-ws-section">
        <SectionChapter id={SECTION_IDS.tailor} eyebrow="Resume" title="Tailor">
          <p>The posting, and how well your profile covers what it asks for.</p>
        </SectionChapter>
        <PostingBlock text={descriptionText} />
        <DocumentBlock>
          <SectionChapter level={3} title="Coverage" />
          {profile === null ? (
            <SetupRequiredState {...NEEDS_PROFILE} />
          ) : modelSetup !== null ? (
            <SetupRequiredState {...modelSetup} />
          ) : modelPending ? (
            <LoadingState rows={2} rowHeight={48} label="Checking your model setup..." />
          ) : (
            <TailorPanel applicationId={applicationId} />
          )}
        </DocumentBlock>
      </div>

      <div className="bj-ws-section">
        <SectionChapter id={SECTION_IDS.draft} title="Draft">
          <p>Header, structure and settings for this application's resume. Changes here apply to this application only.</p>
        </SectionChapter>
        {profile === null ? (
          <DocumentBlock>
            <SetupRequiredState message="No active profile yet -- import one to edit this resume's header, structure and settings." missing={NEEDS_PROFILE.missing} />
          </DocumentBlock>
        ) : (
          <>
            <Titled title="Header">
              <div className="bj-card">
                <HeaderComposer applicationId={applicationId} />
              </div>
            </Titled>
            <Titled title="Resume structure">
              <div className="bj-card">
                <p className="bj-muted bj-small">Drag to reorder, or hide a section.</p>
                <SectionOrderEditor profile={profile} applicationId={applicationId} />
              </div>
            </Titled>
            <Titled title="Resume settings">
              <div className="bj-card">
                <p className="bj-muted bj-small">Overrides for this application only -- unset fields fall back to your profile's defaults.</p>
                <ShapeSettingsPanel applicationId={applicationId} />
              </div>
            </Titled>
          </>
        )}
      </div>

      <div className="bj-ws-section">
        <SectionChapter id={SECTION_IDS.generate} title="Generate" />
        <DocumentBlock>
          <GeneratePanel applicationId={applicationId} setupMissing={generateSetup} setupPending={modelPending && profile !== null} {...generate} />
        </DocumentBlock>
      </div>
    </>
  );
}
