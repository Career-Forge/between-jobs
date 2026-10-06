// The Privacy Policy and the Terms of Service, as typed data. The pages (pages/Privacy.tsx,
// pages/Terms.tsx) only draw it, and legal.test.ts pins its structure.
//
// WHAT IS WRITTEN HERE MUST BE TRUE OF THE CODE AS IT IS. Every statement was checked against
// the source at the version below; when the code changes, re-check the text, and when the text
// changes, bump LEGAL_VERSION (a later change records which version each person agreed to).
// Where something could not be verified from this repository, it is worded so that it stays
// true, and the maintainers' list of open questions says so.
//
// PRODUCT USAGE EVENTS AND THE TESTER PROGRAMME ARE DESCRIBED COLUMN BY COLUMN under 'What we
// store about you': each paragraph names every recorded column of its table in plain words, says
// when the record is deleted, and reconciles with 'What you browse'. legal.test.ts reads the
// migrations (the one that creates each table, and any later `alter table ... add / drop / rename
// column`) and fails the moment either table has a column of any type that the text does not name,
// or another table that records what people do or whether they joined a programme exists without a
// paragraph of its own -- the change that adds one must add its paragraph here, on purpose.

import { PRIVACY_EMAIL, REPO_URL, SOURCE_LICENSE, SUPPORT_EMAIL } from "./site";

// The version a later consent record points at. Changing the text of either document means a
// new value here, and a new effective date and "what changed" line below.
//
// FIRST PUBLISH: the date below is the day the text was written, not the day it is first
// served. Before the pages go live, set LEGAL_VERSION and the date (iso and label) to that day,
// and update the two literals pinned in legal.test.ts. Once the text has been served, a change
// to it gets a new version and date as described above.
export const LEGAL_VERSION = "2026-10-05";
export const LEGAL_EFFECTIVE_DATE = { iso: "2026-10-05", label: "October 5, 2026" } as const;
export const LEGAL_WHAT_CHANGED = "First version.";

// When the operator would look at a person's data. The Privacy Policy says this to everyone and the
// Tester Agreement says it to testers, and the two must say the same thing: a tester who is also
// disclosing something sensitive must not be told a weaker limit than every other user. One
// constant, used by both, so they cannot drift apart. Changing it changes both documents, so it
// means a new LEGAL_VERSION and a new TESTER_AGREEMENT_VERSION (and legal.test.ts pins the literal,
// so the change cannot be made by accident).
export const OPERATOR_ACCESS_LIMIT =
  "We would look at it only to fix a problem you ask us to help with, to investigate abuse or a security problem, or because the law requires it.";

// ── the shape of a document ────────────────────────────────────────────────

// Text that is part plain and part link. A link is either a route inside the app (`to`), an
// https address (`href`), or one of the two contact addresses (`email`).
export type Inline =
  | string
  | { to: string; text: string }
  | { href: string; text: string }
  | { email: string };

export type Block =
  | { kind: "p"; inline: readonly Inline[] }
  | { kind: "h3"; text: string }
  | { kind: "ul"; items: readonly (readonly Inline[])[] }
  | { kind: "defs"; items: readonly { term: string; detail: readonly Inline[] }[] };

export interface Section {
  // The anchor, and the key the tests use to find the section.
  id: string;
  heading: string;
  blocks: readonly Block[];
}

export interface LegalDocument {
  title: string;
  sections: readonly Section[];
}

const p = (...inline: Inline[]): Block => ({ kind: "p", inline });
const h3 = (text: string): Block => ({ kind: "h3", text });
const ul = (...items: (string | Inline[])[]): Block => ({
  kind: "ul",
  items: items.map((item) => (typeof item === "string" ? [item] : item)),
});
const defs = (...items: { term: string; detail: Inline[] }[]): Block => ({ kind: "defs", items });
const def = (term: string, ...detail: Inline[]) => ({ term, detail });

const privacyEmail: Inline = { email: PRIVACY_EMAIL };
const supportEmail: Inline = { email: SUPPORT_EMAIL };
const terms: Inline = { to: "/terms", text: "Terms of Service" };
const policy: Inline = { to: "/privacy", text: "Privacy Policy" };

