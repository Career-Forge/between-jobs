import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import contactResearchRoutes from "../../../src/between_jobs/api/contact_research_routes.py?raw";
import credentialsRoutes from "../../../src/between_jobs/api/credentials_routes.py?raw";
import discoveryRoutes from "../../../src/between_jobs/api/discovery_routes.py?raw";
import hiringSignalSearch from "../../../src/between_jobs/api/hiring_signal_search.py?raw";
import searchProviders from "../../../src/between_jobs/api/search_providers.py?raw";
import goldenReadme from "../../../tests/golden/hiring_signals/provider_responses/README.md?raw";
import integrationsSource from "../pages/Integrations.tsx?raw";
import { SearchProviderCard } from "../pages/Integrations";
import {
  NOT_VERIFIED_LIVE_DETAIL,
  NOT_VERIFIED_LIVE_LABEL,
  PROVIDER_STATUS,
  SEARCH_PROVIDER_CARDS,
  cardNote,
  providerNotice,
  type SearchProviderCardSpec,
} from "./providerStatus";
import { textOfMarkup } from "../testing/markup";

// The real API client pulls in the Supabase client, which throws at import time without env
// vars; nothing in a static render reaches a network anyway. The page also reads who is signed
// in (to ask whether Hiring signals is on for them), which is the same.
vi.mock("../lib/api", () => ({
  apiFetch: vi.fn(),
  ApiError: class ApiError extends Error {},
}));
vi.mock("../auth", () => ({ useAuth: () => ({ session: null }) }));

// The providers that carry the label, and the order is the table's.
const LABELLED = ["adzuna", "apollo", "brave", "exa", "hunter", "jsearch", "serper", "usajobs"];

// The providers the server accepts a key for, read from the server's own table, so that this
// test needs no list of its own to keep in step.
function serverProviders(): { service: string; provider: string }[] {
  const block = /_SUPPORTED_CREDENTIALS = \{([^}]*)\}/.exec(credentialsRoutes);
  if (block === null) throw new Error("credentials_routes.py no longer has _SUPPORTED_CREDENTIALS");
  return [...block[1].matchAll(/\("([a-z_]+)", "([a-z_]+)"\)/g)].map((m) => ({
    service: m[1],
    provider: m[2],
  }));
}

describe("the status table", () => {
  it("has an entry for every provider the server accepts a key for, and for no other", () => {
    const server = serverProviders().map((p) => p.provider).sort();
    // guards the guard: the scan found the table, and it is not trivially small
    expect(server.length).toBeGreaterThanOrEqual(11);
    expect(server).toContain("serper");
    expect(Object.keys(PROVIDER_STATUS).sort()).toEqual(server);
  });

  // About the TABLE, not about what the page draws: see "the cards of the page" below for which of
  // these have a card.
  it("marks, in the table, exactly the eight providers with no recorded successful run against the real service", () => {
    const labelled = Object.entries(PROVIDER_STATUS)
      .filter(([, entry]) => entry.status === "not_verified_live")
      .map(([provider]) => provider)
      .sort();
    expect(labelled).toEqual(LABELLED);
  });

  it("makes no verification claim for the rest (OpenRouter, You.com, Firecrawl): it does not turn an unknown into a 'verified'", () => {
    const others = Object.entries(PROVIDER_STATUS).filter(([provider]) => !LABELLED.includes(provider));
    expect(others.map(([provider]) => provider).sort()).toEqual(["firecrawl", "openrouter", "you_com"]);
    for (const [, entry] of others) {
      expect(entry.status).toBe("no_claim");
    }
    for (const provider of Object.keys(PROVIDER_STATUS)) {
      if (PROVIDER_STATUS[provider].status === "no_claim") {
        expect(providerNotice(provider)).toBeNull();
      }
    }
  });

  it("only has statuses the page knows how to show", () => {
    for (const entry of Object.values(PROVIDER_STATUS)) {
      expect(["not_verified_live", "no_claim"]).toContain(entry.status);
    }
  });
});

