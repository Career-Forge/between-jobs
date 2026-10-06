// What the review screen lists: every value of the draft as a labelled field in the section it
// belongs to, the place in the file each one came from, and the values the model proposed that the
// check against the file did not keep, in plain words.
//
// The fields are made by walking the draft's JSON, not by reading chosen keys out of it, so a
// value cannot be left off the screen by a path nobody thought of: a leaf with a path this file
// has no label for lands in "Other", under its own path, and is shown like the rest. The labels
// are a table keyed by the path with its list positions replaced by `*` (and a map's own key by
// `{key}`); its completeness against the profile's own shape (profileTypes.ts) is a test, which
// builds a draft with EVERY field of every type set -- the fixture's types make adding a field to a
// type a compile error there -- and fails if any value lands in "Other". A second test reads the
// field names of the backend's own model (profile.py), since the types here once left some out, and a
// third runs the model's dump of a minimal profile through this module: the server sends every
// optional field at its default, and none of those may reach the screen as a value or as "Other".
//
// Pure module: no React, no DOM.

import type { TextSpan } from "./highlightSegments";
import { SKILL_CATEGORY_LABELS, type CanonicalProfile, type Skills } from "./profileTypes";
import type { AssumedValue, DroppedValue } from "./resumeImportDraft";

export type SectionId =
  | "personal"
  | "summary"
  | "experience"
  | "projects"
  | "education"
  | "publications"
  | "patents"
  | "skills"
  | "certifications"
  | "achievements"
  | "languages"
  | "volunteering"
  | "other";

interface SectionSpec {
  id: SectionId;
  title: string;
  // For a section that is a list of entries: what one is called, and which of its values name it.
  entry?: { singular: string; named: readonly string[]; joiner: string };
}

const SECTIONS: readonly SectionSpec[] = [
  { id: "personal", title: "Personal details" },
  { id: "summary", title: "Summary" },
  { id: "experience", title: "Experience", entry: { singular: "Experience", named: ["title", "company"], joiner: ", " } },
  { id: "projects", title: "Projects", entry: { singular: "Project", named: ["name"], joiner: ", " } },
  { id: "education", title: "Education", entry: { singular: "Education", named: ["degree", "institution"], joiner: ", " } },
  { id: "publications", title: "Publications", entry: { singular: "Publication", named: ["title"], joiner: ", " } },
  { id: "patents", title: "Patents", entry: { singular: "Patent", named: ["title"], joiner: ", " } },
  { id: "skills", title: "Skills" },
  { id: "certifications", title: "Certifications", entry: { singular: "Certification", named: ["name"], joiner: ", " } },
  { id: "achievements", title: "Achievements" },
  { id: "languages", title: "Languages", entry: { singular: "Language", named: ["language"], joiner: ", " } },
  {
    id: "volunteering",
    title: "Volunteering",
    entry: { singular: "Volunteering", named: ["organization", "role"], joiner: ", " },
  },
  { id: "other", title: "Other" },
];

const SECTION_BY_ID = new Map(SECTIONS.map((section) => [section.id, section]));
const SECTION_ORDER = new Map(SECTIONS.map((section, index) => [section.id, index]));

// The top-level key of the profile each section is read from.
const SECTION_OF_KEY: Readonly<Record<string, SectionId>> = {
  personal: "personal",
  summary_bullets: "summary",
  experience: "experience",
  projects: "projects",
  education: "education",
  publications: "publications",
  patents: "patents",
  skills: "skills",
  certifications: "certifications",
  achievements: "achievements",
  languages: "languages",
  volunteering: "volunteering",
};

interface Rule {
  section: SectionId;
  label: string;
  // A list item: the label gets its position (1-based) after it.
  numbered?: boolean;
  // Set by code from other values, not read from the file (the flag of a current role, which
  // address is the primary one).
  derived?: boolean;
  // The last segment of the path is a key the profile does not fix in advance (a map's key): the
  // label gets it after it, in brackets. Its template ends in KEY_SEGMENT.
  keyed?: boolean;
}

const RULES = new Map<string, Rule>();

// What stands for a map's own key in a template.
const KEY_SEGMENT = "{key}";

function rule(template: string, section: SectionId, label: string, options: Partial<Rule> = {}): void {
  RULES.set(template, { section, label, ...options });
}

// A rule for every value of a map: `mapRule("/personal/x", ...)` labels /personal/x/<any key>.
function mapRule(template: string, section: SectionId, label: string): void {
  rule(`${template}/${KEY_SEGMENT}`, section, label, { keyed: true });
}