// ── who else handles your data ─────────────────────────────────────────────

export type ProcessorGroup = "service" | "model" | "search" | "public" | "optional";

export interface Processor {
  // The key the content test's evidence map uses, so keep it stable.
  name: string;
  group: ProcessorGroup;
  detail: readonly Inline[];
}

export const PROCESSOR_GROUPS: readonly { id: ProcessorGroup; title: string; intro: string }[] = [
  {
    id: "service",
    title: "Services that run Between Jobs",
    intro: "These are involved whenever you use the product.",
  },
  {
    id: "model",
    title: "Your AI provider",
    intro:
      "Between Jobs has no AI provider of its own. You choose one by saving your own key, and you pay it directly.",
  },
  {
    id: "search",
    title: "Search and research providers",
    intro:
      "These are optional and use your own keys. A provider only receives data when you use a feature that needs it, and only if you saved a key for it. Saving a key sends that provider one small test request.",
  },
  {
    id: "public",
    title: "Public job sources",
    intro: "Our servers ask these for public pages. None of these requests carries your account or your profile.",
  },
  {
    id: "optional",
    title: "Only if you use the feature",
    intro: "These are involved only if you connect or use the thing named.",
  },
];

// NEW PROCESSORS MUST BE ADDED HERE BEFORE THEY ARE SWITCHED ON. A service that receives data
// about a person and is not in this list makes the Privacy Policy untrue. legal.test.ts keeps
// an evidence map from every name below to the source file that calls it; adding an entry here
// without adding its evidence there fails the test, on purpose.
export const PROCESSORS: readonly Processor[] = [
  {
    name: "Supabase",
    group: "service",
    detail: [
      "Our database, sign-in provider and file storage. It holds everything listed under 'What we store about you', including the files we prepare for you, and it handles your password.",
    ],
  },
  {
    name: "Railway",
    group: "service",
    detail: ["Runs the Between Jobs API, the server that does the work when you use the app."],
  },
  {
    name: "Cloudflare",
    group: "service",
    detail: [
      "Serves the website you load in your browser. It also receives the email you send to our privacy and support addresses and forwards it to the operator's mailbox.",
    ],
  },
  {
    name: "Resend",
    group: "service",
    detail: [
      "Sends the confirmation and password-reset emails that our sign-in provider generates for your account.",
    ],
  },
  {
    name: "Resume engine",
    group: "service",
    detail: [
      "A private service run by the operator that writes and scores resumes and cover letters, builds interview-practice context and previews your resume header. With each request it receives your profile, the job posting text, and the AI key and model you chose, and it uses the key to call your AI provider for that one request. The engine does not store your key. It is not part of the open-source repository yet.",
    ],
  },
  {
    name: "PDF renderer",
    group: "service",
    detail: [
      "A small service run by the operator that receives the LaTeX source of a resume or cover letter and returns a PDF. It writes the source to a temporary folder that is removed when the PDF is done.",
    ],
  },
  {
    name: "OpenRouter",
    group: "model",
    detail: [
      "The AI provider Between Jobs supports today. You save your own OpenRouter key and pick a model, and OpenRouter passes each request to that model. What it receives depends on the feature, and is listed just below. Saving the key sends OpenRouter a request to check it.",
    ],
  },
  {
    name: "You.com",
    group: "search",
    detail: [
      "Search queries for Discover (keywords, location, company names), for company, contact and event research (a company name, role words and, for event research, the city from your profile) and for Hiring signals.",
    ],
  },
  {
    name: "Firecrawl",
    group: "search",
    detail: [
      "The same searches as You.com, and the web address of a posting you add by URL, so it can fetch that page.",
    ],
  },
  {
    name: "Serper",
    group: "search",
    detail: ["Search queries for Discover and for Hiring signals."],
  },
  {
    name: "Brave Search",
    group: "search",
    detail: ["Search queries for Discover and for Hiring signals."],
  },
  {
    name: "JSearch (RapidAPI)",
    group: "search",
    detail: ["Job search queries from Discover. JSearch is hosted by RapidAPI, so requests go to RapidAPI."],
  },
  {
    name: "Adzuna",
    group: "search",
    detail: ["Job search queries from Discover (keywords and place), with your Adzuna app id and key."],
  },
  {
    name: "USAJobs",
    group: "search",
    detail: [
      "Job search queries from Discover, with your key and the email address you registered with USAJobs, which it requires on every request.",
    ],
  },
  {
    name: "Apollo",
    group: "search",
    detail: ["The name and company of a contact, when you click to look up their work email address."],
  },
  {
    name: "Hunter",
    group: "search",
    detail: ["The name and company of a contact, when you click to look up their work email address."],
  },
  {
    name: "Exa",
    group: "search",
    detail: [
      "The name, company and, when we have one, the job title of a contact, when you click to look for their LinkedIn profile address.",
    ],
  },
  {
    name: "Employer job boards",
    group: "public",
    detail: [
      "We collect public job listings from employers' job boards (for example those run on Greenhouse, Lever, Ashby, Workday, SmartRecruiters, Workable and Recruitee, and the careers sites of Amazon, Apple, D. E. Shaw and Google) into a shared catalog that Discover searches.",
    ],
  },
  {
    name: "RemoteOK and Arbeitnow",
    group: "public",
    detail: [
      "When you search in Discover we read these two public job feeds. Your search words are not sent to them: we fetch the feed and filter it ourselves.",
    ],
  },
  {
    name: "Listing pages",
    group: "public",
    detail: [
      "To check that a listing found by a search is still open, our server requests the listing's own page, or its job board's public interface (including LinkedIn's public job-page interface for a LinkedIn listing).",
    ],
  },
  {
    name: "GitHub",
    group: "public",
    detail: [
      "Contact research asks GitHub's public interface for the public members of a company's organization, using only the company name.",
    ],
  },
  {
    name: "Google",
    group: "optional",
    detail: [
      "If you sign in with Google, Google confirms who you are to our sign-in provider. If you connect Gmail, we use Google's Gmail interface for your drafts and their replies, as the Gmail section below explains.",
    ],
  },
  {
    name: "Telegram",
    group: "optional",
    detail: [
      "If you use the Between Jobs Telegram bot, your messages to it and its replies travel through Telegram, and Telegram's own privacy policy covers what it does with them.",
    ],
  },
  {
    name: "LinkedIn",
    group: "optional",
    detail: [
      "If you open a post in Hiring signals, your browser loads LinkedIn's public embed of that post, so LinkedIn sees that request as it would from any page that embeds its posts. Our servers do not fetch LinkedIn pages for Hiring signals.",
    ],
  },
];

