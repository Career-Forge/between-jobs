import { describe, expect, it } from "vitest";
import guardSource from "../../../src/between_jobs/api/profile_import_guard.py?raw";
import profileSource from "../../../src/between_jobs/api/profile.py?raw";
import serverDefaultsSource from "../testing/serverProfileDefaults.json?raw";
import type {
  CanonicalProfile,
  Certification,
  Education,
  Email,
  Experience,
  LanguageEntry,
  Links,
  OtherLink,
  Patent,
  Personal,
  PersonalLocation,
  Phone,
  Project,
  Publication,
  Skills,
  VolunteerEntry,
} from "./profileTypes";
import {
  allFields,
  assumptionLines,
  buildReview,
  describePath,
  dropReasonText,
  droppedLines,
  leavesOf,
  valueText,
  REVIEW_PATH_TEMPLATES,
} from "./resumeImportReview";

// ── a draft with EVERY field of EVERY type set ─────────────────────────────
//
// `Required<...>` on each type makes this file stop compiling when a field is added to one of
// them, which is the prompt to give it a label in resumeImportReview.ts; the test below then
// fails until it has one, so no field of the profile can reach the screen as "Other".

const email: Required<Email> = { address: "pat@example.com", primary: true };
const phone: Required<Phone> = { number: "+1 555 0100", primary: true, region: "US" };
const otherLink: Required<OtherLink> = { label: "Blog", url: "https://blog.example.com" };
const links: Required<Links> = {
  linkedin: "linkedin.example.com/in/pat",
  github: "github.example.com/pat",
  portfolio: "pat.example.com",
  scholar: "scholar.example.com/pat",
  other: [otherLink],
};
const location: Required<PersonalLocation> = {
  city: "Springfield",
  region: "IL",
  country: "USA",
  show_on_resume: true,
};
const personal: Required<Personal> = {
  name: "Pat Example",
  headline: "Data Engineer",
  emails: [email],
  phones: [phone],
  links,
  location,
  work_authorization: "Authorized to work",
  dob: "1990-01-01",
  nationality: "Example",
  marital_status: "Single",
  work_authorization_status: { US: "citizen" },
  photo: "photo.example.com/pat.jpg",
  signature: true,
};
const experience: Required<Experience> = {
  title: "Data Engineer",
  company: "Acme Corp",
  location: "Remote",
  start_date: "2022-06",
  end_date: "present",
  is_current: true,
  bullets: ["Built pipelines", "Cut costs"],
  skills: ["Python"],
  metrics: ["30% faster"],
  pin: null,
};
const project: Required<Project> = {
  name: "Widget",
  url: "widget.example.com",
  tech: ["Rust"],
  bullets: ["Shipped it"],
  metrics: ["1k users"],
  pin: null,
};
const education: Required<Education> = {
  degree: "BSc",
  field: "Statistics",
  institution: "Example University",
  location: "Springfield",
  start_date: "2016",
  end_date: "2020",
  gpa: "3.9",
  coursework: ["Probability"],
  pin: null,
};
const publication: Required<Publication> = {
  title: "On Widgets",
  authors: "Example, P.",
  venue: "Journal of Widgets",
  date: "2021",
  url: "journal.example.com/widgets",
};
const patent: Required<Patent> = {
  title: "A widget",
  patent_number: "US 1",
  status: "Granted",
  date: "2022",
  url: "patents.example.com/1",
};
const skills: Required<Skills> = {
  programming: ["Python", "SQL"],
  ai_ml: ["PyTorch"],
  data_mlops: ["Airflow"],
  cloud_devops: ["AWS"],
  tools: ["Git"],
  other: ["Statistics"],
};
const certification: Required<Certification> = {
  name: "Cloud Practitioner",
  issuer: "Example Cloud",
  date: "2023",
  url: "certs.example.com/1",
};
const language: Required<LanguageEntry> = { language: "English", fluency: "Native" };
const volunteer: Required<VolunteerEntry> = {
  organization: "Food Bank",
  role: "Volunteer",
  start_date: "2019",
  end_date: "2020",
  bullets: ["Sorted donations"],
};
const FULL: Required<CanonicalProfile> = {
  personal,
  summary_bullets: ["Data engineer with a taste for tidy pipelines"],
  experience: [experience],
  projects: [project],
  education: [education],
  publications: [publication],
  patents: [patent],
  skills,
  certifications: [certification],
  achievements: ["Won a hackathon"],
  languages: [language],
  volunteering: [volunteer],
};

