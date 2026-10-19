// The sections of the application workspace, in the order they are on the page, and the jump bar
// that indexes them. One list, so the bar, the page and the tests cannot disagree about which section
// has which anchor.
//
//   Resume    Tailor #ch-sources · Draft #ch-draft · Generate #generate · Review #ch-check
//   Research  Company intel #company-intel · Contacts #contacts · Events #warm-path-events ·
//             Hiring posts #hiring-posts (listed only while the server has the feature on)
//   Prepare   Practice #interview-practice · Your play #your-play
//
// The ids are CROSS_NAV_HASH's (lib/applicationsBoard.ts): the Applications menu and Today link to
// them. The group labels ("Resume", "Research", "Prepare") are not anchors.

import type { JumpGroup } from "../components/paper/JumpBar";
import { CROSS_NAV_HASH } from "./applicationsBoard";

export const SECTION_IDS = {
  tailor: CROSS_NAV_HASH.tailor,
  draft: CROSS_NAV_HASH.draft,
  generate: CROSS_NAV_HASH.generate,
  review: CROSS_NAV_HASH.review,
  companyIntel: CROSS_NAV_HASH.companyIntel,
  contacts: CROSS_NAV_HASH.contacts,
  events: CROSS_NAV_HASH.warmPathEvents,
  hiringPosts: CROSS_NAV_HASH.hiringPosts,
  practice: CROSS_NAV_HASH.interviewPractice,
  yourPlay: CROSS_NAV_HASH.positioningBrief,
} as const;

// The heading that holds the fit check's reading when a draft was held back. A link that says "see
// why" (the band, Today) lands here, not at the top of the Generate section.
export const HONEST_READ_HEADING_ID = "honest-read-heading";

export function workspaceJumpGroups(hiringPostsOn: boolean): JumpGroup[] {
  return [
    {
      label: "Resume",
      links: [
        { id: SECTION_IDS.tailor, label: "Tailor" },
        { id: SECTION_IDS.draft, label: "Draft" },
        { id: SECTION_IDS.generate, label: "Generate" },
        { id: SECTION_IDS.review, label: "Review" },
      ],
    },
    {
      label: "Research",
      links: [
        { id: SECTION_IDS.companyIntel, label: "Company intel" },
        { id: SECTION_IDS.contacts, label: "Contacts" },
        { id: SECTION_IDS.events, label: "Events" },
        ...(hiringPostsOn ? [{ id: SECTION_IDS.hiringPosts, label: "Hiring posts" }] : []),
      ],
    },
    {
      label: "Prepare",
      links: [
        { id: SECTION_IDS.practice, label: "Practice" },
        { id: SECTION_IDS.yourPlay, label: "Your play" },
      ],
    },
  ];
}