function processorBlocks(): Block[] {
  const blocks: Block[] = [];
  for (const group of PROCESSOR_GROUPS) {
    blocks.push(h3(group.title), p(group.intro));
    blocks.push(
      defs(
        ...PROCESSORS.filter((processor) => processor.group === group.id).map((processor) =>
          def(processor.name, ...processor.detail),
        ),
      ),
    );
    if (group.id === "model") {
      blocks.push(
        p("What your AI provider receives, by feature:"),
        ul(
          "Resumes, cover letters, the Tailor panel and the gap interview: your profile and the job posting text, sent through the resume engine, and for the gap interview the answers you type.",
          "Job fit scores in Discover and for saved searches: a text summary of your profile (name, headline, location, work-authorization note, summary, recent roles, projects, skills and education, but not your email address or phone number) and, for each job, its title, company, location and a short excerpt. Saved searches run in the background every few hours, without you clicking, and use your key when they do.",
          "Importing a resume file (PDF or DOCX): the text read from the file, which is your resume as you wrote it, so the model can fill in a draft profile for you to review. We do not keep the file itself.",
          "Company research: excerpts of public web pages found by your search provider, which the model turns into short claims.",
          "Contact research, event research and outreach drafts: the company, a contact's name and job title, the role, and excerpts of public web pages. They do not send your profile. In event research the AI provider sees only the excerpts of public pages: your city goes to your search provider as part of the search words and is not sent to the AI provider.",
          "Positioning briefs: your skills as matched against a posting, and the company research you already have.",
          "Interview practice: questions are written from the job's company, title and description, evidence in your resume and, where there is one, a company-level summary of how interviews there are run. The answers you type are sent for scoring and feedback along with the question and your resume evidence.",
          "The browser extension's Draft answer: the question's text, a summary of your profile and the job posting text, and then the draft again for a check.",
          "Gmail replies (only if you connect Gmail): the text of the newest reply in a thread of a draft we created, so it can be classified. This also runs in the background.",
        ),
      );
    }
  }
  return blocks;
}

