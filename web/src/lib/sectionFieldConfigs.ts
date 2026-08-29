// Declarative field shapes for the 8 "array of typed entries" sections --
// drives EntryEditModal so adding a new editable section type is a config
// entry, not a new form component. Deliberately excludes: Personal/header
// (genuinely different scope -- the Header Composer), Achievements and
// Skills (flat string lists, not structured entries -- they get their own
// lighter inline editors, see AchievementsSection/SkillsSection).

export type FieldType = "text" | "bullets" | "chips" | "daterange";

export interface FieldConfig {
  key: string;
  label: string;
  type: FieldType;
  required?: boolean;
  placeholder?: string;
  // daterange only
  startKey?: string;
  endKey?: string;
  currentKey?: string;
}

export interface SectionFieldConfig {
  entryLabel: string;
  fields: FieldConfig[];
}

export const SECTION_FIELD_CONFIGS: Record<string, SectionFieldConfig> = {
  experience: {
    entryLabel: "Experience",
    fields: [
      { key: "title", label: "Job Title", type: "text", required: true },
      { key: "company", label: "Company", type: "text", required: true },
      { key: "location", label: "Location", type: "text", placeholder: "City, ST" },
      {
        key: "dates",
        label: "Dates",
        type: "daterange",
        startKey: "start_date",
        endKey: "end_date",
        currentKey: "is_current",
      },
      { key: "bullets", label: "Bullets", type: "bullets" },
      { key: "skills", label: "Skills", type: "chips" },
      { key: "metrics", label: "Metrics", type: "chips", placeholder: "e.g. 94% accuracy" },
    ],
  },
  projects: {
    entryLabel: "Project",
    fields: [
      { key: "name", label: "Name", type: "text", required: true },
      { key: "url", label: "URL", type: "text" },
      { key: "tech", label: "Tech", type: "chips" },
      { key: "bullets", label: "Bullets", type: "bullets" },
      { key: "metrics", label: "Metrics", type: "chips" },
    ],
  },
  education: {
    entryLabel: "Education",
    fields: [
      { key: "degree", label: "Degree", type: "text", required: true },
      { key: "field", label: "Field of Study", type: "text" },
      { key: "institution", label: "Institution", type: "text", required: true },
      { key: "location", label: "Location", type: "text" },
      { key: "dates", label: "Dates", type: "daterange", startKey: "start_date", endKey: "end_date" },
      { key: "gpa", label: "GPA", type: "text" },
      { key: "coursework", label: "Coursework", type: "chips" },
    ],
  },
  publications: {
    entryLabel: "Publication",
    fields: [
      { key: "title", label: "Title", type: "text", required: true },
      { key: "authors", label: "Authors", type: "text", placeholder: "Doe, J., Patel, R." },
      { key: "venue", label: "Venue", type: "text", placeholder: "e.g. KDD 2024" },
      { key: "date", label: "Date", type: "text" },
      { key: "url", label: "URL", type: "text" },
    ],
  },
  patents: {
    entryLabel: "Patent",
    fields: [
      { key: "title", label: "Title", type: "text", required: true },
      { key: "patent_number", label: "Patent Number", type: "text", placeholder: "US 11,000,000" },
      { key: "status", label: "Status", type: "text", placeholder: "filed / pending / granted" },
      { key: "date", label: "Date", type: "text" },
      { key: "url", label: "URL", type: "text" },
    ],
  },
  certifications: {
    entryLabel: "Certification",
    fields: [
      { key: "name", label: "Name", type: "text", required: true },
      { key: "issuer", label: "Issuer", type: "text" },
      { key: "date", label: "Date", type: "text" },
      { key: "url", label: "URL", type: "text" },
    ],
  },
  languages: {
    entryLabel: "Language",
    fields: [
      { key: "language", label: "Language", type: "text", required: true },
      { key: "fluency", label: "Fluency", type: "text", placeholder: "e.g. Native, Fluent" },
    ],
  },
  volunteering: {
    entryLabel: "Volunteering",
    fields: [
      { key: "organization", label: "Organization", type: "text", required: true },
      { key: "role", label: "Role", type: "text" },
      { key: "dates", label: "Dates", type: "daterange", startKey: "start_date", endKey: "end_date" },
      { key: "bullets", label: "Bullets", type: "bullets" },
    ],
  },
};
