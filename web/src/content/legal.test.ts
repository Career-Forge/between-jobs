import { describe, expect, it } from "vitest";
import registryAdapters from "../../../src/between_jobs/api/job_registry_adapters.py?raw";
import atsLiveness from "../../../src/between_jobs/api/ats_liveness.py?raw";
import contactEnrichment from "../../../src/between_jobs/api/contact_enrichment.py?raw";
import contactResearch from "../../../src/between_jobs/api/contact_research.py?raw";
import credentialsRoutes from "../../../src/between_jobs/api/credentials_routes.py?raw";
import forgeEnginesClient from "../../../src/between_jobs/api/forge_engines_client.py?raw";
import gmailClient from "../../../src/between_jobs/api/gmail_client.py?raw";
import gmailReplyChecker from "../../../src/between_jobs/api/gmail_reply_checker.py?raw";
import hiringSignalCache from "../../../src/between_jobs/api/hiring_signal_cache.py?raw";
import llmClient from "../../../src/between_jobs/api/llm_client.py?raw";
import researchClients from "../../../src/between_jobs/api/research_clients.py?raw";
import searchProviders from "../../../src/between_jobs/api/search_providers.py?raw";
import supabaseClient from "../../../src/between_jobs/api/supabase_client.py?raw";
import telegramClient from "../../../src/between_jobs/api/telegram_client.py?raw";
import latexCompiler from "../../../latex-service/src/latex_service/compiler.py?raw";
import latexServiceClient from "../../../src/between_jobs/api/latex_service_client.py?raw";
import linkCodes from "../../../src/between_jobs/api/link_codes_store.py?raw";
import telegramDedup from "../../../supabase/migrations/20261003120448_telegram_update_dedup.sql?raw";
import loggingSetup from "../../../src/between_jobs/api/logging_setup.py?raw";
import extensionStorePrivacy from "../../../extension/store/PRIVACY.md?raw";
import webViteConfig from "../../vite.config.ts?raw";
// The extension's own README, not its wxt.config.ts: Vite in this package cannot load the
// extension's TypeScript (its tsconfig comes from a generated folder). The README's permission
// list is the one the store listing is written from.
import extensionReadme from "../../../extension/README.md?raw";
import accountCard from "../components/AccountCardView.tsx?raw";
import integrations from "../pages/Integrations.tsx?raw";
import profile from "../pages/Profile.tsx?raw";
import authSource from "../auth.tsx?raw";
import hiringSignalsLib from "../lib/hiringSignals.ts?raw";
import legalSource from "./legal.ts?raw";
import { PRIVACY_PATH, TERMS_PATH } from "../lib/publicRoutes";
import {
  LEGAL_EFFECTIVE_DATE,
  LEGAL_VERSION,
  LEGAL_WHAT_CHANGED,
  PRIVACY,
  PROCESSORS,
  PROCESSOR_GROUPS,
  TERMS,
  type Block,
  type Inline,
  type LegalDocument,
} from "./legal";
import { LANDING } from "./landing";
import { PRIVACY_EMAIL, REPO_URL, SUPPORT_EMAIL } from "./site";

// Every module of the API, and every migration, as text: the guards below read what the code
// does rather than keep a list that could drift. (vite's `?raw` glob, not `node:fs`: @types/node
// is not installed here.)
const API_DIR = "../../../src/between_jobs/api/";
const apiSources = import.meta.glob("../../../src/between_jobs/api/*.py", {
  query: "?raw",
  import: "default",
  eager: true,
}) as Record<string, string>;
const migrationSources = import.meta.glob("../../../supabase/migrations/*.sql", {
  query: "?raw",
  import: "default",
  eager: true,
}) as Record<string, string>;

// A module of the API by name, without its extension: apiModule("app").
function apiModule(name: string): string {
  const source = apiSources[`${API_DIR}${name}.py`];
  if (source === undefined) throw new Error(`no API module named ${name}`);
  return source;
}

// The Privacy Policy and the Terms are typed data (legal.ts). This pins their STRUCTURE, that
// nothing unfinished or untrue-by-construction is in them, and -- the part that keeps them
// honest over time -- that every service the policy names is backed by a source file that really
// calls it, and that the numbers and names it quotes are the ones the code uses.

// ── flattening a document ──────────────────────────────────────────────────

function inlineText(part: Inline): string {
  if (typeof part === "string") return part;
  if ("email" in part) return part.email;
  return part.text;
}

function blockTexts(block: Block): string[] {
  switch (block.kind) {
    case "p":
      return [block.inline.map(inlineText).join("")];
    case "h3":
      return [block.text];
    case "ul":
      return block.items.map((item) => item.map(inlineText).join(""));
    case "defs":
      return block.items.flatMap((item) => [item.term, item.detail.map(inlineText).join("")]);
  }
}

function blockInlines(block: Block): Inline[] {
  switch (block.kind) {
    case "p":
      return [...block.inline];
    case "h3":
      return [];
    case "ul":
      return block.items.flatMap((item) => [...item]);
    case "defs":
      return block.items.flatMap((item) => [...item.detail]);
  }
}

function textsOf(doc: LegalDocument): string[] {
  return doc.sections.flatMap((section) => [section.heading, ...section.blocks.flatMap(blockTexts)]);
}

function inlinesOf(doc: LegalDocument): Inline[] {
  return doc.sections.flatMap((section) => section.blocks.flatMap(blockInlines));
}

const DOCS: [string, LegalDocument][] = [
  ["Privacy Policy", PRIVACY],
  ["Terms of Service", TERMS],
];

// ── version, date, what changed ────────────────────────────────────────────