describe("the cards of the page", () => {
  it("are one per provider, each a search provider the server knows and the table decides on", () => {
    const providers = SEARCH_PROVIDER_CARDS.map((card) => card.provider);
    expect(new Set(providers).size).toBe(providers.length);
    const server = new Set(
      serverProviders()
        .filter((p) => p.service === "search")
        .map((p) => p.provider),
    );
    for (const card of SEARCH_PROVIDER_CARDS) {
      expect(server.has(card.provider), `${card.provider} is not a search provider of the server`).toBe(true);
      expect(Object.hasOwn(PROVIDER_STATUS, card.provider), `${card.provider} has no status`).toBe(true);
      expect(card.title.trim()).not.toBe("");
      expect(card.note.trim()).not.toBe("");
    }
  });

  // The label is shown on the cards the page has. JSearch, Adzuna and USAJobs are in the table (the
  // server accepts a key for them) but have no card, so their label is shown nowhere. When a card is
  // added for one of them it carries its label with no change to the table, and this list is the one
  // thing to update.
  it("carry the label on exactly Brave, Serper, Apollo, Hunter and Exa: the cards that have one", () => {
    const withLabel = SEARCH_PROVIDER_CARDS.filter(
      (card) => PROVIDER_STATUS[card.provider].status === "not_verified_live",
    ).map((card) => card.provider);
    expect(withLabel).toEqual(["brave", "serper", "apollo", "hunter", "exa"]);
    // and the labelled providers with no card are exactly the three the page cannot take a key for
    const withoutCard = LABELLED.filter((provider) => !SEARCH_PROVIDER_CARDS.some((card) => card.provider === provider));
    expect(withoutCard).toEqual(["adzuna", "jsearch", "usajobs"]);
  });

  it("keep the order the page always showed them in", () => {
    expect(SEARCH_PROVIDER_CARDS.map((card) => card.title)).toEqual([
      "You.com",
      "Firecrawl",
      "Brave Search",
      "Serper",
      "Apollo",
      "Hunter",
      "Exa",
    ]);
  });

  it("are drawn by the page from the registry, not from a card typed out per provider", () => {
    expect(integrationsSource).toContain("SEARCH_PROVIDER_CARDS.map((card) => (");
    expect(integrationsSource).not.toMatch(/<SearchProviderCard\s+title=/);
    expect(integrationsSource).toContain("const notice = providerNotice(provider);");
  });
});

function renderCard(card: SearchProviderCardSpec, hiringSignalsEnabled = false): string {
  return renderToStaticMarkup(
    <SearchProviderCard card={card} hiringSignalsEnabled={hiringSignalsEnabled} credential={null} onChanged={async () => {}} />,
  );
}

function render(provider: string, hiringSignalsEnabled = false): string {
  const card = SEARCH_PROVIDER_CARDS.find((c) => c.provider === provider);
  if (card === undefined) throw new Error(`no card for ${provider}`);
  return renderCard(card, hiringSignalsEnabled);
}

describe("a provider's card", () => {
  it.each(["brave", "serper", "apollo", "hunter", "exa"])(
    "says %s has not been verified live, in the header and in words",
    (provider) => {
      const html = render(provider);
      expect(html).toContain(`<span class="bj-badge-muted">${NOT_VERIFIED_LIVE_LABEL}</span>`);
      expect(NOT_VERIFIED_LIVE_LABEL).toBe("Not yet verified live");
      expect(textOfMarkup(html)).toContain(NOT_VERIFIED_LIVE_DETAIL);
      // still the card it was: the setup state and the key form are untouched
      expect(html).toContain("Not configured");
      expect(html).toContain("Save key");
    },
  );

  it.each(["you_com", "firecrawl"])("says nothing about live verification for %s", (provider) => {
    const html = render(provider);
    expect(html).not.toContain("Not yet verified live");
    expect(html).not.toContain("bj-badge-muted");
  });

  it("says, on the Apollo card, what its key check has and has not been run with", () => {
    const text = textOfMarkup(render("apollo"));
    expect(text).toContain(
      "Its key check has been run against the real service only with a made-up key or none; no run with a valid key is recorded.",
    );
    // and the other labelled cards do not carry that caveat
    expect(textOfMarkup(render("hunter"))).not.toContain("made-up key");
  });

  // The help text of a provider is drawn under the general notice. No card has one today (JSearch
  // has none), so a card of that shape is drawn here to keep the rendering under test.
  it("draws a provider's own caveat under the general notice, when its card has one", () => {
    const html = renderCard({ title: "JSearch", provider: "jsearch", placeholder: "x", note: "A note." });
    const text = textOfMarkup(html);
    expect(text).toContain(NOT_VERIFIED_LIVE_DETAIL);
    expect(text).toContain("remote_jobs_only");
    expect(text).toContain("unconfirmed");
    expect(text.indexOf("remote_jobs_only")).toBeGreaterThan(text.indexOf(NOT_VERIFIED_LIVE_DETAIL));
  });

  it("says a provider with no caveat of its own has none: no stray text after the general notice", () => {
    const text = textOfMarkup(render("serper"));
    expect(text).not.toContain("remote_jobs_only");
    expect(text).not.toContain("made-up key");
  });

  it("shows the label before the configured state, so a saved key does not hide it", () => {
    const card = SEARCH_PROVIDER_CARDS.find((c) => c.provider === "serper");
    if (card === undefined) throw new Error("no card");
    const html = renderToStaticMarkup(
      <SearchProviderCard
        card={card}
        hiringSignalsEnabled={false}
        credential={{
          id: "c1",
          service: "search",
          provider: "serper",
          model: null,
          is_validated: true,
          updated_at: "2026-10-01T00:00:00Z",
          scope: null,
        }}
        onChanged={async () => {}}
      />,
    );
    expect(html).toContain("Configured");
    expect(html).toContain("Not yet verified live");
  });
});