const NO_SPANS = {};

describe("every field of the profile's own types has a label and a section", () => {
  it("leaves nothing in Other, and shows every value exactly once", () => {
    const sections = buildReview(FULL, NO_SPANS);
    expect(sections.map((section) => section.id)).not.toContain("other");
    const fields = allFields(sections);
    expect(fields.length).toBe(leavesOf(FULL).length);
    expect(new Set(fields.map((field) => field.path)).size).toBe(fields.length);
    // and the fixture really does set a lot (a guard against an emptied fixture)
    expect(fields.length).toBeGreaterThan(55);
  });

  // Section, label and whether the value is set by a rule, for every field of the full profile, in
  // the order they are listed: a field moved to another section, renamed, or no longer marked as set
  // by a rule would otherwise change what the screen says with nothing failing.
  it("puts every field in its section, under its label, in order, and marks the ones a rule sets", () => {
    const rows = buildReview(FULL, NO_SPANS).flatMap((section) =>
      section.blocks.flatMap((block) => block.fields.map((field) => `${section.id} | ${field.label} | derived=${field.derived}`)),
    );
    expect(rows).toEqual([
      "personal | Name | derived=false",
      "personal | Headline | derived=false",
      "personal | Email 1 | derived=false",
      "personal | Primary email 1 | derived=true",
      "personal | Phone 1 | derived=false",
      "personal | Primary phone 1 | derived=true",
      "personal | Phone region 1 | derived=false",
      "personal | LinkedIn | derived=false",
      "personal | GitHub | derived=false",
      "personal | Portfolio | derived=false",
      "personal | Google Scholar | derived=false",
      "personal | Other link name 1 | derived=false",
      "personal | Other link 1 | derived=false",
      "personal | City | derived=false",
      "personal | Region | derived=false",
      "personal | Country | derived=false",
      "personal | Show the place on the resume | derived=true",
      "personal | Work authorization | derived=false",
      "personal | Date of birth | derived=false",
      "personal | Nationality | derived=false",
      "personal | Marital status | derived=false",
      "personal | Work authorization status (US) | derived=false",
      "personal | Photo | derived=false",
      "personal | Signature on the resume | derived=false",
      "summary | Summary line 1 | derived=false",
      "experience | Job title | derived=false",
      "experience | Company | derived=false",
      "experience | Location | derived=false",
      "experience | Start date | derived=false",
      "experience | End date | derived=false",
      "experience | Current role | derived=true",
      "experience | Bullet 1 | derived=false",
      "experience | Bullet 2 | derived=false",
      "experience | Skill 1 | derived=false",
      "experience | Metric 1 | derived=false",
      "projects | Name | derived=false",
      "projects | Link | derived=false",
      "projects | Technology 1 | derived=false",
      "projects | Bullet 1 | derived=false",
      "projects | Metric 1 | derived=false",
      "education | Degree | derived=false",
      "education | Field of study | derived=false",
      "education | School | derived=false",
      "education | Location | derived=false",
      "education | Start date | derived=false",
      "education | End date | derived=false",
      "education | Grade | derived=false",
      "education | Course 1 | derived=false",
      "publications | Title | derived=false",
      "publications | Authors | derived=false",
      "publications | Venue | derived=false",
      "publications | Date | derived=false",
      "publications | Link | derived=false",
      "patents | Title | derived=false",
      "patents | Patent number | derived=false",
      "patents | Status | derived=false",
      "patents | Date | derived=false",
      "patents | Link | derived=false",
      "skills | Programming | derived=false",
      "skills | Programming | derived=false",
      "skills | AI / ML | derived=false",
      "skills | Data & Pipelines | derived=false",
      "skills | Cloud & DevOps | derived=false",
      "skills | Tools | derived=false",
      "skills | Other | derived=false",
      "certifications | Name | derived=false",
      "certifications | Issuer | derived=false",
      "certifications | Date | derived=false",
      "certifications | Link | derived=false",
      "achievements | Achievement 1 | derived=false",
      "languages | Language | derived=false",
      "languages | Level | derived=false",
      "volunteering | Organization | derived=false",
      "volunteering | Role | derived=false",
      "volunteering | Start date | derived=false",
      "volunteering | End date | derived=false",
      "volunteering | Bullet 1 | derived=false",
    ]);
  });

  it("numbers every kind of list item by its position, not just the first one", () => {
    const two: CanonicalProfile = {
      personal: {
        name: "Pat",
        phones: [{ number: "+1 555 0100" }, { number: "+1 555 0101" }],
        links: { other: [{ label: "Blog", url: "blog.example.com" }, { label: "Talks", url: "talks.example.com" }] },
      },
      achievements: ["First win", "Second win"],
      education: [{ degree: "BSc", institution: "Example University", coursework: ["Probability", "Algebra"] }],
      volunteering: [{ organization: "Food Bank", bullets: ["Sorted donations", "Drove the van"] }],
    };
    const labels = allFields(buildReview(two, NO_SPANS)).map((field) => `${field.label}: ${field.value}`);
    expect(labels).toEqual(
      expect.arrayContaining([
        "Phone 1: +1 555 0100",
        "Phone 2: +1 555 0101",
        "Other link name 1: Blog",
        "Other link name 2: Talks",
        "Other link 2: talks.example.com",
        "Achievement 1: First win",
        "Achievement 2: Second win",
        "Course 1: Probability",
        "Course 2: Algebra",
        "Bullet 1: Sorted donations",
        "Bullet 2: Drove the van",
      ]),
    );
  });

  it("lists the sections in the order of a resume, each with its title", () => {
    expect(buildReview(FULL, NO_SPANS).map((section) => section.title)).toEqual([
      "Personal details",
      "Summary",
      "Experience",
      "Projects",
      "Education",
      "Publications",
      "Patents",
      "Skills",
      "Certifications",
      "Achievements",
      "Languages",
      "Volunteering",
    ]);
  });
});