describe("the version of the legal text", () => {
  it("is the date the text took effect, and the page's date label says the same day", () => {
    expect(LEGAL_VERSION).toBe("2026-10-05");
    expect(LEGAL_EFFECTIVE_DATE.iso).toBe(LEGAL_VERSION);
    const label = new Date(`${LEGAL_EFFECTIVE_DATE.iso}T00:00:00Z`).toLocaleDateString("en-US", {
      timeZone: "UTC",
      month: "long",
      day: "numeric",
      year: "numeric",
    });
    expect(LEGAL_EFFECTIVE_DATE.label).toBe(label);
  });

  it("says what changed", () => {
    expect(LEGAL_WHAT_CHANGED.trim().length).toBeGreaterThan(0);
  });
});

// ── structure ──────────────────────────────────────────────────────────────

const REQUIRED_PRIVACY_SECTIONS = [
  "short-version",
  "who-we-are",
  "what-we-store",
  "who-else",
  "operator-access",
  "gmail",
  "telegram",
  "extension",
  "browser-storage",
  "keeping-deleting",
  "security",
  "your-rights",
  "children",
  "changes",
  "contact",
];

const REQUIRED_TERMS_SECTIONS = [
  "agreement",
  "beta",
  "your-responsibility",
  "your-keys",
  "acceptable-use",
  "your-content",
  "suspension",
  "open-source",
  "no-warranty",
  "liability",
  "changes",
  "governing-law",
  "contact",
];

describe("the sections", () => {
  it("the Privacy Policy has exactly the required sections, in this order", () => {
    expect(PRIVACY.sections.map((section) => section.id)).toEqual(REQUIRED_PRIVACY_SECTIONS);
  });

  it("the Terms have exactly the required sections, in this order", () => {
    expect(TERMS.sections.map((section) => section.id)).toEqual(REQUIRED_TERMS_SECTIONS);
  });

  it.each(DOCS)("every %s section has a plain heading, a unique id and some content", (_name, doc) => {
    const ids = new Set<string>();
    for (const section of doc.sections) {
      expect(section.id).toMatch(/^[a-z]+(-[a-z]+)*$/);
      expect(ids.has(section.id), `duplicate id ${section.id}`).toBe(false);
      ids.add(section.id);
      expect(section.heading.trim().length).toBeGreaterThan(0);
      expect(section.heading.length).toBeLessThanOrEqual(60);
      expect(section.blocks.length).toBeGreaterThan(0);
    }
  });

  it("the documents are titled, and the titles are what the footers call them", () => {
    expect(PRIVACY.title).toBe("Privacy Policy");
    expect(TERMS.title).toBe("Terms of Service");
  });

  it("every list has items and every definition list has terms and details", () => {
    for (const [, doc] of DOCS) {
      for (const section of doc.sections) {
        for (const block of section.blocks) {
          if (block.kind === "ul") expect(block.items.length).toBeGreaterThan(0);
          if (block.kind === "defs") {
            expect(block.items.length).toBeGreaterThan(0);
            for (const item of block.items) {
              expect(item.term.trim().length).toBeGreaterThan(0);
              expect(item.detail.length).toBeGreaterThan(0);
            }
          }
        }
      }
    }
  });
});

// ── nothing unfinished ─────────────────────────────────────────────────────

// Every string in the landing page's data, joined: JSON.stringify would add brackets of its own.
function landingText(): string {
  const found: string[] = [];
  const walk = (value: unknown) => {
    if (typeof value === "string") found.push(value);
    else if (Array.isArray(value)) value.forEach(walk);
    else if (typeof value === "object" && value !== null) Object.values(value).forEach(walk);
  };
  walk(LANDING);
  return found.join("\n");
}

describe("nothing unfinished is in the text", () => {
  it.each(DOCS)("%s has no placeholder, marker or draft label", (_name, doc) => {
    const text = textsOf(doc).join("\n");
    expect(text).not.toMatch(/TODO|FIXME|TBD|XXX|lorem|ipsum|placeholder|MAINTAINER/i);
    // Case matters here: "drafts" and "Draft answer" are things the product does, a DRAFT label is not.
    expect(text).not.toMatch(/\bDRAFT\b/);
    expect(text).not.toMatch(/[[\]]/);
    expect(text).not.toMatch(/\{\{|\}\}|<[A-Za-z/]/);
  });

  it.each(DOCS)("%s has no em dash (the project writes ' -- ')", (_name, doc) => {
    expect(textsOf(doc).join("\n")).not.toContain("—");
  });

  it("the landing page's words have none either", () => {
    const text = landingText();
    expect(text).not.toMatch(/TODO|FIXME|TBD|lorem|ipsum|placeholder|\[|\]/i);
    expect(text).not.toContain("—");
  });
});

// ── links ──────────────────────────────────────────────────────────────────

describe("every link target", () => {
  const ROUTES = new Set<string>([PRIVACY_PATH, TERMS_PATH]);

  it.each(DOCS)("in the %s is an internal route or an https address", (_name, doc) => {
    for (const part of inlinesOf(doc)) {
      if (typeof part === "string" || "email" in part) continue;
      if ("to" in part) {
        expect(ROUTES.has(part.to), part.to).toBe(true);
      } else {
        const url = new URL(part.href);
        expect(url.protocol, part.href).toBe("https:");
        expect(url.username + url.password, part.href).toBe("");
      }
      expect(part.text.trim().length).toBeGreaterThan(0);
    }
  });

  it("the only external address is the source repository", () => {
    const hrefs = new Set<string>();
    for (const [, doc] of DOCS) {
      for (const part of inlinesOf(doc)) {
        if (typeof part !== "string" && "href" in part) hrefs.add(part.href);
      }
    }
    expect([...hrefs]).toEqual([REPO_URL]);
  });

  it("link text says where it goes (no 'here', no 'click')", () => {
    for (const [, doc] of DOCS) {
      for (const part of inlinesOf(doc)) {
        if (typeof part === "string" || "email" in part) continue;
        expect(part.text.toLowerCase()).not.toMatch(/^(here|click|link|this|read more)\b/);
      }
    }
  });
});

