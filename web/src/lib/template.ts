// The canonical resume template -- the exact shape the spine's
// deterministic validator (profile.py) accepts. This is the same
// contract the Telegram bot sends; the two surfaces share one importer
// server-side, so this file is presentation only, never validation.
//
// Schema v1.1 (Sprint 3.1d): publications/certifications/languages/
// volunteering and links.scholar are all OPTIONAL -- shown here as
// empty so they're discoverable, but a profile with none of them is a
// completely normal v1.0-shaped resume. Fill in whichever apply; delete
// the rest. At least one of experience/projects/publications/patents/
// volunteering needs a real entry (education alone isn't enough evidence
// to generate a document from).
//
// Schema v1.2 (R5, resumeforge-shape-and-fit.md): experience/project/
// education entries accept an optional `pin` field --
// `{mandatory: true, min_bullets: <1-6 or omit>}` -- a hard guarantee that
// entry survives resume generation regardless of relevance ranking, capped
// at 6 pinned entries total. This is a MANUAL choice, never something to
// infer from resume text -- leave it out (or `pin: null`) unless you
// specifically want to force an entry's inclusion.
//
// Schema v1.3 (R7, resumeforge-shape-and-fit.md): `patents` -- the same
// citable-evidence shape `publications` already has, for a candidate whose
// most relevant output is a patent rather than a paper.

export const RESUME_TEMPLATE = {
  personal: {
    name: "<Your Name>",
    headline: "<e.g. Software Engineer>",
    emails: [{ address: "<you@example.com>", primary: true }],
    phones: [{ number: "<+1 555 0100>", primary: true, region: "US" }],
    links: { linkedin: "", github: "", portfolio: "", scholar: "" },
    location: { city: "", region: "", country: "", show_on_resume: false },
    // No visa/permit fact belongs on a resume, so it's fine (expected,
    // even) for a real resume to leave this untouched -- the placeholder
    // below has to read unmistakably as "your own words go here," never as
    // a real answer an LLM could just carry through unedited (the exact
    // failure this field guards against: see the web Profile page's own
    // "Personal details" card for the real, region-specific examples).
    work_authorization:
      "<describe your work authorization in your own words -- e.g. citizenship/visa/permit status and whether continuing here would need any employer action>",
  },
  summary_bullets: ["<one line summarizing who you are>"],
  experience: [
    {
      title: "<Job Title>",
      company: "<Company>",
      location: "<City, ST>",
      start_date: "YYYY-MM",
      end_date: "YYYY-MM or present",
      is_current: false,
      bullets: ["<what you did, with a number if you can>"],
      skills: ["<Python>"],
      metrics: [],
      // Optional, manual -- see the schema v1.2 note above. Leave null
      // unless you specifically want this entry force-included.
      pin: null as { mandatory: boolean; min_bullets?: number | null } | null,
    },
  ],
  projects: [],
  education: [
    {
      degree: "<B.S. Computer Science>",
      institution: "<University>",
      start_date: "YYYY-MM",
      end_date: "YYYY-MM",
    },
  ],
  // Optional -- for research-track profiles. Each entry cites like a
  // reference: authors as they'd appear in the citation, venue, date, url.
  publications: [] as {
    title: string;
    authors: string;
    venue: string;
    date: string;
    url: string;
  }[],
  // Optional -- for a candidate whose most relevant output is a patent
  // rather than a paper. `status` is free text as it appears on the
  // filing (e.g. "filed", "pending", "granted") -- never guessed.
  patents: [] as {
    title: string;
    patent_number: string;
    status: string;
    date: string;
    url: string;
  }[],
  skills: {
    programming: [],
    ai_ml: [],
    data_mlops: [],
    cloud_devops: [],
    tools: [],
    other: [],
  },
  certifications: [] as { name: string; issuer: string; date: string; url: string }[],
  achievements: [],
  languages: [] as { language: string; fluency: string }[],
  volunteering: [] as {
    organization: string;
    role: string;
    start_date: string;
    end_date: string;
    bullets: string[];
  }[],
};

export const CONVERSION_PROMPT = `Fill in this JSON template with my real resume information.

Rules:
- Use ONLY facts from my actual resume below -- never invent, embellish, or guess anything.
- Dates must be YYYY-MM format ("present" is allowed as an end_date).
- If something on my resume doesn't fit a field, leave the field as-is or empty rather than forcing it.
- publications/patents/certifications/languages/volunteering are optional -- only fill in the
  ones that apply to me. If I have none, leave those as empty arrays.
- Leave every "pin" field as null -- that's a manual choice I make myself afterward, not
  something to infer from my resume text.
- Return ONLY the filled JSON, nothing else.

Template:
${JSON.stringify(RESUME_TEMPLATE, null, 2)}

My resume:
[paste your resume here]`;