// A person Hiring signals is not enabled for must not be told their key is used for it: the server
// answers every one of its routes as if the feature did not exist, so the sentence would be false and
// would advertise a feature that does not exist for them.
describe("the notes about hiring posts", () => {
  const WITH_FEATURE = {
    brave:
      "Used for job search on Discover and to find hiring posts on an application. Validating runs one tiny real search call -- Brave has no free key-check endpoint.",
    serper:
      "Used for job search on Discover and, as a last resort, to find hiring posts on an application (it returns Google results). Validating runs one 1-credit search call -- Serper has no free key-check endpoint.",
  };

  it.each(["brave", "serper"] as const)("%s's card does not mention hiring at all when the feature is off for the person", (provider) => {
    const text = textOfMarkup(render(provider, false));
    expect(text.toLowerCase()).not.toContain("hiring");
    expect(text).toContain("Used for job search on Discover.");
    // the rest of the note stays: what validating costs
    expect(text).toContain("Validating runs one");
    expect(text).toContain(NOT_VERIFIED_LIVE_DETAIL);
  });

  it.each(["brave", "serper"] as const)("%s's card says what it always said when the feature is on for the person", (provider) => {
    expect(textOfMarkup(render(provider, true))).toContain(WITH_FEATURE[provider]);
  });

  it("no card's note, in its feature-off form, names Hiring signals or hiring posts", () => {
    for (const card of SEARCH_PROVIDER_CARDS) {
      expect(cardNote(card, false).toLowerCase(), card.provider).not.toContain("hiring");
    }
  });

  it("a card with no hiring note says the same whether or not the feature is on", () => {
    for (const card of SEARCH_PROVIDER_CARDS.filter((candidate) => candidate.hiringNote === undefined)) {
      expect(cardNote(card, true), card.provider).toBe(card.note);
    }
    expect(SEARCH_PROVIDER_CARDS.filter((card) => card.hiringNote !== undefined).map((card) => card.provider)).toEqual([
      "brave",
      "serper",
    ]);
  });

  it("is asked of the page's own reading of the person's status, once, and handed to every card", () => {
    expect(integrationsSource).toContain("const hiringSignalsEnabled = useHiringSignalsEnabled();");
    expect((integrationsSource.match(/useHiringSignalsEnabled\(\)/g) ?? []).length).toBe(1);
    expect(integrationsSource).toContain("hiringSignalsEnabled={hiringSignalsEnabled}");
    expect(integrationsSource).toContain("const note = cardNote(card, hiringSignalsEnabled);");
    // the note is not read straight off the card anywhere else
    expect(integrationsSource).not.toContain("card.note");
  });
});

describe("the notice", () => {
  it("is the label, the shared sentence and, for JSearch, a caveat of its own", () => {
    expect(providerNotice("serper")).toEqual({
      label: "Not yet verified live",
      detail: NOT_VERIFIED_LIVE_DETAIL,
      help: null,
    });
    const jsearch = providerNotice("jsearch");
    expect(jsearch?.label).toBe("Not yet verified live");
    expect(jsearch?.help).toContain("remote_jobs_only");
    expect(jsearch?.help).toContain("work_from_home");
    expect(jsearch?.help).toContain("unconfirmed");
  });

  it("says in words what an unverified integration is and what asks it, and nothing about a feature", () => {
    expect(NOT_VERIFIED_LIVE_DETAIL).toBe(
      "This integration has not had a successful run against the real service yet, so it may fail or return nothing. It is only asked when you have saved a key here.",
    );
    // it is true of contact enrichment as well as of search, so it names neither
    expect(NOT_VERIFIED_LIVE_DETAIL).not.toMatch(/search|hiring/i);
  });

  it("is nothing for a name the table does not have, including a built-in's name", () => {
    for (const name of ["", "nonexistent", "constructor", "__proto__", "toString"]) {
      expect(providerNotice(name)).toBeNull();
    }
  });
});