// The server's canonical profile carries every optional field at its default, so a draft the person
// has not filled in beyond a name and a job still holds them all. This is the model's own dump of
// that minimal profile (tests/test_profile_review_defaults_fixture.py fails when it goes stale).
describe("a profile as the server sends it, every default present", () => {
  const dump = JSON.parse(serverDefaultsSource) as CanonicalProfile;

  it("has no section of values that have no label, and does not list the unset render-only flag", () => {
    const sections = buildReview(dump, NO_SPANS);
    expect(sections.map((section) => section.id)).toEqual(["personal", "experience"]);
    const fields = allFields(sections);
    expect(fields.map((field) => field.path)).not.toContain("/personal/signature");
    // every label is a label, never a raw path
    for (const field of fields) expect(field.label.startsWith("/"), field.path).toBe(false);
    expect(fields.map((field) => `${field.label}: ${field.value}`)).toEqual([
      "Name: Pat Example",
      "Show the place on the resume: No",
      "Job title: Data Engineer",
      "Company: Acme Corp",
      "Start date: 2022-01",
      "End date: present",
      "Current role: No",
    ]);
  });

  it("still shows the signature flag when it is on, and every other render-only value, with a label", () => {
    const withValues = {
      ...dump,
      personal: {
        ...dump.personal,
        signature: true,
        dob: "1990-01-01",
        marital_status: "Single",
        photo: "photo.example.com/pat.jpg",
        work_authorization_status: { US: "citizen", CA: "permit" },
      },
    };
    const fields = allFields(buildReview(withValues, NO_SPANS));
    expect(fields.map((field) => `${field.label}: ${field.value}`)).toEqual([
      "Name: Pat Example",
      "Show the place on the resume: No",
      "Date of birth: 1990-01-01",
      "Marital status: Single",
      "Work authorization status (US): citizen",
      "Work authorization status (CA): permit",
      "Photo: photo.example.com/pat.jpg",
      "Signature on the resume: Yes",
      "Job title: Data Engineer",
      "Company: Acme Corp",
      "Start date: 2022-01",
      "End date: present",
      "Current role: No",
    ]);
    expect(buildReview(withValues, NO_SPANS).map((section) => section.id)).not.toContain("other");
  });

  it("skips only the flag's unset default: a value that is not that default is shown wherever it is", () => {
    // a false at any other path is a value (the person's own, or a rule's), and so is a non-boolean
    // at the flag's path
    const fields = allFields(buildReview({ ...dump, personal: { ...dump.personal, signature: false } }, NO_SPANS));
    expect(fields.some((field) => field.label.startsWith("Show the place"))).toBe(true);
    const odd = allFields(buildReview({ ...dump, personal: { ...dump.personal, signature: "yes" as unknown as boolean } }, NO_SPANS));
    expect(odd.find((field) => field.path === "/personal/signature")?.value).toBe("yes");
  });

  it("labels a map's value by its key, even when the key looks like a list position", () => {
    const fields = allFields(
      buildReview({ ...dump, personal: { ...dump.personal, work_authorization_status: { "0": "x" } } }, NO_SPANS),
    );
    expect(fields.find((field) => field.path === "/personal/work_authorization_status/0")?.label).toBe(
      "Work authorization status (0)",
    );
  });
});