// personal
rule("/personal/name", "personal", "Name");
rule("/personal/headline", "personal", "Headline");
rule("/personal/emails/*/address", "personal", "Email", { numbered: true });
rule("/personal/emails/*/primary", "personal", "Primary email", { numbered: true, derived: true });
rule("/personal/phones/*/number", "personal", "Phone", { numbered: true });
rule("/personal/phones/*/primary", "personal", "Primary phone", { numbered: true, derived: true });
rule("/personal/phones/*/region", "personal", "Phone region", { numbered: true });
rule("/personal/links/linkedin", "personal", "LinkedIn");
rule("/personal/links/github", "personal", "GitHub");
rule("/personal/links/portfolio", "personal", "Portfolio");
rule("/personal/links/scholar", "personal", "Google Scholar");
rule("/personal/links/other/*/label", "personal", "Other link name", { numbered: true });
rule("/personal/links/other/*/url", "personal", "Other link", { numbered: true });
rule("/personal/location/city", "personal", "City");
rule("/personal/location/region", "personal", "Region");
rule("/personal/location/country", "personal", "Country");
rule("/personal/location/show_on_resume", "personal", "Show the place on the resume", { derived: true });
rule("/personal/work_authorization", "personal", "Work authorization");
// The render-only fields (profile.py keeps them so the canonical JSON round-trips). A document import
// never fills them, but a draft is the server's whole profile, so each has a label rather than
// showing up under its raw path in "Other".
rule("/personal/dob", "personal", "Date of birth");
rule("/personal/nationality", "personal", "Nationality");
rule("/personal/marital_status", "personal", "Marital status");
mapRule("/personal/work_authorization_status", "personal", "Work authorization status");
rule("/personal/photo", "personal", "Photo");
rule("/personal/signature", "personal", "Signature on the resume");

// summary and achievements
rule("/summary_bullets/*", "summary", "Summary line", { numbered: true });
rule("/achievements/*", "achievements", "Achievement", { numbered: true });

// experience
rule("/experience/*/title", "experience", "Job title");
rule("/experience/*/company", "experience", "Company");
rule("/experience/*/location", "experience", "Location");
rule("/experience/*/start_date", "experience", "Start date");
rule("/experience/*/end_date", "experience", "End date");
rule("/experience/*/is_current", "experience", "Current role", { derived: true });
rule("/experience/*/bullets/*", "experience", "Bullet", { numbered: true });
rule("/experience/*/skills/*", "experience", "Skill", { numbered: true });
rule("/experience/*/metrics/*", "experience", "Metric", { numbered: true });

// projects
rule("/projects/*/name", "projects", "Name");
rule("/projects/*/url", "projects", "Link");
rule("/projects/*/tech/*", "projects", "Technology", { numbered: true });
rule("/projects/*/bullets/*", "projects", "Bullet", { numbered: true });
rule("/projects/*/metrics/*", "projects", "Metric", { numbered: true });

// education
rule("/education/*/degree", "education", "Degree");
rule("/education/*/field", "education", "Field of study");
rule("/education/*/institution", "education", "School");
rule("/education/*/location", "education", "Location");
rule("/education/*/start_date", "education", "Start date");
rule("/education/*/end_date", "education", "End date");
rule("/education/*/gpa", "education", "Grade");
rule("/education/*/coursework/*", "education", "Course", { numbered: true });

// publications and patents
rule("/publications/*/title", "publications", "Title");
rule("/publications/*/authors", "publications", "Authors");
rule("/publications/*/venue", "publications", "Venue");
rule("/publications/*/date", "publications", "Date");
rule("/publications/*/url", "publications", "Link");
rule("/patents/*/title", "patents", "Title");
rule("/patents/*/patent_number", "patents", "Patent number");
rule("/patents/*/status", "patents", "Status");
rule("/patents/*/date", "patents", "Date");
rule("/patents/*/url", "patents", "Link");

// skills: one rule per category the profile has, from the labels the Skills view already uses, so
// a category added to the profile's type is a compile error there before it is a gap here
for (const category of Object.keys(SKILL_CATEGORY_LABELS) as (keyof Skills)[]) {
  rule(`/skills/${category}/*`, "skills", SKILL_CATEGORY_LABELS[category]);
}

// certifications, languages, volunteering
rule("/certifications/*/name", "certifications", "Name");
rule("/certifications/*/issuer", "certifications", "Issuer");
rule("/certifications/*/date", "certifications", "Date");
rule("/certifications/*/url", "certifications", "Link");
rule("/languages/*/language", "languages", "Language");
rule("/languages/*/fluency", "languages", "Level");
rule("/volunteering/*/organization", "volunteering", "Organization");
rule("/volunteering/*/role", "volunteering", "Role");
rule("/volunteering/*/start_date", "volunteering", "Start date");
rule("/volunteering/*/end_date", "volunteering", "End date");
rule("/volunteering/*/bullets/*", "volunteering", "Bullet", { numbered: true });

