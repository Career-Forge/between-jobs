import type { ReactNode } from "react";
import { CompanyIntelPanel } from "../CompanyIntelPanel";
import { ContactFinderPanel } from "../ContactFinderPanel";
import { HiringSignalsPanel } from "../HiringSignalsPanel";
import { InterviewPracticePanel } from "../InterviewPracticePanel";
import { PositioningBriefPanel } from "../PositioningBriefPanel";
import { WarmPathEventsPanel } from "../WarmPathEventsPanel";
import { DocumentBlock } from "../paper/DocumentLayout";
import { SectionChapter } from "../paper/SectionChapter";
import type { HiringStatus } from "../../lib/hiringSignalsStatus";
import { SECTION_IDS } from "../../lib/workspaceSections";
import "../../styles/workspace.css";

// The research and prepare parts of the workspace: Company intel, Contacts, Events and Hiring posts
// (Research), then Practice and Your play (Prepare). Each panel is its own section and keeps the panel
// component it always had; this file is where they sit in the page.
//
// THE CONVENTION A PANEL FOLLOWS HERE (and the one to restyle it by):
//
//   <div className="bj-ws-section">                       one section = one wrapper
//     <SectionChapter id="company-intel" title="Company intel" eyebrow="Research" />
//     <DocumentBlock className="bj-ws-panel">            a block, so the panel can draw its own trail
//       <CompanyIntelPanel applicationId={...} />
//     </DocumentBlock>
//   </div>
//
//  - The section's anchor id (SECTION_IDS, lib/workspaceSections.ts, which are CROSS_NAV_HASH's) is on the
//    SectionChapter, and the chapter's heading is the h2 a link to the anchor lands focus on. The eyebrow
//    names the group and is on the first section of each group only.
//  - A panel draws its own trail line with <BlockTrail trail receipt receiptTitle /> as the last thing in
//    its block (components/paper/DocumentLayout.tsx). Written inside a DocumentBlock it sits in the
//    right margin from 1280px, under the block from 960px, and opens as a sheet on a phone, without the
//    panel knowing which. A panel with a richer layout may use several blocks.
//  - The panel does NOT draw its own h2: the chapter is the section's heading, and a panel's own headings
//    are h3 and below. Nothing hides a panel's heading any more; one that draws an h2 gets two.
//  - The page's one cyan fill is the next-step band's. A panel's buttons are outlined and none asks for
//    the primary class; a failure of one panel never changes the band.
//
// Each of the six is built the same way (components/research/ and lib/research*.ts): an intro that says
// what the section does and what it spends, an outlined run button, the states a block can be in (a
// placeholder while it reads, teaching before it has anything, a stale line with the stored run's own date,
// the searches that did not complete, a setup notice with its link, a failure with a Retry), its evidence in
// the one evidence pattern, and one trail line from returned fields only.
//
// Hiring posts exists only while the server is known to have the feature on. While that is not known
// (still being asked, or the ask failed) the anchor is an empty target with no heading, and nothing is
// said about it; once the server answers that it is off, a link to #hiring-posts lands on a note saying
// so instead of scrolling nowhere. The Hiring posts jump-bar entry follows the same rule.

function Section({ id, title, eyebrow, children }: { id: string; title: string; eyebrow?: string; children: ReactNode }) {
  return (
    <div className="bj-ws-section">
      <SectionChapter id={id} title={title} eyebrow={eyebrow} />
      <DocumentBlock className="bj-ws-panel">{children}</DocumentBlock>
    </div>
  );
}

export interface PanelSectionsProps {
  applicationId: string;
  hiring: HiringStatus;
  /** Whether the address names #hiring-posts (the note is only for a person who asked for it). */
  hiringRequested: boolean;
}

export function PanelSections({ applicationId, hiring, hiringRequested }: PanelSectionsProps) {
  return (
    <>
      <Section id={SECTION_IDS.companyIntel} title="Company intel" eyebrow="Research">
        <CompanyIntelPanel applicationId={applicationId} />
      </Section>
      <Section id={SECTION_IDS.contacts} title="Contacts">
        <ContactFinderPanel applicationId={applicationId} />
      </Section>
      <Section id={SECTION_IDS.events} title="Warm-path events">
        <WarmPathEventsPanel applicationId={applicationId} />
      </Section>
      {hiring.kind === "enabled" ? (
        <Section id={SECTION_IDS.hiringPosts} title="Hiring posts">
          <HiringSignalsPanel applicationId={applicationId} />
        </Section>
      ) : hiring.kind === "disabled" && hiringRequested ? (
        <div className="bj-ws-section">
          <SectionChapter id={SECTION_IDS.hiringPosts} title="Hiring posts">
            <p>Hiring posts is switched off on this server. Nothing else is affected.</p>
          </SectionChapter>
        </div>
      ) : (
        <div id={SECTION_IDS.hiringPosts} />
      )}
      <Section id={SECTION_IDS.practice} title="Interview practice" eyebrow="Prepare">
        <InterviewPracticePanel applicationId={applicationId} />
      </Section>
      <Section id={SECTION_IDS.yourPlay} title="Your play">
        <PositioningBriefPanel applicationId={applicationId} />
      </Section>
    </>
  );
}