// The backend's own field names against this module's labels: a field added to the profile model
// that has no label here is a test failure instead of a raw path under "Other" on every draft.
describe("every field of the backend's profile model has a label", () => {
  function fieldsOfModels(): Map<string, string[]> {
    const models = new Map<string, string[]>();
    let current: string[] | null = null;
    for (const line of profileSource.split("\n")) {
      const header = /^class (\w+)\(BaseModel\):/.exec(line);
      if (header !== null) {
        current = [];
        models.set(header[1], current);
      } else if (/^\S/.test(line)) {
        current = null;
      } else if (current !== null) {
        const field = /^    (\w+): [\w[]/.exec(line);
        if (field !== null && field[1] !== "model_config") current.push(field[1]);
      }
    }
    return models;
  }

  const segments = new Set(REVIEW_PATH_TEMPLATES.flatMap((template) => template.split("/")));

  it("reads the model classes (a guard against this scan going blind)", () => {
    const models = fieldsOfModels();
    expect([...models.keys()]).toContain("Personal");
    expect(models.get("Personal")).toEqual(
      expect.arrayContaining(["name", "dob", "marital_status", "work_authorization_status", "photo", "signature"]),
    );
    expect(models.get("Patent")).toContain("patent_number");
    expect(models.size).toBeGreaterThanOrEqual(15);
  });

  it("has every field name of every model as a part of some label's path", () => {
    // A pin is never read from a document and is not shown on this screen.
    const notShown = new Set(["pin", "mandatory", "min_bullets"]);
    const missing: string[] = [];
    for (const [model, fields] of fieldsOfModels()) {
      for (const field of fields) {
        if (!notShown.has(field) && !segments.has(field)) missing.push(`${model}.${field}`);
      }
    }
    expect(missing).toEqual([]);
  });

  it("has every field of Personal at a path of its own under /personal", () => {
    const personalFields = fieldsOfModels().get("Personal") ?? [];
    for (const field of personalFields) {
      const own = REVIEW_PATH_TEMPLATES.some((template) => template === `/personal/${field}` || template.startsWith(`/personal/${field}/`));
      expect(own, field).toBe(true);
    }
  });
});

// What each entry of a list is called on the screen, section by section: the name of the entry is
// built from chosen values of it, so a section that lost its key or one of its name fields would
// still list every value and fail nothing else.
describe("the heading of each entry", () => {
  it("names one entry of every list section by what identifies it", () => {
    const headings = Object.fromEntries(
      buildReview(FULL, NO_SPANS)
        .filter((section) => section.blocks[0].heading !== null)
        .map((section) => [section.id, section.blocks.map((block) => block.heading)]),
    );
    expect(headings).toEqual({
      experience: ["Experience 1: Data Engineer, Acme Corp"],
      projects: ["Project 1: Widget"],
      education: ["Education 1: BSc, Example University"],
      publications: ["Publication 1: On Widgets"],
      patents: ["Patent 1: A widget"],
      certifications: ["Certification 1: Cloud Practitioner"],
      languages: ["Language 1: English"],
      volunteering: ["Volunteering 1: Food Bank, Volunteer"],
    });
  });

  it("numbers entries by their position and keeps each entry's name with its own values", () => {
    const profile: CanonicalProfile = {
      personal: { name: "Pat" },
      experience: [
        { title: "Data Engineer", company: "Acme", start_date: "2022-01", end_date: "present" },
        { title: "Analyst", company: "Beta", start_date: "2020-01", end_date: "2021-12" },
      ],
    };
    const section = buildReview(profile, NO_SPANS).find((candidate) => candidate.id === "experience");
    expect(section?.blocks.map((block) => block.heading)).toEqual(["Experience 1: Data Engineer, Acme", "Experience 2: Analyst, Beta"]);
  });

  it("leaves out a name part that is empty, instead of a stray comma or colon", () => {
    // title is the empty string and company is Acme: no ", " before it
    const profile = {
      personal: { name: "Pat" },
      experience: [{ title: "", company: "Acme", start_date: "2022-01", end_date: "present" }],
    } as CanonicalProfile;
    const section = buildReview(profile, NO_SPANS).find((candidate) => candidate.id === "experience");
    expect(section?.blocks[0].heading).toBe("Experience 1: Acme");
  });

  it("is just the kind and the number when the entry has nothing to name it by", () => {
    const profile = {
      personal: { name: "Pat" },
      experience: [
        { title: "Data Engineer", company: "Acme", start_date: "2022-01", end_date: "present" },
        { title: "", company: "", start_date: "2020-01", end_date: "2021-12" },
      ],
    } as CanonicalProfile;
    const section = buildReview(profile, NO_SPANS).find((candidate) => candidate.id === "experience");
    expect(section?.blocks[1].heading).toBe("Experience 2");
  });
});

describe("buildReview", () => {
  const profile: CanonicalProfile = {
    personal: {
      name: "Pat Example",
      headline: "Data Engineer",
      emails: [
        { address: "pat@example.com", primary: true },
        { address: "pat@work.example.com", primary: false },
      ],
      location: { city: "Springfield", show_on_resume: true },
    },
    summary_bullets: ["First line", "Second line"],
    experience: [
      {
        title: "Data Engineer",
        company: "Acme Corp",
        start_date: "2022-06",
        end_date: "present",
        is_current: true,
        bullets: ["Built pipelines", "Cut costs"],
        skills: ["Python"],
      },
      { title: "Analyst", company: "Beta LLC", start_date: "2020-01", end_date: "2022-05", is_current: false },
    ],
    skills: { programming: ["Python", "SQL"], tools: ["Git"] },
  };

  it("groups values by section, in the order of a resume", () => {
    expect(buildReview(profile, NO_SPANS).map((section) => section.id)).toEqual([
      "personal",
      "summary",
      "experience",
      "skills",
    ]);
  });

  it("labels each value and lists personal details in a natural order", () => {
    const personalSection = buildReview(profile, NO_SPANS)[0];
    expect(personalSection.blocks).toHaveLength(1);
    expect(personalSection.blocks[0].heading).toBeNull();
    expect(personalSection.blocks[0].fields.map((field) => `${field.label}: ${field.value}`)).toEqual([
      "Name: Pat Example",
      "Headline: Data Engineer",
      "Email 1: pat@example.com",
      "Email 2: pat@work.example.com",
      "Primary email 1: Yes",
      "Primary email 2: No",
      "City: Springfield",
      "Show the place on the resume: Yes",
    ]);
  });

  it("gives each experience entry its own block, named by what it is, with its values in order", () => {
    const experienceSection = buildReview(profile, NO_SPANS).find((section) => section.id === "experience");
    expect(experienceSection?.blocks.map((block) => block.heading)).toEqual([
      "Experience 1: Data Engineer, Acme Corp",
      "Experience 2: Analyst, Beta LLC",
    ]);
    expect(experienceSection?.blocks[0].fields.map((field) => `${field.label}: ${field.value}`)).toEqual([
      "Job title: Data Engineer",
      "Company: Acme Corp",
      "Start date: 2022-06",
      "End date: present",
      "Current role: Yes",
      "Bullet 1: Built pipelines",
      "Bullet 2: Cut costs",
      "Skill 1: Python",
    ]);
    expect(experienceSection?.blocks[1].fields.map((field) => field.path)).toEqual([
      "/experience/1/title",
      "/experience/1/company",
      "/experience/1/start_date",
      "/experience/1/end_date",
      "/experience/1/is_current",
    ]);
  });

  it("labels each skill by its category, in the order the categories are listed", () => {
    const skillsSection = buildReview(profile, NO_SPANS).find((section) => section.id === "skills");
    expect(skillsSection?.blocks[0].fields.map((field) => `${field.label}: ${field.value}`)).toEqual([
      "Programming: Python",
      "Programming: SQL",
      "Tools: Git",
    ]);
  });

  it("marks the values that are set by a rule and not read from the file", () => {
    const fields = allFields(buildReview(profile, NO_SPANS));
    const derived = fields.filter((field) => field.derived).map((field) => field.path);
    expect(derived).toEqual([
      "/personal/emails/0/primary",
      "/personal/emails/1/primary",
      "/personal/location/show_on_resume",
      "/experience/0/is_current",
      "/experience/1/is_current",
    ]);
  });

  it("puts a value with a path it has no label for in Other, under its own path, and drops nothing", () => {
    const odd = {
      personal: { name: "Pat", pronouns: "they/them", links: { mastodon: "x.example" } },
      experience: [{ title: "T", company: "C", start_date: "2020-01", end_date: "present", pin: { mandatory: true } }],
      colour: "blue",
    } as unknown as CanonicalProfile;
    const sections = buildReview(odd, NO_SPANS);
    const other = sections.find((section) => section.id === "other");
    expect(other?.blocks[0].fields.map((field) => [field.label, field.value])).toEqual([
      ["/personal/pronouns", "they/them"],
      ["/personal/links/mastodon", "x.example"],
      ["/experience/0/pin/mandatory", "Yes"],
      ["/colour", "blue"],
    ]);
    // all seven values are on the screen
    expect(allFields(sections)).toHaveLength(leavesOf(odd).length);
    expect(sections[sections.length - 1].id).toBe("other");
  });

  it("is safe with keys that are special to JavaScript", () => {
    const tricky = JSON.parse('{"personal":{"name":"Pat"},"__proto__":{"x":"1"},"constructor":"c"}') as CanonicalProfile;
    const fields = allFields(buildReview(tricky, NO_SPANS));
    expect(fields.map((field) => field.path).sort()).toEqual(["/__proto__/x", "/constructor", "/personal/name"].sort());
  });

  it("is an empty list for a profile with nothing but a name", () => {
    expect(allFields(buildReview({ personal: { name: "Pat" } }, NO_SPANS))).toHaveLength(1);
  });

  it("escapes a key the way a JSON pointer does, so the path is the one the server uses", () => {
    const odd = { personal: { name: "Pat" }, "a/b": { "c~d": "x" } } as unknown as CanonicalProfile;
    expect(leavesOf(odd).map((leaf) => leaf.path)).toContain("/a~1b/c~0d");
  });
});

describe("where each value came from", () => {
  const profile: CanonicalProfile = {
    personal: { name: "Pat Example" },
    experience: [{ title: "Data Engineer", company: "Acme", start_date: "2022-01", end_date: "2022-12" }],
  };

  it("is the span at the value's own path, and null where there is none", () => {
    const spans = {
      "/personal/name": { start: 6, end: 17 },
      "/experience/0/title": { start: 30, end: 43 },
    };
    const fields = allFields(buildReview(profile, spans));
    const byPath = new Map(fields.map((field) => [field.path, field.span]));
    expect(byPath.get("/personal/name")).toEqual({ start: 6, end: 17 });
    expect(byPath.get("/experience/0/title")).toEqual({ start: 30, end: 43 });
    expect(byPath.get("/experience/0/company")).toBeNull();
  });

  it("does not look a span up by anything but the exact path", () => {
    const spans = { "/personal/name/x": { start: 0, end: 3 }, "personal/name": { start: 0, end: 3 } };
    expect(allFields(buildReview(profile, spans)).every((field) => field.span === null)).toBe(true);
  });
});

describe("assumptions", () => {
  const profile: CanonicalProfile = {
    personal: { name: "Pat" },
    experience: [{ title: "T", company: "C", start_date: "2020-01", end_date: "2020-12" }],
  };
  const note = "The document shows only the year, so the month is a convention (January for a start, December for an end). Check it.";

  it("is shown beside the value it is about", () => {
    const fields = allFields(
      buildReview(profile, NO_SPANS, [
        { path: "/experience/0/start_date", value: "2020-01", note },
        { path: "/experience/0/end_date", value: "2020-12", note },
      ]),
    );
    expect(fields.filter((field) => field.assumption !== null).map((field) => field.path)).toEqual([
      "/experience/0/start_date",
      "/experience/0/end_date",
    ]);
  });

  it("is not put on a field that holds a different value now (the server numbers by the model's own list)", () => {
    const fields = allFields(
      buildReview(profile, NO_SPANS, [{ path: "/experience/0/start_date", value: "2018-01", note }]),
    );
    expect(fields.every((field) => field.assumption === null)).toBe(true);
  });

  // Not something the server sends today (a path has one note); what is shown if it ever did.
  it("shows the later note when two are given for the same value", () => {
    const fields = allFields(
      buildReview(profile, NO_SPANS, [
        { path: "/experience/0/start_date", value: "2020-01", note: "earlier note" },
        { path: "/experience/0/start_date", value: "2020-01", note: "later note" },
      ]),
    );
    expect(fields.find((field) => field.path === "/experience/0/start_date")?.assumption).toBe("later note");
  });

  it("is listed in words with the server's own note, wherever it points", () => {
    expect(
      assumptionLines([{ path: "/experience/1/start_date", value: "2020-01", note }]),
    ).toEqual([{ where: "Experience, entry 2, Start date", value: "2020-01", note }]);
  });
});

describe("describePath", () => {
  it("says where a value of the model's draft is, in words, numbering as the model did", () => {
    expect(describePath("/experience/1/bullets/2")).toBe("Experience, entry 2, Bullet 3");
    expect(describePath("/experience/0/title")).toBe("Experience, entry 1, Job title");
    expect(describePath("/personal/links/linkedin")).toBe("Personal details, LinkedIn");
    expect(describePath("/personal/emails/1/address")).toBe("Personal details, Email 2");
    expect(describePath("/skills/tools/2")).toBe("Skills, Tools, item 3");
    expect(describePath("/summary_bullets/0")).toBe("Summary, Summary line 1");
    expect(describePath("/education/0/degree")).toBe("Education, entry 1, Degree");
  });

  it("says an entry as a whole, and a field the profile has no label for", () => {
    expect(describePath("/experience/2")).toBe("Experience, entry 3");
    expect(describePath("/projects/0")).toBe("Projects, entry 1");
    expect(describePath("/personal/pronouns")).toBe("Personal details, pronouns");
    expect(describePath("/experience/0/pin/mandatory")).toBe("Experience, entry 1, pin, mandatory");
  });

  it("gives back a path it cannot place, as it is", () => {
    expect(describePath("/colour")).toBe("/colour");
    expect(describePath("/constructor/x")).toBe("/constructor/x");
    expect(describePath("not-a-pointer")).toBe("not-a-pointer");
    expect(describePath("")).toBe("The whole profile");
  });
});

describe("what was left out, in words", () => {
  function serverReasons(): string[] {
    const block = /DropReason = Literal\[([^\]]*)\]/.exec(guardSource);
    if (block === null) throw new Error("profile_import_guard.py no longer has DropReason");
    return [...block[1].matchAll(/"([a-z_]+)"/g)].map((match) => match[1]);
  }

  it("has a plain sentence for every reason the server can give, and for no empty one", () => {
    const reasons = serverReasons();
    expect(reasons).toHaveLength(10);
    const sentences = reasons.map(dropReasonText);
    for (const sentence of sentences) {
      expect(sentence.length).toBeGreaterThan(20);
      expect(sentence).not.toContain("reason:");
    }
    expect(new Set(sentences).size).toBe(sentences.length);
  });

  it("keeps the server's detail only where it says something the sentence does not", () => {
    const lines = droppedLines([
      { path: "/personal/location/city", reason: "invalid_value", detail: "a work arrangement, not a place", value: "Remote" },
      { path: "/education/0/field", reason: "duplicates_sibling", detail: "already part of the degree", value: "Statistics" },
      { path: "/personal/dob", reason: "never_from_document", detail: "this field is not filled from a document; set it in the profile editor", value: "1990" },
      { path: "/personal/x", reason: "unknown_field", detail: "not a field of the profile", value: null },
      { path: "/experience/0/start_date", reason: "date_not_in_document", detail: "this date is not in the document", value: "2020-01" },
    ]);
    expect(lines.map((line) => line.detail)).toEqual(["a work arrangement, not a place", "already part of the degree", null, null, null]);
  });

  it("says plainly that a reason it does not know is unknown, and names it", () => {
    expect(dropReasonText("brand_new_reason")).toBe(
      "It was left out by the check against your file (reason: brand_new_reason).",
    );
    expect(dropReasonText("constructor")).toContain("reason: constructor");
  });

  // A key with a "/" or a "~" in it is escaped in a JSON pointer ("~1" and "~0"), and "~01" is a
  // literal "~1", not a "/": the order the two escapes are undone in matters. Hardening for a key a
  // model invents, not something the profile's own fields have.
  it("undoes a pointer's escapes in the right order", () => {
    expect(describePath("/personal/x~01y")).toBe("Personal details, x~1y");
    expect(describePath("/personal/x~1y")).toBe("Personal details, x/y");
    expect(describePath("/personal/x~0y")).toBe("Personal details, x~y");
  });

  it("says where a render-only value was, by its label, and a map's value by its key", () => {
    expect(describePath("/personal/dob")).toBe("Personal details, Date of birth");
    expect(describePath("/personal/signature")).toBe("Personal details, Signature on the resume");
    expect(describePath("/personal/work_authorization_status/US")).toBe("Personal details, Work authorization status (US)");
    // the map as a whole, which is what the guard reports when it drops it
    expect(describePath("/personal/work_authorization_status")).toBe("Personal details, work authorization status");
  });

  it("describes each dropped value by where it was, why, the server's detail and what the model proposed", () => {
    expect(
      droppedLines([
        { path: "/skills/tools/2", reason: "not_in_document", detail: "this text is not in the document", value: "HyperWidget" },
        { path: "/experience/2", reason: "entry_incomplete", detail: "removed: no value for title could be found in the document", value: null },
        { path: "/personal/location/city", reason: "invalid_value", detail: "", value: "Remote" },
      ]),
    ).toEqual([
      {
        where: "Skills, Tools, item 3",
        why: "This text is not in your file.",
        detail: null, // the server's detail only says it again
        proposed: "HyperWidget",
      },
      {
        where: "Experience, entry 3",
        why: "It was removed because a value it cannot do without was not kept.",
        detail: "removed: no value for title could be found in the document",
        proposed: null,
      },
      {
        where: "Personal details, City",
        why: "This is not a usable value for that field.",
        detail: null,
        proposed: "Remote",
      },
    ]);
  });
});

describe("valueText", () => {
  it("shows text as it is, numbers as numbers and true/false as Yes and No", () => {
    expect(valueText("a b")).toBe("a b");
    expect(valueText(3)).toBe("3");
    expect(valueText(true)).toBe("Yes");
    expect(valueText(false)).toBe("No");
  });
});

describe("leavesOf", () => {
  it("skips what holds nothing and keeps false and 0", () => {
    expect(leavesOf({ a: "", b: null, c: [], d: {}, e: false, f: 0, g: ["", "x"] })).toEqual([
      { path: "/e", value: false },
      { path: "/f", value: 0 },
      { path: "/g/1", value: "x" },
    ]);
  });
});