describe("the contact addresses", () => {
  it("are exactly privacy@ and support@ the domain, and nothing else is written as an address", () => {
    expect(PRIVACY_EMAIL).toBe("privacy@between-jobs.tech");
    expect(SUPPORT_EMAIL).toBe("support@between-jobs.tech");

    const used = new Set<string>();
    for (const [, doc] of DOCS) {
      for (const part of inlinesOf(doc)) {
        if (typeof part !== "string" && "email" in part) used.add(part.email);
      }
    }
    expect([...used].sort()).toEqual([PRIVACY_EMAIL, SUPPORT_EMAIL]);

    // No address typed by hand in running text, where a typo would go unnoticed.
    for (const [, doc] of DOCS) {
      for (const part of inlinesOf(doc)) {
        if (typeof part === "string") expect(part).not.toMatch(/[\w.+-]+@[\w-]+\.[\w.-]+/);
      }
    }
  });

  it("both documents give both ways to reach us", () => {
    for (const [, doc] of DOCS) {
      const contact = doc.sections.find((section) => section.id === "contact");
      const emails = contact?.blocks.flatMap(blockInlines).filter((part) => typeof part !== "string" && "email" in part);
      expect(emails?.length, doc.title).toBeGreaterThanOrEqual(2);
    }
  });
});

// ── the processors, and the evidence for each ──────────────────────────────

interface CodeCheck {
  path: string;
  text: string;
  needles: string[];
}

type Evidence =
  | { kind: "code"; checks: CodeCheck[] }
  // A deployment fact that no file in this repository can show. It is allowed only for the names
  // pinned in OPERATOR_ATTESTED below, and each needs the operator's confirmation.
  | { kind: "operator"; note: string };

// Every processor name -> where the code calls it. Adding a processor to legal.ts without an
// entry here fails the test below, which is the point: the policy may not name a service the
// code does not use, and the code may not use one the policy does not name.
const EVIDENCE: Record<string, Evidence> = {
  Supabase: {
    kind: "code",
    checks: [
      { path: "src/between_jobs/api/supabase_client.py", text: supabaseClient, needles: ["acreate_client", "SUPABASE_URL"] },
    ],
  },
  Railway: {
    kind: "operator",
    note: "Where the API is deployed. The repository only knows Railway as an example platform and a deployment-id variable.",
  },
  Cloudflare: {
    kind: "operator",
    note: "Where the website's files are served from, and the forwarder for mail sent to privacy@ and support@ (Cloudflare Email Routing). The operator confirms both when the site and the mail are set up, and rewords the sentence if a different mail setup is chosen.",
  },
  Resend: { kind: "operator", note: "The mail sender configured in the sign-in provider's dashboard, not in code." },
  "Resume engine": {
    kind: "code",
    checks: [
      {
        path: "src/between_jobs/api/forge_engines_client.py",
        text: forgeEnginesClient,
        needles: ["FORGE_ENGINES_BASE_URL", '"/apply"', "credential.secret"],
      },
    ],
  },
  "PDF renderer": {
    kind: "code",
    checks: [
      { path: "src/between_jobs/api/latex_service_client.py", text: latexServiceClient, needles: ["LATEX_SERVICE_BASE_URL", "/compile"] },
      { path: "latex-service/src/latex_service/compiler.py", text: latexCompiler, needles: ["TemporaryDirectory"] },
    ],
  },
  OpenRouter: {
    kind: "code",
    checks: [
      { path: "src/between_jobs/api/llm_client.py", text: llmClient, needles: ["https://openrouter.ai/api/v1", "AsyncOpenAI"] },
      { path: "src/between_jobs/api/credentials_routes.py", text: credentialsRoutes, needles: ["https://openrouter.ai/api/v1/key"] },
    ],
  },
  "You.com": {
    kind: "code",
    checks: [{ path: "src/between_jobs/api/research_clients.py", text: researchClients, needles: ["ydc-index.io"] }],
  },
  Firecrawl: {
    kind: "code",
    checks: [
      {
        path: "src/between_jobs/api/research_clients.py",
        text: researchClients,
        needles: ["api.firecrawl.dev/v2/search", "api.firecrawl.dev/v2/scrape"],
      },
    ],
  },
  Serper: {
    kind: "code",
    checks: [{ path: "src/between_jobs/api/search_providers.py", text: searchProviders, needles: ["google.serper.dev"] }],
  },
  "Brave Search": {
    kind: "code",
    checks: [{ path: "src/between_jobs/api/search_providers.py", text: searchProviders, needles: ["api.search.brave.com"] }],
  },
  "JSearch (RapidAPI)": {
    kind: "code",
    checks: [{ path: "src/between_jobs/api/search_providers.py", text: searchProviders, needles: ["jsearch.p.rapidapi.com"] }],
  },
  Adzuna: {
    kind: "code",
    checks: [{ path: "src/between_jobs/api/search_providers.py", text: searchProviders, needles: ["api.adzuna.com"] }],
  },
  USAJobs: {
    kind: "code",
    checks: [{ path: "src/between_jobs/api/search_providers.py", text: searchProviders, needles: ["data.usajobs.gov"] }],
  },
  Apollo: {
    kind: "code",
    checks: [{ path: "src/between_jobs/api/contact_enrichment.py", text: contactEnrichment, needles: ["api.apollo.io"] }],
  },
  Hunter: {
    kind: "code",
    checks: [{ path: "src/between_jobs/api/contact_enrichment.py", text: contactEnrichment, needles: ["api.hunter.io"] }],
  },
  Exa: {
    kind: "code",
    checks: [{ path: "src/between_jobs/api/research_clients.py", text: researchClients, needles: ["api.exa.ai"] }],
  },
  "Employer job boards": {
    kind: "code",
    checks: [
      {
        path: "src/between_jobs/api/job_registry_adapters.py",
        text: registryAdapters,
        needles: [
          "Greenhouse",
          "Lever",
          "Ashby",
          "Workday",
          "api.smartrecruiters.com",
          "apply.workable.com",
          "Recruitee",
          "www.amazon.jobs",
          "jobs.apple.com",
          "www.deshaw.com",
          "www.google.com",
        ],
      },
    ],
  },
  "RemoteOK and Arbeitnow": {
    kind: "code",
    checks: [
      {
        path: "src/between_jobs/api/search_providers.py",
        text: searchProviders,
        needles: ["https://remoteok.com/api", "https://www.arbeitnow.com/api/job-board-api"],
      },
    ],
  },
  "Listing pages": {
    kind: "code",
    checks: [
      {
        path: "src/between_jobs/api/ats_liveness.py",
        text: atsLiveness,
        needles: ["https://boards-api.greenhouse.io", "https://www.linkedin.com/jobs-guest"],
      },
    ],
  },
  GitHub: {
    kind: "code",
    checks: [{ path: "src/between_jobs/api/contact_research.py", text: contactResearch, needles: ["https://api.github.com"] }],
  },
  Google: {
    kind: "code",
    checks: [
      {
        path: "src/between_jobs/api/gmail_client.py",
        text: gmailClient,
        needles: ["https://accounts.google.com", "https://gmail.googleapis.com", "https://oauth2.googleapis.com/token"],
      },
      { path: "web/src/auth.tsx", text: authSource, needles: ['provider: "google"'] },
    ],
  },
  Telegram: {
    kind: "code",
    checks: [{ path: "src/between_jobs/api/telegram_client.py", text: telegramClient, needles: ["https://api.telegram.org"] }],
  },
  LinkedIn: {
    kind: "code",
    checks: [{ path: "web/src/lib/hiringSignals.ts", text: hiringSignalsLib, needles: ["https://www.linkedin.com/embed/feed/update/"] }],
  },
};

