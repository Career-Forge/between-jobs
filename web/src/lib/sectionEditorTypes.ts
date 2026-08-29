// Section drag/hide (Sprint 3.2d) -- Proposal §24.5.2's Structure mode.
//
// Scoped to the INTERSECTION of what forge-engines can actually render
// (assemble.py's SECTION_LATEX: summary, experience, internships,
// projects, skills, education, certifications, achievements, activities,
// publications, patents) and what between-jobs' own profile schema can
// populate (profileTypes.ts's CanonicalProfile). `internships`/
// `activities` have no schema field to source from -- listing either
// would silently never render what the user enabled. `publications`/
// `patents` (R7, 2026-08-23) DO now have a real forge-engines render
// template (the achievements-style required/presence-gated path, not
// bubbles), so they're listed below; `languages`/`volunteering` still
// have none, and stay out for the same "would look like a working
// toggle that does nothing" reason.

import type { CanonicalProfile } from "./profileTypes";

export const RENDERABLE_SECTIONS = [
  "summary",
  "experience",
  "projects",
  "skills",
  "achievements",
  "certifications",
  "publications",
  "patents",
  "education",
] as const;

export type SectionName = (typeof RENDERABLE_SECTIONS)[number];

export const SECTION_LABELS: Record<SectionName, string> = {
  summary: "Summary",
  experience: "Experience",
  projects: "Projects",
  skills: "Skills",
  achievements: "Achievements",
  certifications: "Certifications",
  publications: "Publications",
  patents: "Patents",
  education: "Education",
};

export function sectionHasContent(section: SectionName, profile: CanonicalProfile): boolean {
  switch (section) {
    case "summary":
      return (profile.summary_bullets?.length ?? 0) > 0;
    case "experience":
      return (profile.experience?.length ?? 0) > 0;
    case "projects":
      return (profile.projects?.length ?? 0) > 0;
    case "skills":
      return Object.values(profile.skills ?? {}).some((s) => (s?.length ?? 0) > 0);
    case "achievements":
      return (profile.achievements?.length ?? 0) > 0;
    case "certifications":
      return (profile.certifications?.length ?? 0) > 0;
    case "publications":
      return (profile.publications?.length ?? 0) > 0;
    case "patents":
      return (profile.patents?.length ?? 0) > 0;
    case "education":
      return (profile.education?.length ?? 0) > 0;
  }
}