// ── the Privacy Policy ─────────────────────────────────────────────────────

export const PRIVACY: LegalDocument = {
  title: "Privacy Policy",
  sections: [
    {
      id: "short-version",
      heading: "The short version",
      blocks: [
        ul(
          "We store what you give Between Jobs: your profile, the jobs you track and the documents it prepares for you. We store it so the service can work for you.",
          "AI features run on your own keys. To do the work, Between Jobs sends the text a feature needs, such as parts of your profile and the job posting, to the AI provider you chose.",
          "Between Jobs never submits an application for you and never sends an email from your mailbox. You do the final step.",
          "There are no ads, no payment details, no third-party analytics and no tracking scripts.",
          "You can delete your account yourself on the Profile page. It removes your account and its data straight away, with the exceptions listed under 'Keeping and deleting your data'.",
          "The person who runs the service can technically read what is in the database. 'What the operator can see' says what that means.",
        ),
      ],
    },
    {
      id: "who-we-are",
      heading: "Who runs Between Jobs",
      blocks: [
        p(
          "Between Jobs is the service at between-jobs.tech, with its web app, API, browser extension and Telegram bot. In this policy 'we' and 'us' mean whoever operates it, and 'the operator' means the same. You can reach us at ",
          privacyEmail,
          ".",
        ),
      ],
    },
    {
      id: "what-we-store",
      heading: "What we store about you",
      blocks: [
        p(
          "Everything below is tied to your account. It is removed when your account is deleted, except where 'Keeping and deleting your data' says otherwise.",
        ),
        defs(
          def(
            "Your account",
            "Your email address and how you sign in. Your password goes from your browser straight to our sign-in provider, Supabase, and our API never receives it. If you sign in with Google, Google confirms your identity to the sign-in provider.",
          ),
          def(
            "Your profile",
            "The resume content you import or edit: name, headline, contact details, links, work history, projects, education, skills and the rest of the resume template. Optional fields such as a work-authorization note, date of birth, nationality or marital status are stored only if you fill them in. Each version you activate is kept, together with a list of facts we derive from it.",
          ),
          def(
            "Jobs and applications",
            "The postings you add or track (title, company, location, web address and posting text), the stage each application is in, and a history of changes to it.",
          ),
          def(
            "Documents we prepare",
            "The resume and cover letter generated for each application, kept as LaTeX source in file storage and turned into a PDF when you ask for one, plus your layout choices and the fit and quality checks produced with them.",
          ),
          def(
            "Searches and your Today feed",
            "Saved searches (keywords, location, company names and the remote-only setting); items on your Today feed, including strong job matches found by saved searches, with the job's title, company, location, link and a short excerpt; and Hiring-signals searches and saved posts (the post's web address and id, and the search that found it, not the post's text).",
          ),
          def(
            "Research you ask for",
            "Company research (short claims, with the titles and addresses of the public web pages they came from); contact research (names, job titles and public evidence for people at a company, and a work email address or profile link if you run a lookup); event research (events and speakers); the outreach drafts we help you write; and positioning briefs.",
          ),
          def(
            "Interview practice",
            "The practice questions, the answers you type, our written feedback and score for each answer, and the resume evidence the questions were written from.",
          ),
          def(
            "Your connections",
            "Your provider keys, and your Gmail connection if you make one, stored encrypted. We also keep which AI provider and model you chose as your default, in plain text next to the encrypted key. If you link Telegram, your numeric Telegram user id and a count of failed link-code attempts from it, and nothing else about your Telegram account: no name or username.",
          ),
          def(
            "Answers for the browser extension",
            "The question text and the answer you approved, when you press 'Fill & remember' in the extension.",
          ),
          def(
            "Product usage events",
            "A short record each time you use a main feature, so we can tell whether the product works for people. It says which feature you used (a search, preparing a resume or cover letter, downloading a PDF, a setup problem, or a form fill by the browser extension), which kind of feature it was (for example company research or interview practice), which application it concerned, which job-site platform it was (for example Greenhouse), whether it worked, a few counts (such as how many results were found or how many form fields were filled), how long it took, and when. It never contains what you typed, your resume or any job text, a web address, your IP address or details of your browser, and it is not sent to any analytics service. The browser extension does not send these records yet; when it does, the only thing it will report is how many fields it tried and filled, never their values.",
          ),
          def(
            "Tester programme",
            "Only if you join the tester programme; nothing is recorded here unless you do. It holds the role you chose (from a fixed list), your seniority band, your answer to an optional question about whether you would need an employer to sponsor your right to work (you can decline it, and the product never uses it: it is only counted in our reports on the programme), which version of the Tester Agreement you accepted, when you accepted it, when you withdrew if you did, and the day the record was first made. We use it to see how people in each role and level get on. It is deleted with your account, and in any case 30 days after the programme ends unless you have agreed that we may keep it. The Tester Agreement, which you read before you join, describes it in full.",
          ),
          def(
            "Technical records",
            "Counters that limit how often you can use costly features, a record of when you signed the browser extension out, short-lived codes for linking Telegram, a one-time token that ties a Gmail connect attempt to your account (valid for 10 minutes and removed when the attempt comes back, but kept until you delete your account if it never does), the numbered lists of applications the Telegram bot shows you when you ask it to list them (stored as application ids, so that 'apply to #3' means the one you saw: each is valid for 30 minutes, and it is kept until you delete your account), and a queue of internal events that feeds your Today feed.",
          ),
        ),
        h3("What we do not store"),
        ul(
          "Your password. Sign-in is handled by Supabase and our API never receives it.",
          "Your provider keys or Gmail connection in readable form. They are encrypted in the database (see 'Security').",
          "Your inbox. If you connect Gmail we keep the connection, and for each reply we classify a short quoted excerpt and the message id (see 'Gmail').",
          "Payment details. Between Jobs takes no payments.",
          "What you browse. The browser extension keeps no browsing history. On its four supported sites it sends only the address of a page that has an application form, and a little more when you press a button (see 'The browser extension'). Product usage events hold no web addresses.",
        ),
      ],
    },
    {
      id: "who-else",
      heading: "Who else handles your data",
      blocks: [
        p(
          "We list a service here only if Between Jobs calls it today, and we will add any new one here before we switch it on.",
        ),
        ...processorBlocks(),
        h3("Data that is not tied to your account"),
        p(
          "A few kinds of data are shared between users and carry no account id: the catalog of public job listings, the text of postings that people add (the same posting added twice is stored once), company-level summaries of how interviews at a company are run (built from public web pages), and a cache of search results that is deleted after about a day. Because none of it is tied to you, deleting your account does not remove it.",
        ),
      ],
    },
    {
      id: "operator-access",
      heading: "What the operator can see",
      blocks: [
        p(
          "The Between Jobs API reaches the database with a privileged role (Supabase's 'service role'), which the per-user access rules do not limit. So the operator can technically read every table and every stored file, and can decrypt your saved provider keys and Gmail connection, because the API has to decrypt them to use them for you.",
        ),
        p(
          `We do not read your data as a matter of routine. ${OPERATOR_ACCESS_LIMIT} We do not sell your data and we do not use it for advertising.`,
        ),
        p(
          "The API's own logs are written to carry request ids, route names, counts and error types, and to leave out request bodies, keys, resume and job text, and email contents. Our hosting providers keep their own logs of connections and, like any host, see the IP address of each connection.",
        ),
      ],
    },
    {
      id: "gmail",
      heading: "Gmail (optional)",
      blocks: [
        ul(
          "Connecting Gmail is optional. If you do, Between Jobs asks Google for two permissions: 'gmail.compose', to create drafts, and 'gmail.readonly', to read the thread of a draft it created.",
          "Between Jobs only creates drafts. Nothing in its code calls Gmail's send function, and you press Send yourself. Google describes the compose permission as covering drafts and sending, so this is a limit of what Between Jobs does, not of what the permission allows.",
          "Roughly every 15 minutes a background job reads the thread of each draft Between Jobs created, to see whether you sent it and whether anyone replied. It does not read anything else in your inbox.",
          "It sends the text of the newest reply to your AI provider, which classifies it (for example an interview request or a rejection). We keep the classification, the passages quoted as evidence, the message id, and the ids of the draft and thread. We do not keep the reply itself.",
          "When the classification is confident it moves the application to the matching stage. Otherwise it puts a suggestion on your Today feed for you to review.",
          "The connection is stored encrypted. 'Disconnect Gmail' on the Integrations page deletes our copy of it. To also withdraw Between Jobs' access at Google, use your Google account's permissions page. Deleting your account asks Google to revoke it for you.",
        ),
      ],
    },
    {
      id: "telegram",
      heading: "Telegram (optional)",
      blocks: [
        ul(
          "If you message the Between Jobs bot we create an account for your Telegram user id if you do not have one. You can link it to a web account with a code, and the data it collected is merged into that account.",
          "The bot receives what you send it: resumes, job postings and commands. It replies through Telegram, sends you the resume PDF you ask for, and tells you when a saved search finds a strong match.",
          "We store your numeric Telegram user id and a count of failed link-code attempts from it, and nothing else about your Telegram account. '/unlink' detaches it.",
          "If you delete your web account and message the bot again, it starts a new, empty account.",
        ),
      ],
    },
    {
      id: "extension",
      heading: "The browser extension",
      blocks: [
        ul(
          "The Chrome extension helps fill in job application forms. It runs only on jobs.lever.co, job-boards.greenhouse.io, boards.greenhouse.io and jobs.ashbyhq.com. It asks Chrome for the 'storage' and 'sidePanel' permissions and for access to those four sites, and nothing else.",
          "It reads the page to find the application form, its fields and its questions. Apart from the page address described next and the few things sent only when you press a button, it does not send page contents, what you type, or the other pages you visit.",
          "Signed in as you, whenever it finds an application form on one of those sites it sends that page's address (without any query string or fragment) to our API to check whether you track that job. This happens whether or not you do. The API answers with the application's id or nothing, does not store the address and keeps it out of its own logs. The address does travel in the request's query string, so it may appear in the connection logs of our hosting providers. Once it has found a tracked application it sends the application's id to fetch your contact details and your prepared documents. Only when you press the matching button does it send the text of one custom question (to look for a saved answer or to draft one), an answer you chose to remember, or a request to mark the application as applied.",
          "Draft answer uses your AI provider, as described above.",
          "It never clicks a submit button, never ticks a checkbox and fills a form only when you press a button. Questions about gender, race, disability, veteran status and similar self-identification topics are never listed, drafted or filled. It recognizes them by their wording, so read the form before you submit.",
          "On your device it keeps your sign-in in the browser's session storage for extensions (pages you visit cannot read it, and Chrome clears it when the browser restarts), and in local storage the highest field-map version it has accepted and whether you have agreed to its first-run notice, until you uninstall it.",
          "It has no analytics of its own and sends no usage events yet. Later it may report, as plain counts, how many form fields a fill tried and completed (see 'Product usage events'), never the values, the questions or the page. 'Sign out' in its panel ends its session and tells the API to reject the tokens it had issued.",
        ),
      ],
    },
    {
      id: "browser-storage",
      heading: "Cookies and browser storage",
      blocks: [
        p(
          "The website keeps your sign-in session, and a few small notes for the first-run checklist, in your browser's local storage. Its own code sets no cookies and loads no analytics or advertising scripts.",
        ),
      ],
    },
    {
      id: "keeping-deleting",
      heading: "Keeping and deleting your data",
      blocks: [
        ul(
          "We keep your data while your account exists.",
          "The tester programme is an exception to that line. If you joined it, we delete your enrollment record and your usage records 30 days after the programme ends, even if your account stays, unless you have agreed that we may keep them. 'Delete my account' removes them straight away, as it does everything else.",
          "'Delete my account' on the Profile page removes your account and everything tied to it, immediately and for good: your profile, applications, documents and the files stored for them, saved searches, keys, connections, practice sessions, research, product usage events, tester programme enrollment and the rest of what is listed above. It also asks Google to revoke a Gmail connection, signs out your sessions and the browser extension, clears Telegram link-attempt counters and removes your entries from our sign-in provider's audit log. Those extra steps are made on a best-effort basis.",
          [
            "You can also ask us to delete your account by email at ",
            privacyEmail,
            ". We will do it within 7 days.",
          ],
          "You can delete saved searches, saved Hiring-signals posts and searches, a pending profile import, your provider keys and your Gmail connection yourself. There is no button yet to delete a single application or document: ask us by email, or delete the whole account.",
          "Some data lives only briefly by design: a link code is valid for 10 minutes, a cached search result is deleted after about 24 hours, and the numbers Telegram assigns to each update it sends the bot (kept only so that the same update is not processed twice) are removed once they are older than 7 days, as a side effect of the bot receiving later updates.",
        ),
        h3("What is not removed when you delete your account"),
        ul(
          "Drafts we already created in your Gmail. They are in your own mailbox, and stay there until you delete them.",
          "The shared data described under 'Who else handles your data', which is not tied to your account.",
          "Backups. If our database provider keeps backups, deleted data stays in them until they expire.",
          "Logs kept by our providers outside our database (Supabase and our hosting providers), which follow their own retention.",
          "Anything your AI and search providers kept under their own terms.",
        ),
      ],
    },
    {
      id: "security",
      heading: "Security",
      blocks: [
        ul(
          "Your provider keys and your Gmail connection are encrypted in the database. The key that encrypts them is held in the database's vault, separate from the encrypted values.",
          "Every table has row-level security switched on, and every API route that reads or changes your data checks who you are. The few routes that cannot carry your sign-in token have their own checks: the health check returns no user data, the Google redirect that completes a Gmail connection is matched to you by a one-time code, and the Telegram webhook accepts only requests that carry our secret.",
          "The website's production build and the browser extension's store package are built to talk to our API over HTTPS, and both builds are refused at build time if they point at a plain-HTTP or local address. The HTTPS connection itself is provided by our hosting platforms.",
          "The API limits how often each account can use the costly features.",
          [
            "No system is perfectly secure. If you find a security problem, please tell us at ",
            supportEmail,
            ".",
          ],
        ),
      ],
    },
    {
      id: "your-rights",
      heading: "Your rights and choices",
      blocks: [
        ul(
          "You can see and change your profile in the app, and download it with 'Export JSON' on the Profile page. There is no one-click download of everything else yet. You can ask us for a copy of the data we hold about you by email, and we aim to answer within 30 days.",
          [
            "You can ask us to correct or delete your data, or to stop using it in a particular way, at ",
            privacyEmail,
            ". Depending on where you live, for example the EU, the UK or California, you may have legal rights to access, correct, delete, export or object to our use of your data, and we will honor them as the law requires.",
          ],
          "You decide which AI and search providers receive your data by choosing which keys to save. Remove a key and the features that need it stop working.",
        ),
      ],
    },
    {
      id: "children",
      heading: "Children",
      blocks: [
        p(
          "Between Jobs is for people aged 16 and over. We do not knowingly collect data from anyone younger. If you think a child has an account, tell us at ",
          privacyEmail,
          " and we will delete it.",
        ),
      ],
    },
    {
      id: "changes",
      heading: "Changes to this policy",
      blocks: [
        p(
          "We change the version and the effective date at the top of this page whenever the policy changes, and the 'What changed' line says what is different. The earlier versions are in the history of ",
          { href: REPO_URL, text: "the source repository" },
          ".",
        ),
      ],
    },
    {
      id: "contact",
      heading: "Contact",
      blocks: [
        p("Privacy questions and requests, including access, correction and deletion: ", privacyEmail, "."),
        p("Anything else, including bug reports: ", supportEmail, "."),
        p("The ", terms, " are the other half of how Between Jobs works."),
      ],
    },
  ],
};