const RULE_ORDER = new Map([...RULES.keys()].map((template, index) => [template, index]));

// Every path template this module has a label for: what a test compares the backend's own field
// names against, so a field added there without a label here is a failing test, not a raw path
// on the screen.
export const REVIEW_PATH_TEMPLATES: readonly string[] = [...RULES.keys()];

// A value the server's profile always carries, set to its empty default when nothing set it. Showing
// it would list a value as read from the file ("Signature: No", with "no place in your file was
// found") that nobody read: the render-only flag is the person's to turn on, and an import never
// does. The same flag set to true is a value and is shown; nothing else is skipped.
const UNSET_DEFAULTS: ReadonlyMap<string, string | number | boolean> = new Map([["/personal/signature", false]]);

// ── paths ──────────────────────────────────────────────────────────────────

// A JSON pointer's segments, with the escapes a pointer allows undone.
function segmentsOf(path: string): string[] {
  if (!path.startsWith("/")) return [];
  return path
    .slice(1)
    .split("/")
    .map((segment) => segment.replace(/~1/g, "/").replace(/~0/g, "~"));
}

function isIndex(segment: string): boolean {
  return /^(0|[1-9]\d*)$/.test(segment);
}

function templateOf(segments: readonly string[]): string {
  return "/" + segments.map((segment) => (isIndex(segment) ? "*" : segment)).join("/");
}

function indicesOf(segments: readonly string[]): number[] {
  return segments.filter(isIndex).map(Number);
}

interface Matched {
  // The key of the rule in the table, which is also its place in the order the labels are listed.
  template: string;
  rule: Rule;
  // The map key a keyed rule matched, else null.
  key: string | null;
  // The list positions in the path (not counting a map's key, which may itself look like a number).
  indices: number[];
}

// The rule for a path: the one for its template, or else the one for the map its last segment is a
// key of.
function matchRule(segments: readonly string[]): Matched | undefined {
  const exact = RULES.get(templateOf(segments));
  if (exact !== undefined) return { template: templateOf(segments), rule: exact, key: null, indices: indicesOf(segments) };
  if (segments.length < 2) return undefined;
  const head = segments.slice(0, -1);
  const template = `${templateOf(head)}/${KEY_SEGMENT}`;
  const keyed = RULES.get(template);
  if (keyed === undefined || keyed.keyed !== true) return undefined;
  return { template, rule: keyed, key: segments[segments.length - 1], indices: indicesOf(head) };
}

// ── the draft's leaves ─────────────────────────────────────────────────────

type LeafValue = string | number | boolean;

interface Leaf {
  path: string;
  value: LeafValue;
}