describe("what the page's claims rest on, in the server's code", () => {
  const jsearchSource = searchProviders.slice(
    searchProviders.indexOf("async def fetch_jsearch"),
    searchProviders.indexOf("# ── Fan-out orchestration"),
  );
  const fanOut = searchProviders.slice(searchProviders.indexOf("async def search_jobs"));

  it("JSearch really sends no remote-filter parameter, as its help text says", () => {
    expect(jsearchSource.length).toBeGreaterThan(200);
    expect(jsearchSource).not.toMatch(/["']remote_jobs_only["']|["']work_from_home["']/);
    // the flag it relies on instead
    expect(jsearchSource).toContain('"remote": j.get("job_is_remote")');
  });

  it("a provider is only asked when the person saved a key for it: each one is behind its own key", () => {
    for (const guard of [
      "if credentials.serper_key:",
      "if credentials.brave_key:",
      "if credentials.jsearch_key:",
      "if credentials.adzuna_app_id and credentials.adzuna_app_key:",
      "if credentials.usajobs_key and credentials.usajobs_email:",
    ]) {
      expect(fanOut, guard).toContain(guard);
    }
    // and the keys come from the person's own saved credentials, nowhere else
    for (const provider of ["serper", "brave", "jsearch", "adzuna", "usajobs"]) {
      expect(discoveryRoutes).toMatch(new RegExp(`try_get_secret(?:_pair)?\\(supabase, user_id, service="search", provider="${provider}"\\)`));
    }
  });

  // The three contact providers are labelled too, and the shared sentence says each is only asked
  // when the person saved a key for it.
  it("Apollo, Hunter and Exa are each used only behind the person's own saved key", () => {
    for (const provider of ["apollo", "hunter", "exa"]) {
      expect(contactResearchRoutes, provider).toContain(
        `${provider}_key = await try_get_secret(supabase, user_id, service="search", provider="${provider}")`,
      );
    }
    expect(contactResearchRoutes).toContain("if apollo_key is not None:");
    expect(contactResearchRoutes).toContain("if hunter_key is not None and");
    expect(contactResearchRoutes).toContain("if exa_key is None:");
  });
});

// What the labels rest on. The table says "no recorded successful live run" for eight providers and
// says nothing for three; this reads the repository for the records that decide it, so that a
// recorded run is a reason to change the table and not something nobody notices.
describe("what the repository records about live runs", () => {
  it("records measurements against the real You.com and Firecrawl APIs, which is why those two say nothing", () => {
    expect(hiringSignalSearch).toContain("measured against the real");
    expect(hiringSignalSearch).toContain("You.com and Firecrawl APIs");
    expect(goldenReadme).toContain("real Firecrawl v2");
    expect(goldenReadme).toContain("real You.com");
    expect(PROVIDER_STATUS.you_com.status).toBe("no_claim");
    expect(PROVIDER_STATUS.firecrawl.status).toBe("no_claim");
  });

  it("says of Brave and Serper that nothing but mocked transports covers them", () => {
    expect(hiringSignalSearch).toContain("Brave and Serper are implemented from their");
    expect(hiringSignalSearch).toContain("covered by mocked-transport tests only");
  });

  it("records Apollo's key check as run live only with a made-up or missing key", () => {
    expect(credentialsRoutes).toMatch(/for ANY key, including a missing or made-up one\s+\(checked live/);
    expect(credentialsRoutes).toContain('`{"healthy": true, "is_logged_in": false}`');
  });

  it("has no captured response of Apollo, Hunter or Exa among its golden fixtures", () => {
    const fixtures = Object.keys(
      import.meta.glob("../../../tests/golden/**/*.{json,md}", { query: "?raw", import: "default" }),
    );
    // guards the guard: the glob found the fixtures it is about
    expect(fixtures.some((name) => name.includes("firecrawl_company_posts.json"))).toBe(true);
    expect(fixtures.filter((name) => /apollo|hunter|exa/i.test(name.split("/").pop() ?? ""))).toEqual([]);
  });
});