// The deployment facts the code cannot show. Naming a new one here is a deliberate act that
// needs the operator's say-so, so it is a list a test pins rather than a free-for-all.
const OPERATOR_ATTESTED = ["Cloudflare", "Railway", "Resend"];

describe("the processor list", () => {
  it("names each service once, in a known group", () => {
    const names = PROCESSORS.map((processor) => processor.name);
    expect(new Set(names).size).toBe(names.length);
    const groups = new Set(PROCESSOR_GROUPS.map((group) => group.id));
    for (const processor of PROCESSORS) {
      expect(groups.has(processor.group), processor.name).toBe(true);
      expect(processor.detail.length).toBeGreaterThan(0);
    }
    for (const group of PROCESSOR_GROUPS) {
      expect(PROCESSORS.some((processor) => processor.group === group.id), group.id).toBe(true);
    }
  });

  it("has an entry in the evidence map for every name, and no entry for a name that is not listed", () => {
    expect(Object.keys(EVIDENCE).sort()).toEqual(PROCESSORS.map((processor) => processor.name).sort());
  });

  it.each(Object.entries(EVIDENCE).filter(([, evidence]) => evidence.kind === "code"))(
    "%s is called from the source file named, which mentions its host or SDK",
    (name, evidence) => {
      if (evidence.kind !== "code") throw new Error("unreachable");
      expect(evidence.checks.length, name).toBeGreaterThan(0);
      for (const check of evidence.checks) {
        // The file exists (an import that did not resolve would not have loaded) and is not empty.
        expect(check.text.length, check.path).toBeGreaterThan(0);
        for (const needle of check.needles) {
          expect(check.text.toLowerCase(), `${check.path} should mention ${needle}`).toContain(needle.toLowerCase());
        }
      }
    },
  );

  it("lets only the deployment facts the code cannot show go without a source file", () => {
    const attested = Object.entries(EVIDENCE)
      .filter(([, evidence]) => evidence.kind === "operator")
      .map(([name]) => name)
      .sort();
    expect(attested).toEqual(OPERATOR_ATTESTED);
    for (const name of attested) {
      const evidence = EVIDENCE[name];
      if (evidence.kind === "operator") expect(evidence.note.length).toBeGreaterThan(10);
    }
  });

  it("appears in the Privacy Policy under its own name", () => {
    const terms = new Set(
      PRIVACY.sections.flatMap((section) =>
        section.blocks.flatMap((block) => (block.kind === "defs" ? block.items.map((item) => item.term) : [])),
      ),
    );
    for (const processor of PROCESSORS) expect(terms.has(processor.name), processor.name).toBe(true);
  });

  it("names no service that is not in the code", () => {
    const text = textsOf(PRIVACY).join("\n");
    for (const absent of [
      "Sentry",
      "PostHog",
      "Mixpanel",
      "Google Analytics",
      "Amplitude",
      "Segment",
      "Plausible",
      "Datadog",
      "Stripe",
      "Intercom",
      "HubSpot",
    ]) {
      expect(text, absent).not.toContain(absent);
    }
  });

  it("reminds whoever edits it, next to the list, that a new processor is added before it is switched on", () => {
    expect(legalSource).toMatch(/NEW PROCESSORS MUST BE ADDED HERE BEFORE THEY ARE SWITCHED ON/);
  });
});

// ── numbers and names the text quotes are the ones the code uses ───────────