function escapeSegment(segment: string): string {
  return segment.replace(/~/g, "~0").replace(/\//g, "~1");
}

// Every value in a JSON tree that is text, a number or a true/false, with its JSON pointer. An
// empty string, null and an empty list or object hold nothing and are skipped.
export function leavesOf(tree: unknown, base = ""): Leaf[] {
  if (typeof tree === "string") return tree === "" ? [] : [{ path: base, value: tree }];
  if (typeof tree === "number" || typeof tree === "boolean") return [{ path: base, value: tree }];
  if (Array.isArray(tree)) return tree.flatMap((item, index) => leavesOf(item, `${base}/${index}`));
  if (typeof tree === "object" && tree !== null) {
    return Object.entries(tree).flatMap(([key, item]) => leavesOf(item, `${base}/${escapeSegment(key)}`));
  }
  return [];
}

export function valueText(value: LeafValue): string {
  if (typeof value === "boolean") return value ? "Yes" : "No";
  return String(value);
}

function getAt(tree: unknown, segments: readonly string[]): unknown {
  let node = tree;
  for (const segment of segments) {
    if (typeof node !== "object" || node === null) return undefined;
    node = (node as Record<string, unknown>)[segment];
  }
  return node;
}

// ── fields, blocks, sections ───────────────────────────────────────────────

export interface ReviewField {
  // The value's JSON pointer into the draft: the key of its source span.
  path: string;
  label: string;
  value: string;
  // Set by code from other values, not read from the file: there is no place in the file to show.
  derived: boolean;
  // Where it was found in the extracted text, or null when no place is known.
  span: TextSpan | null;
  // A note from the server about a guess made for this value (a year with no month), or null.
  assumption: string | null;
}

export interface ReviewBlock {
  // Stable within a section: the entry's position, or "all".
  id: string;
  // What an entry is called ("Experience 1: Data Engineer, Acme"); null for a section that is not
  // a list of entries.
  heading: string | null;
  fields: ReviewField[];
}

export interface ReviewSection {
  id: SectionId;
  title: string;
  blocks: ReviewBlock[];
}

function labelOf(matched: Matched): string {
  const { rule, indices, key } = matched;
  if (key !== null) return `${rule.label} (${key})`;
  if (rule.numbered !== true || indices.length === 0) return rule.label;
  return `${rule.label} ${indices[indices.length - 1] + 1}`;
}

function entryHeading(section: SectionSpec, profile: unknown, key: string, index: number): string {
  const entry = section.entry;
  if (entry === undefined) return "";
  const named = entry.named
    .map((field) => getAt(profile, [key, String(index), field]))
    .filter((value): value is string => typeof value === "string" && value !== "");
  const name = named.join(entry.joiner);
  return name === "" ? `${entry.singular} ${index + 1}` : `${entry.singular} ${index + 1}: ${name}`;
}

interface Placed extends ReviewField {
  section: SectionId;
  rank: number[];
}

// The draft as sections of labelled fields. `spans` is where each value was found; `assumptions` are
// the server's notes about values it had to guess part of -- shown beside a field only when the
// field at that path still holds the value the note was about, since the server numbers them the
// way the model listed its entries (see the note on `DroppedValue.path`).
export function buildReview(
  profile: CanonicalProfile,
  spans: Readonly<Record<string, TextSpan>>,
  assumptions: readonly AssumedValue[] = [],
): ReviewSection[] {
  const noteFor = new Map<string, AssumedValue>();
  for (const assumption of assumptions) noteFor.set(assumption.path, assumption);

  const placed: Placed[] = [];
  for (const leaf of leavesOf(profile)) {
    if (UNSET_DEFAULTS.has(leaf.path) && UNSET_DEFAULTS.get(leaf.path) === leaf.value) continue;
    const segments = segmentsOf(leaf.path);
    const matched = matchRule(segments);
    const text = valueText(leaf.value);
    const assumption = noteFor.get(leaf.path);
    const base = {
      path: leaf.path,
      value: text,
      span: spans[leaf.path] ?? null,
      assumption: assumption !== undefined && assumption.value === text ? assumption.note : null,
    };
    if (matched === undefined) {
      placed.push({
        ...base,
        // an unknown path is shown, under the path it has: never dropped, never guessed at
        label: leaf.path,
        derived: false,
        section: "other",
        rank: [0],
      });
      continue;
    }
    const { indices } = matched;
    const spec = SECTION_BY_ID.get(matched.rule.section);
    const entryIndex = spec?.entry !== undefined && indices.length > 0 ? indices[0] : -1;
    placed.push({
      ...base,
      label: labelOf(matched),
      derived: matched.rule.derived === true,
      section: matched.rule.section,
      // entry first, then the order the labels are listed in, then the position in a list
      rank: [entryIndex, RULE_ORDER.get(matched.template) ?? 0, ...indices.slice(entryIndex === -1 ? 0 : 1)],
    });
  }

  placed.sort((a, b) => {
    const bySection = (SECTION_ORDER.get(a.section) ?? 0) - (SECTION_ORDER.get(b.section) ?? 0);
    if (bySection !== 0) return bySection;
    for (let i = 0; i < Math.max(a.rank.length, b.rank.length); i += 1) {
      const diff = (a.rank[i] ?? -1) - (b.rank[i] ?? -1);
      if (diff !== 0) return diff;
    }
    return 0;
  });

  const sections: ReviewSection[] = [];
  for (const spec of SECTIONS) {
    const inSection = placed.filter((field) => field.section === spec.id);
    if (inSection.length === 0) continue;
    const key = Object.entries(SECTION_OF_KEY).find(([, id]) => id === spec.id)?.[0] ?? "";
    const blocks: ReviewBlock[] = [];
    if (spec.entry === undefined) {
      blocks.push({ id: "all", heading: null, fields: inSection.map(toField) });
    } else {
      const byEntry = new Map<number, Placed[]>();
      for (const field of inSection) {
        const entryIndex = field.rank[0];
        byEntry.set(entryIndex, [...(byEntry.get(entryIndex) ?? []), field]);
      }
      for (const [entryIndex, fields] of byEntry) {
        blocks.push({
          id: String(entryIndex),
          heading: entryHeading(spec, profile, key, entryIndex),
          fields: fields.map(toField),
        });
      }
    }
    sections.push({ id: spec.id, title: spec.title, blocks });
  }
  return sections;
}

function toField(placed: Placed): ReviewField {
  return {
    path: placed.path,
    label: placed.label,
    value: placed.value,
    derived: placed.derived,
    span: placed.span,
    assumption: placed.assumption,
  };
}

// Every field of every section, in the order they are listed.
export function allFields(sections: readonly ReviewSection[]): ReviewField[] {
  return sections.flatMap((section) => section.blocks.flatMap((block) => block.fields));
}

// ── what the model proposed that the check did not keep ────────────────────

// Where a path points, in words. The numbers are the model's own (see `DroppedValue.path`).
export function describePath(path: string): string {
  const segments = segmentsOf(path);
  if (segments.length === 0) return path === "" ? "The whole profile" : path;
  const sectionId = Object.hasOwn(SECTION_OF_KEY, segments[0]) ? SECTION_OF_KEY[segments[0]] : undefined;
  const section = sectionId === undefined ? undefined : SECTION_BY_ID.get(sectionId);
  if (section === undefined) return path;

  const matched = matchRule(segments);
  const indices = matched !== undefined ? matched.indices : indicesOf(segments);
  const entryNumber = section.entry !== undefined && indices.length > 0 ? indices[0] + 1 : null;
  const entry = entryNumber === null ? "" : `, entry ${entryNumber}`;
  if (matched !== undefined) {
    // a list position the label does not carry itself ("Tools" in a skills list) is said here
    const last = segments[segments.length - 1];
    const position = matched.key === null && isIndex(last) && matched.rule.numbered !== true ? `, item ${Number(last) + 1}` : "";
    return `${section.title}${entry}, ${labelOf(matched)}${position}`;
  }
  // an entry as a whole (/experience/2)
  if (section.entry !== undefined && segments.length === 2 && indices.length === 1) {
    return `${section.title}, entry ${indices[0] + 1}`;
  }
  const rest = segments.slice(entryNumber === null ? 1 : 2).map((segment) => (isIndex(segment) ? `item ${Number(segment) + 1}` : segment.replace(/_/g, " ")));
  return `${section.title}${entry}${rest.length > 0 ? `, ${rest.join(", ")}` : ""}`;
}

const REASON_TEXT: Readonly<Record<string, string>> = {
  not_in_document: "This text is not in your file.",
  date_not_in_document: "This date is not in your file.",
  invalid_value: "This is not a usable value for that field.",
  wrong_type: "It came back in a form the profile cannot hold.",
  unknown_field: "The profile has no field like this.",
  too_long: "It is too long to be one value.",
  too_many: "It is more than the profile can hold, so the rest were left out.",
  never_from_document: "This is never read from a file. Set it yourself in the profile editor.",
  duplicates_sibling: "It is already part of another value, so it was not repeated.",
  // True whatever the cause: the missing value may be absent from the file or may have been dropped
  // for a reason of its own (too long, not usable), which is listed on its own line.
  entry_incomplete: "It was removed because a value it cannot do without was not kept.",
};

// Why a value was left out, as a sentence. A reason this page does not know (a newer server) is
// said to be unknown rather than made into something plausible.
export function dropReasonText(reason: string): string {
  return Object.hasOwn(REASON_TEXT, reason)
    ? REASON_TEXT[reason]
    : `It was left out by the check against your file (reason: ${reason}).`;
}

export interface DroppedLine {
  where: string;
  why: string;
  // The server's own detail ("a work arrangement, not a place"), or null when it adds nothing.
  detail: string | null;
  // What the model proposed, or null.
  proposed: string | null;
}

// Reasons whose server detail only says the sentence again ("this text is not in the document").
const DETAIL_ADDS_NOTHING = new Set(["not_in_document", "date_not_in_document", "unknown_field", "never_from_document"]);

export function droppedLines(dropped: readonly DroppedValue[]): DroppedLine[] {
  return dropped.map((item) => ({
    where: describePath(item.path),
    why: dropReasonText(item.reason),
    detail: item.detail.trim() === "" || DETAIL_ADDS_NOTHING.has(item.reason) ? null : item.detail,
    proposed: item.value,
  }));
}

export interface AssumptionLine {
  where: string;
  value: string;
  note: string;
}

export function assumptionLines(assumptions: readonly AssumedValue[]): AssumptionLine[] {
  return assumptions.map((item) => ({ where: describePath(item.path), value: item.value, note: item.note }));
}