// ── the Terms of Service ───────────────────────────────────────────────────

export const TERMS: LegalDocument = {
  title: "Terms of Service",
  sections: [
    {
      id: "agreement",
      heading: "What these terms cover",
      blocks: [
        p(
          "These terms apply when you create an account or use Between Jobs: the website at between-jobs.tech, its API, the browser extension and the Telegram bot. If you do not agree to them, do not use it. You must be at least 16 years old. How we handle your data is in the ",
          policy,
          ".",
        ),
      ],
    },
    {
      id: "beta",
      heading: "It is a beta, provided as is",
      blocks: [
        p(
          "Between Jobs is early software. It is provided 'as is' and 'as available'. Features change, break and go away, and data can be lost. Keep your own copy of anything that matters: the Profile page has 'Export JSON' for your profile.",
        ),
      ],
    },
    {
      id: "your-responsibility",
      heading: "What you are responsible for",
      blocks: [
        ul(
          "The accuracy of your profile, and of everything you send to an employer. Between Jobs drafts and prepares. It never submits an application for you, and you always do that step.",
          "Reading what it prepares. AI writes drafts that can be wrong or can overstate things. Check every claim before you use it.",
          "Following the rules of the sites you use. If you use the browser extension or copy material from a job board, you stay bound by that site's own terms.",
          "Keeping your account and your keys safe.",
        ),
      ],
    },
    {
      id: "your-keys",
      heading: "Your own keys",
      blocks: [
        ul(
          "Between Jobs runs on your own AI and search keys. The providers charge you directly for what you use, and what you trigger can cost money. We do not bill you for them and we are not responsible for their charges.",
          "You must have the right to use any key you save.",
          "We limit how often an account can use the costly features, to protect the service. Those limits do not cap what your providers charge.",
        ),
      ],
    },
    {
      id: "acceptable-use",
      heading: "Acceptable use",
      blocks: [
        p("Do not:"),
        ul(
          "scrape or bulk-collect data from the service, or resell it;",
          "get around limits, security or access rules, or interfere with how the service runs;",
          "upload or generate content that is illegal or that infringes someone else's rights;",
          "use someone else's account or someone else's keys.",
        ),
      ],
    },
    {
      id: "your-content",
      heading: "Your content",
      blocks: [
        p(
          "What you put into Between Jobs stays yours. You give us permission to store it and process it to run the service for you, including by sending it to the providers you choose, as the ",
          policy,
          " describes.",
        ),
      ],
    },
    {
      id: "suspension",
      heading: "Suspension and ending",
      blocks: [
        p(
          "You can stop at any time and delete your account on the Profile page. We can suspend or close an account that breaks these terms or puts the service or other people at risk.",
        ),
      ],
    },
    {
      id: "open-source",
      heading: "Open source",
      blocks: [
        p(
          "The source code of the web app, the API and the browser extension is published under the ",
          SOURCE_LICENSE,
          " license at ",
          { href: REPO_URL, text: "the source repository" },
          ". The license covers the code. It does not cover the data held by the hosted service, which is yours, and parts of the hosted service, such as the resume engine, are not in that repository.",
        ),
      ],
    },
    {
      id: "no-warranty",
      heading: "No warranty",
      blocks: [
        p(
          "To the extent the law allows, we make no promise that Between Jobs will be uninterrupted or error free, that anything it writes or scores is accurate, or that it will get you an interview or a job.",
        ),
      ],
    },
    {
      id: "liability",
      heading: "Limit of liability",
      blocks: [
        p(
          "To the extent the law allows, we are not liable for indirect or consequential losses, such as a missed opportunity, lost data or provider charges, and our total liability to you for anything arising from the service is limited to the amount you paid us for it in the 12 months before the claim.",
        ),
      ],
    },
    {
      id: "changes",
      heading: "Changes",
      blocks: [
        p(
          "We may change these terms. We update the version and the effective date at the top of this page and say what changed. If you keep using Between Jobs after a change, you accept the new terms.",
        ),
      ],
    },
    {
      id: "governing-law",
      heading: "Governing law",
      blocks: [
        p(
          "These terms are governed by the laws of the State of New Jersey, USA, without regard to its conflict-of-law rules.",
        ),
      ],
    },
    {
      id: "contact",
      heading: "Contact",
      blocks: [
        p("Questions about these terms and bug reports: ", supportEmail, ". Privacy requests: ", privacyEmail, "."),
      ],
    },
  ],
};