describe("what the text says matches the code", () => {
  const privacyText = textsOf(PRIVACY).join("\n");

  it("the Gmail permissions are the two the client requests", () => {
    expect(privacyText).toContain("gmail.compose");
    expect(privacyText).toContain("gmail.readonly");
    expect(gmailClient).toContain("https://www.googleapis.com/auth/gmail.compose");
    expect(gmailClient).toContain("https://www.googleapis.com/auth/gmail.readonly");
    // And only those: no other Gmail scope is requested.
    const scopes = gmailClient.match(/auth\/gmail\.[a-z]+/g) ?? [];
    expect(new Set(scopes)).toEqual(new Set(["auth/gmail.compose", "auth/gmail.readonly"]));
  });

  it("does not claim that the compose permission cannot send (Google's own description says it can)", () => {
    expect(privacyText).not.toMatch(/cannot send|can't send|can not send|does not allow sending/i);
  });

  it("no code path sends mail through Gmail", () => {
    // The client has no function for it; what the text says about drafts rests on this.
    expect(gmailClient).not.toMatch(/messages\/send|drafts\/send|\.send\(/);
    expect(gmailClient).toContain("_DRAFTS_URL");
    expect(privacyText).toContain("Nothing in its code calls Gmail's send function");
  });

  it("the reply checker runs about every 15 minutes, as the text says", () => {
    expect(gmailReplyChecker).toContain("_DEFAULT_CHECK_INTERVAL_SECONDS = 900.0");
    expect(privacyText).toContain("Roughly every 15 minutes");
  });

  it("a cached search result is deleted after about 24 hours: a 24-hour ceiling, swept hourly by a worker that can be switched off", () => {
    // The code's own contract is the ceiling plus one purge interval, and the sweep has a kill
    // switch, so the text says "about" and these pin what "about" rests on.
    expect(hiringSignalCache).toContain("CACHE_TTL = timedelta(hours=24)");
    expect(hiringSignalCache).toContain("PURGE_INTERVAL_SECONDS = 3600.0");
    expect(apiModule("app")).toMatch(/"hiring_signal_cache_purge",\s*interval_seconds=PURGE_INTERVAL_SECONDS/);
    expect(privacyText).toContain("deleted after about 24 hours");
    expect(privacyText).toContain("deleted after about a day");
    expect(privacyText).not.toContain("within 24 hours");
  });

  it("what Telegram's updates leave is an update number, cleared after 7 days by a later update; a link code lasts 10 minutes", () => {
    // The table keeps Telegram's update_id (a counter Telegram assigns), never a message id, and
    // its only purge is inside the claim of a later update, 200 rows at a time: with no traffic
    // nothing is cleared, so the text names the number and the trigger, not a schedule.
    expect(telegramDedup).toContain("update_id");
    expect(telegramDedup).toContain("interval '7 days'");
    expect(telegramDedup).toContain("limit 200");
    expect(telegramDedup).not.toMatch(/message_id/);
    expect(linkCodes).toContain("_CODE_TTL_SECONDS = 10 * 60");
    expect(privacyText).toContain("a link code is valid for 10 minutes");
    expect(privacyText).toContain("the numbers Telegram assigns to each update it sends the bot");
    expect(privacyText).toContain("older than 7 days, as a side effect of the bot receiving later updates");
    expect(privacyText).not.toContain("Telegram message ids");
    expect(privacyText).not.toContain("cleared after about 7 days");
  });

  it("the extension's permissions and sites are the ones the text lists", () => {
    expect(extensionReadme).toContain("`storage`");
    expect(extensionReadme).toContain("`sidePanel`");
    expect(extensionReadme).toContain("The extension requests no `activeTab`, `scripting`, `tabs`, cookies or");
    for (const host of ["jobs.lever.co", "job-boards.greenhouse.io", "boards.greenhouse.io", "jobs.ashbyhq.com"]) {
      expect(extensionReadme, host).toContain(host);
      expect(privacyText, host).toContain(host);
    }
    expect(privacyText).toContain("'storage' and 'sidePanel'");
  });

  it("the buttons the text names exist under those names", () => {
    expect(profile).toContain("Export JSON");
    expect(accountCard).toContain("Delete my account");
    expect(integrations).toContain("Disconnect Gmail");
    expect(privacyText).toContain("'Export JSON'");
    expect(privacyText).toContain("'Delete my account'");
    expect(privacyText).toContain("'Disconnect Gmail'");
  });

  it("every key the text says you can save is one the API accepts, and no other", () => {
    const supported = [...credentialsRoutes.matchAll(/\("(?:llm|search)", "([a-z_]+)"\)/g)].map((m) => m[1]);
    const inText: Record<string, string> = {
      openrouter: "OpenRouter",
      you_com: "You.com",
      firecrawl: "Firecrawl",
      serper: "Serper",
      brave: "Brave Search",
      jsearch: "JSearch (RapidAPI)",
      adzuna: "Adzuna",
      usajobs: "USAJobs",
      apollo: "Apollo",
      hunter: "Hunter",
      exa: "Exa",
    };
    const names = new Set(PROCESSORS.map((processor) => processor.name));
    for (const provider of new Set(supported)) {
      expect(inText[provider], `${provider} is accepted by the API but has no entry here`).toBeDefined();
      expect(names.has(inText[provider]), `${inText[provider]} is not in the processor list`).toBe(true);
    }
  });
});

// ── the text a section of the policy says, and what each part of it rests on ──

function sectionText(doc: LegalDocument, id: string): string {
  const section = doc.sections.find((candidate) => candidate.id === id);
  if (section === undefined) throw new Error(`no section ${id}`);
  return section.blocks.flatMap(blockTexts).join("\n");
}

// What the policy says the AI provider receives, feature by feature: the list under the AI
// provider's entry, joined.
function aiProviderListText(): string {
  const blocks = PRIVACY.sections.find((section) => section.id === "who-else")?.blocks ?? [];
  const lead = blocks.findIndex(
    (block) => block.kind === "p" && blockTexts(block)[0].startsWith("What your AI provider receives"),
  );
  const list = blocks[lead + 1];
  if (lead === -1 || list === undefined || list.kind !== "ul") {
    throw new Error("the list of what the AI provider receives is not where the guard looks for it");
  }
  return blockTexts(list).join("\n");
}

// ── every place the API calls the model is described ───────────────────────

// Every API module that calls the model through llm_client, and the words of the list under
// 'What your AI provider receives' that cover what it sends. The guard is keyed on the import
// of llm_client, not on the engine client: a module that sends text to the user's AI provider
// goes through llm_client, and the engine client is already described under its own entry.
const MODEL_CALLERS: Record<string, string> = {
  application_answer_generator: "Draft answer",
  application_status_classifier: "Gmail replies",
  company_intel_pipeline: "Company research",
  company_intel_routes: "Company research",
  contact_research: "Contact research",
  contact_research_routes: "Contact research",
  discovery_routes: "Job fit scores",
  extension_routes: "Draft answer",
  gmail_reply_checker: "Gmail replies",
  interview_practice: "Interview practice",
  interview_practice_routes: "Interview practice",
  // Restructures the company research's verified claims into a company-level summary.
  interview_registry: "Company research",
  job_fit_scoring: "Job fit scores",
  outreach_writer: "outreach drafts",
  positioning_brief: "Positioning briefs",
  positioning_brief_routes: "Positioning briefs",
  saved_search_matcher: "Job fit scores",
  warm_path_events: "event research",
  warm_path_events_routes: "event research",
};

describe("every place the API calls the model is covered by 'What your AI provider receives'", () => {
  const callers = Object.entries(apiSources)
    .filter(([path, source]) => !path.endsWith("/llm_client.py") && /^from \.llm_client import/m.test(source))
    .map(([path]) => path.slice(API_DIR.length, -".py".length))
    .sort();

  it("knows every module that sends text to the user's AI provider, and no module that does not", () => {
    const unlisted = callers.filter((name) => !(name in MODEL_CALLERS));
    expect(
      unlisted,
      unlisted
        .map(
          (name) =>
            `${name}.py calls the model; add what it sends to 'What your AI provider receives' in legal.ts and to this map`,
        )
        .join("\n"),
    ).toEqual([]);
    const stale = Object.keys(MODEL_CALLERS).filter((name) => !callers.includes(name));
    expect(
      stale,
      `${stale.join(", ")} no longer calls the model: take it out of this map, and out of the list in legal.ts if nothing else sends the same text`,
    ).toEqual([]);
    expect(callers.length).toBe(Object.keys(MODEL_CALLERS).length);
  });

  it.each(Object.entries(MODEL_CALLERS))("%s is described by a list entry that says %j", (_file, phrase) => {
    expect(aiProviderListText()).toContain(phrase);
  });
});

describe("the lists of what each recipient receives match the payloads", () => {
  const aiList = aiProviderListText();

  it("event research: the city goes into the search words, and the AI provider gets only the evidence", () => {
    expect(apiModule("warm_path_events_routes")).toContain("build_event_query_plan(company_name, metro)");
    expect(apiModule("warm_path_events")).toContain('user_prompt=f"Evidence:\\n{evidence_text}"');
    expect(aiList).not.toContain("Apart from your city");
    expect(aiList).toContain("your city goes to your search provider");
    expect(aiList).toContain("is not sent to the AI provider");
    const youCom = PROCESSORS.find((processor) => processor.name === "You.com");
    expect(youCom?.detail.map(inlineText).join(" ")).toContain("the city from your profile");
  });

  it("interview practice: the job's company, title and description go with the resume evidence", () => {
    const practice = apiModule("interview_practice");
    for (const key of ['"company": context["company_name"]', '"job_title"', '"job_description"', '"resume_evidence"', '"registry_entry"']) {
      expect(practice, key).toContain(key);
    }
    expect(aiList).toContain("written from the job's company, title and description");
  });

  it("Exa: the contact's job title is part of the query, so the entry names it", () => {
    expect(contactEnrichment).toContain('query += f", {claimed_title}"');
    const exa = PROCESSORS.find((processor) => processor.name === "Exa");
    expect(exa?.detail.map(inlineText).join(" ")).toContain("the job title of a contact");
  });
});

// ── what the browser extension is said to send ─────────────────────────────

describe("the browser extension section says what the extension sends", () => {
  const privacyText = textsOf(PRIVACY).join("\n");
  const extension = sectionText(PRIVACY, "extension");

  it("says the page address goes out for every application form it finds, tracked or not", () => {
    expect(extension).toContain("whenever it finds an application form on one of those sites");
    expect(extension).toContain("to check whether you track that job. This happens whether or not you do.");
    // The store listing's own draft already says it plainly, in the same words.
    expect(extensionStorePrivacy).toContain("whether or not you track that job");
  });

  it("does not deny it anywhere else the policy talks about browsing", () => {
    expect(privacyText).not.toMatch(/does not collect your browsing history/);
    expect(privacyText).not.toMatch(/does not send[^.]*your browsing history/);
    expect(sectionText(PRIVACY, "what-we-store")).toContain("see 'The browser extension'");
  });

  it("says the API does not store the address or log it, which rests on the code", () => {
    expect(extension).toContain("does not store the address and keeps it out of its own logs");
    const routes = apiModule("extension_routes");
    const start = routes.indexOf("async def lookup_application_by_url(");
    expect(start).toBeGreaterThan(-1);
    const body = routes.slice(start, routes.indexOf("@router", start));
    expect(body).toContain("find_application_by_url(");
    expect(body).toContain('{"application_id": application["id"] if application else None}');
    expect(body).not.toMatch(/insert|upsert|update|\.rpc\(/);
    // The access line loses its query string, and the API's own request logs name routes.
    expect(loggingSetup).toContain("_strip_query(");
    expect(apiModule("app")).toContain("never the query string");
  });

  it("does not claim more about the hosting providers than that the address may be in their logs", () => {
    expect(extension).toContain("it may appear in the connection logs of our hosting providers");
  });
});

// ── the stored-but-quiet tables are named ──────────────────────────────────

describe("what we store names the small tables that hold something about you", () => {
  const stored = sectionText(PRIVACY, "what-we-store");

  it("the AI provider and model chosen as the default are kept next to the key", () => {
    expect(credentialsRoutes).toContain("set_preference(");
    expect(apiModule("capability_preferences_store")).toContain('DEFAULT_CAPABILITY = "default"');
    expect(stored).toContain("which AI provider and model you chose as your default");
    // One default row, written when a key is saved: never "per feature".
    expect(stored).not.toMatch(/per feature/i);
  });

  it("the numbered lists behind the Telegram bot's 'apply to #3' last 30 minutes and are not purged", () => {
    expect(apiModule("telegram_webhook")).toContain("create_working_set(");
    expect(apiModule("working_sets_store")).toContain("_DEFAULT_TTL_SECONDS = 30 * 60");
    expect(stored).toContain("the numbered lists of applications the Telegram bot shows you");
    expect(stored).toContain("each is valid for 30 minutes, and it is kept until you delete your account");
  });

  it("the token that ties a Gmail connect attempt to you is valid for 10 minutes and is kept if it is never used", () => {
    const states = apiModule("oauth_states_store");
    expect(states).toContain("_STATE_TTL_SECONDS = 10 * 60");
    expect(states).toContain("async def mint_state(");
    expect(stored).toContain("a one-time token that ties a Gmail connect attempt to your account");
    expect(stored).toContain("kept until you delete your account if it never does");
  });

  it("failed link-code attempts are counted per Telegram id, and the text says so in both places", () => {
    const creators = Object.values(migrationSources).filter((sql) => /create table public\.link_code_attempts/i.test(sql));
    expect(creators.length).toBe(1);
    expect(stored).toContain("a count of failed link-code attempts from it");
    expect(sectionText(PRIVACY, "telegram")).toContain("a count of failed link-code attempts from it");
  });
});

// ── nothing records product usage events yet ───────────────────────────────

describe("product usage events", () => {
  // The tables whose name suggests they record what people do. Each of these three is described
  // already (application history, event research, the queue behind the Today feed).
  const DESCRIBED = ["application_events", "event_outbox", "warm_path_events"];

  function eventLikeTables(): string[] {
    const found = new Set<string>();
    for (const sql of Object.values(migrationSources)) {
      for (const match of sql.matchAll(/create\s+table\s+(?:if\s+not\s+exists\s+)?(?:public\.)?([a-z_]+)/gi)) {
        if (/event|usage|funnel|analytic|telemetry|metric/i.test(match[1])) found.add(match[1].toLowerCase());
      }
    }
    return [...found].sort();
  }

  it("are not recorded by any table the policy does not describe", () => {
    const undescribed = eventLikeTables().filter((table) => !DESCRIBED.includes(table));
    expect(
      undescribed,
      `${undescribed.join(", ")}: if this table records what people do in the product, the Privacy Policy must describe it in the same change -- name every recorded column (including any that says which application or which job-site platform an event is about), say that an event is deleted with the account, add 'product usage events' to 'Keeping and deleting your data', and reconcile it with 'What you browse' -- and this guard must then check that each column is named. If it records nothing about people, add it to DESCRIBED.`,
    ).toEqual([]);
  });

  it("are not mentioned in the policy while nothing records them", () => {
    const text = textsOf(PRIVACY).join("\n");
    expect(text).not.toMatch(/usage event|product event|product usage/i);
  });
});

// ── who the API checks ─────────────────────────────────────────────────────

describe("the text says who the API checks, and the code agrees", () => {
  const security = sectionText(PRIVACY, "security");
  const ROUTE = /@(?:router|app)\.(?:get|post|put|patch|delete|api_route)\(/;
  const USER_CHECK = /require_user_id|require_active_extension_user_id/;

  it("every router module checks a user, except the Telegram webhook (the app's own route is pinned next)", () => {
    const unchecked = Object.entries(apiSources)
      .filter(([path, source]) => !path.endsWith("/app.py") && ROUTE.test(source) && !USER_CHECK.test(source))
      .map(([path]) => path.slice(API_DIR.length, -".py".length))
      .sort();
    expect(unchecked).toEqual(["telegram_webhook"]);
  });

  it("the app's own route is the health check, and the webhook accepts only the shared secret", () => {
    const appRoutes = [...apiModule("app").matchAll(/@app\.(?:get|post|put|patch|delete|api_route)\(([^)]*)\)/g)];
    expect(appRoutes.map((match) => match[1])).toEqual(['"/health", methods=["GET", "HEAD"]']);

    const webhook = apiModule("telegram_webhook");
    expect(webhook).toContain('@router.post("/telegram/webhook", dependencies=[Depends(_verify_webhook_secret)])');
    expect(webhook).toContain("x-telegram-bot-api-secret-token");
    expect(webhook).toContain("hmac.compare_digest");
  });

  it("the Google redirect is the one route of its module that carries no sign-in token, and a one-time state finds the user", () => {
    const oauth = apiModule("gmail_oauth_routes");
    expect((oauth.match(/@router\.(?:get|post)\(/g) ?? []).length).toBe(2);
    expect((oauth.match(/Depends\(require_user_id\)/g) ?? []).length).toBe(1);
    expect(oauth).toContain('@router.get("/oauth/gmail/callback")');
    expect(oauth).toContain("consume_state(");
  });

  it("says that every route that touches your data checks you, names the three exceptions, and no longer says 'every request'", () => {
    expect(security).toContain("every API route that reads or changes your data checks who you are");
    expect(security).toContain("the health check returns no user data");
    expect(security).toContain("matched to you by a one-time code");
    expect(security).toContain("the Telegram webhook accepts only requests that carry our secret");
    expect(security).not.toContain("checks who you are on every request");
  });
});

// ── the HTTPS claim, for the builds that are guarded ───────────────────────

describe("the HTTPS build claim is made only for the builds that are guarded", () => {
  const security = sectionText(PRIVACY, "security");

  it("the website's production build refuses an API address that is not https or is local", () => {
    expect(webViteConfig).toContain('config.command === "build"');
    expect(webViteConfig).toContain("assertApiBase(");
  });

  it("the extension's store package is guarded and its ordinary build is not, as its README says", () => {
    // wxt.config.ts wires the guard to `zip` only (extension/tests/store-build-guard.test.ts
    // pins that); this package's Vite cannot load the extension's TypeScript, so the README is
    // the evidence here.
    expect(extensionReadme).toContain("`npm run build` is for local testing");
    expect(extensionReadme).toContain("which refuses to package anything unless");
  });

  it("says both builds refuse, and does not say the extension's every build does", () => {
    expect(security).toContain("The website's production build and the browser extension's store package");
    expect(security).toContain("both builds are refused at build time");
    expect(security).not.toContain("a build pointed at a plain-HTTP or local address is refused");
  });
});

// ── mail sent to the contact addresses ─────────────────────────────────────

describe("the Cloudflare entry", () => {
  it("says it forwards the mail sent to the privacy and support addresses, which only the operator can confirm", () => {
    const cloudflare = PROCESSORS.find((processor) => processor.name === "Cloudflare");
    const detail = cloudflare?.detail.map(inlineText).join(" ") ?? "";
    expect(detail).toContain("the email you send to our privacy and support addresses");
    expect(detail).toContain("forwards it to the operator's mailbox");
    const evidence = EVIDENCE.Cloudflare;
    expect(evidence.kind).toBe("operator");
    if (evidence.kind === "operator") expect(evidence.note).toContain("Cloudflare Email Routing");
  });
});

// ── what the landing page claims ───────────────────────────────────────────

describe("the landing page's claims", () => {
  it("do not say the product is open source and standalone, which the engine service makes untrue", () => {
    const text = landingText().toLowerCase();
    expect(text).not.toMatch(/fully standalone|open-source and fully|self-contained/);
    // It says plainly that the generation service is separate and not in the repository yet.
    expect(LANDING.source.text).toContain("separate");
    expect(LANDING.source.text).toContain("not in the repository yet");
  });

  it("make no superlative or comparison", () => {
    const text = landingText().toLowerCase();
    expect(text).not.toMatch(
      /\b(best|leading|revolutionary|revolutionize|cutting-edge|world-class|unmatched|unrivalled|number one|#1|seamless|effortless|supercharge|ai-powered|guarantee|10x|instantly|fastest|smartest)\b/,
    );
  });

  it("name no competitor", () => {
    const text = landingText().toLowerCase();
    for (const name of ["simplify", "huntr", "teal", "jobscan", "rezi", "indeed", "ziprecruiter", "glassdoor", "linkedin", "monster", "careerbuilder"]) {
      expect(text, name).not.toContain(name);
    }
  });

  it("promise that you submit, and say the keys are yours", () => {
    expect(LANDING.promise.text).toContain("never submits an application for you");
    expect(LANDING.promise.title).toBe("You always submit");
    expect(LANDING.keys.text).toContain("your own");
  });

  it("do not say the Gmail connection only creates drafts: it also reads replies", () => {
    const text = landingText();
    expect(text).not.toMatch(/only creates drafts/i);
    // Wherever the landing page mentions Gmail it says the connection reads, and points at the policy.
    expect(text).toMatch(/Gmail/);
    expect(text).toMatch(/\breads\b/);
    expect(text).toContain("Privacy Policy");
    expect(LANDING.gmail.text).toContain("reads the thread of each draft it created");
    expect(LANDING.gmail.text).toContain("goes to your AI provider to be classified");
    expect(LANDING.gmail.text).toContain("a confident result can move that application to a new stage");
    // The verified halves of the promise stay.
    expect(LANDING.promise.text).toContain("never sends an email from your mailbox");
  });

  it("describe what the Gmail connection does, and each part of it is in the code", () => {
    expect(gmailClient).toContain("auth/gmail.readonly");
    expect(gmailReplyChecker).toContain("get_thread(");
    expect(gmailReplyChecker).toContain("classify_reply(");
    // A confident result moves the application's stage on its own.
    expect(gmailReplyChecker).toContain("AUTO_TRACK_THRESHOLD");
    expect(gmailReplyChecker).toContain("change_stage(");
    expect(apiModule("application_status_classifier")).toContain("AUTO_TRACK_THRESHOLD =");
    // And the reply text is what goes to the AI provider.
    expect(apiModule("application_status_classifier")).toContain("Reply text:");
  });

  it("describe the four things the product does, and nothing it does not", () => {
    expect(LANDING.features.map((feature) => feature.title)).toEqual([
      "Find roles",
      "Track applications",
      "Prepare documents",
      "Practice interviews",
    ]);
  });
});
