import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import {
  EMBED_URL_PREFIX,
  NOTHING_SHOWN_NOTE,
  NO_POSTS_SEEN_NOTE,
  YOU_COM_NO_LINKEDIN_NOTE,
} from "../lib/hiringSignals";
import type {
  Freshness,
  HiringSignal,
  SavedPost,
  SearchOutcome,
  SearchProvider,
  SignalCounts,
} from "../lib/hiringSignalsTypes";
import {
  FailureView,
  SearchResults,
  type SearchResultsProps,
} from "./HiringSignalsViews";

// Static-markup tests for the panel's wording: what it says when a search
// failed, and what it says about a result list -- above all the honest empty
// one. (Ids, names and urls are synthetic; nothing here is fetched.)

const ID = "7000000000000000001";
const ID_2 = "7000000000000000002";
const NOW = new Date("2026-09-19T12:00:00Z");

// The words a reader would see: tags stripped, entities decoded (the renderer
// escapes an apostrophe as &#x27;).
function visibleText(html: string): string {
  return html
    .replace(/<[^>]*>/g, " ")
    .replace(/&#x27;/g, "'")
    .replace(/&quot;/g, '"')
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&amp;/g, "&")
    .replace(/\s+/g, " ");
}

function iframeCount(html: string): number {
  return (html.match(/<iframe/g) ?? []).length;
}

function renderInRouter(node: React.ReactNode): string {
  return renderToStaticMarkup(<MemoryRouter>{node}</MemoryRouter>);
}

describe("FailureView", () => {
  const noop = () => {};

  it("sends setup-required to the Integrations page, and offers no pointless retry", () => {
    const html = renderInRouter(
      <FailureView
        failure={{ kind: "setup_required", message: "Hiring signals need a search provider." }}
        onRetry={noop}
      />,
    );
    expect(visibleText(html)).toContain("Hiring signals need a search provider.");
    expect(html).toContain('href="/profile/integrations"');
    expect(visibleText(html)).toContain("Open Integrations settings");
    expect(html).toContain('role="alert"');
    expect(visibleText(html)).not.toContain("Try again");
  });

  it("does not present setup-required as a failure (red) -- it is a to-do", () => {
    const html = renderInRouter(
      <FailureView failure={{ kind: "setup_required", message: "x" }} onRetry={noop} />,
    );
    expect(html).toContain("bj-hs-callout");
    expect(html).not.toContain("bj-error");
  });

  it("falls back to its own wording when the server sent no setup message", () => {
    const html = renderInRouter(
      <FailureView failure={{ kind: "setup_required", message: "" }} onRetry={noop} />,
    );
    expect(visibleText(html)).toContain("Connect a search provider to find hiring posts.");
  });

  it("says a missing application is missing in plain language, not with the server's technical text", () => {
    const html = renderInRouter(
      <FailureView
        failure={{ kind: "not_found", message: "no application found for id '6b1f0c1e'" }}
        onRetry={noop}
      />,
    );
    expect(visibleText(html)).toContain("This application could not be found");
    expect(html).not.toContain("6b1f0c1e");
    expect(html).toContain('href="/applications"');
    expect(visibleText(html)).not.toContain("Try again");
  });

  it("offers Try again for a retryable error", () => {
    const html = renderInRouter(
      <FailureView
        failure={{ kind: "error", message: "Your search provider could not be reached.", retryable: true }}
        onRetry={noop}
      />,
    );
    expect(visibleText(html)).toContain("Your search provider could not be reached.");
    expect(html).toMatch(/<button[^>]*>Try again<\/button>/);
    expect(html).toContain('role="alert"');
  });

  it("offers no Try again when trying again cannot change the outcome", () => {
    const html = renderInRouter(
      <FailureView
        failure={{
          kind: "error",
          message: "This application has no company name to search for.",
          retryable: false,
        }}
        onRetry={noop}
      />,
    );
    expect(visibleText(html)).toContain("This application has no company name to search for.");
    expect(html).not.toContain("<button");
  });
});

function makeSignal(overrides: Partial<HiringSignal> = {}): HiringSignal {
  return {
    activity_id: ID,
    post_url: `https://www.linkedin.com/posts/example-${ID}`,
    embed_url: `${EMBED_URL_PREFIX}${ID}`,
    author_name: "Jane Example",
    posted_at: "2026-09-16T12:00:00Z",
    age_hint: null,
    species: "hiring_drive",
    comment_count: null,
    role_match: null,
    registry_match: null,
    saved: false,
    ...overrides,
  };
}

function makeSave(overrides: Partial<SavedPost> = {}): SavedPost {
  return {
    id: "save-1",
    activity_id: ID,
    post_url: `https://www.linkedin.com/feed/update/urn:li:activity:${ID}`,
    embed_url: `${EMBED_URL_PREFIX}${ID}`,
    created_at: "2026-09-18T12:00:00Z",
    ...overrides,
  };
}

function makeCounts(overrides: Partial<SignalCounts> = {}): SignalCounts {
  return {
    raw_hits: 0,
    rejected: 0,
    duplicates: 0,
    off_topic_hidden: 0,
    echoes_hidden: 0,
    job_seekers_hidden: 0,
    too_old_hidden: 0,
    shown: 0,
    ...overrides,
  };
}

function makeOutcome(
  signals: HiringSignal[],
  overrides: {
    freshness?: Freshness;
    counts?: SignalCounts | null;
    cached?: boolean;
    unreadable?: number;
    provider?: SearchProvider | null;
  } = {},
): SearchOutcome {
  return {
    response: {
      provider: overrides.provider === undefined ? "you_com" : overrides.provider,
      cached: overrides.cached ?? false,
      freshness: overrides.freshness ?? "week",
      query_label: "Acme -- software engineer -- last 7 days",
      signals,
      counts: overrides.counts === undefined ? makeCounts() : overrides.counts,
    },
    unreadable: overrides.unreadable ?? 0,
  };
}

function renderResults(
  outcome: SearchOutcome,
  overrides: Partial<SearchResultsProps> = {},
): string {
  return renderToStaticMarkup(
    <SearchResults
      outcome={outcome}
      now={NOW}
      savedByActivity={new Map()}
      savesLoaded={true}
      savingIds={new Set()}
      removingIds={new Set()}
      openKeys={new Set()}
      cardErrors={{}}
      onWiden={() => {}}
      onToggleEmbed={() => {}}
      onSave={() => {}}
      onUnsave={() => {}}
      {...overrides}
    />,
  );
}

describe("SearchResults -- the honest empty answer", () => {
  it("says what was searched and through whom, using the human label rather than a raw query", () => {
    const html = renderResults(makeOutcome([]));
    expect(visibleText(html)).toContain(
      "Acme -- software engineer -- last 7 days -- via You.com",
    );
    expect(html).not.toMatch(/site:|inurl:|"linkedin/i);
  });

  it("treats zero results as an empty result -- with the counts sentence, not an error, and no claim about the company", () => {
    const html = renderResults(makeOutcome([], { counts: makeCounts(), provider: "firecrawl" }));
    const text = visibleText(html);
    expect(text).toContain(NO_POSTS_SEEN_NOTE);
    expect(text).toContain("The search returned no results.");
    expect(text).not.toMatch(/real answer/i);
    expect(html).not.toContain("bj-error");
    expect(html).not.toContain('role="alert"');
    expect(html).not.toContain("<li");
  });

  it("tells a You.com-only user that its index has no LinkedIn posts, rather than calling nothing an answer (SEAM-2 / YH-2)", () => {
    // You.com returned zero LinkedIn results on every LinkedIn-directed query
    // measured: an empty list from it is a fact about the index.
    const text = visibleText(
      renderResults(makeOutcome([], { counts: makeCounts(), provider: "you_com" })),
    );
    expect(text).toContain(YOU_COM_NO_LINKEDIN_NOTE);
    expect(text).toContain("does not appear to include LinkedIn posts");
    expect(text).not.toContain(NO_POSTS_SEEN_NOTE);
    expect(text).not.toMatch(/real answer|many companies simply have no/i);
    // ... and the same provider's pages of non-post links say it too
    const nonPosts = makeCounts({ raw_hits: 4, rejected: 4 });
    expect(
      visibleText(renderResults(makeOutcome([], { counts: nonPosts, provider: "you_com" }))),
    ).toContain(YOU_COM_NO_LINKEDIN_NOTE);
  });

  it("gives the other providers the plain 'returned no posts' note, never the You.com one", () => {
    for (const provider of ["firecrawl", "brave", "serper", null] as const) {
      const text = visibleText(renderResults(makeOutcome([], { counts: makeCounts(), provider })));
      expect(text).toContain(NO_POSTS_SEEN_NOTE);
      expect(text).not.toContain("You.com");
    }
  });

  it("explains why nothing is shown when the search did return hits", () => {
    const counts = makeCounts({ raw_hits: 7, off_topic_hidden: 4, job_seekers_hidden: 3 });
    const text = visibleText(renderResults(makeOutcome([], { counts })));
    expect(text).toContain(
      "The search returned 7 results; 0 shown. Not shown: 4 not about this company and 3 possible job-seeker posts.",
    );
    // posts came back, so the note is about the index, not "You.com sees no posts"
    expect(text).toContain(NOTHING_SHOWN_NOTE);
    expect(text).not.toContain(YOU_COM_NO_LINKEDIN_NOTE);
    expect(text).not.toContain("role");
  });

  it("suggests the next wider window when the window is not already the widest", () => {
    const day = renderResults(makeOutcome([], { freshness: "day" }));
    expect(day).toMatch(/<button[^>]*>Search the last 3 days<\/button>/);
    const threeDays = renderResults(makeOutcome([], { freshness: "3days" }));
    expect(threeDays).toMatch(/<button[^>]*>Search the last 7 days<\/button>/);
  });

  it("suggests nothing to widen when already at 7 days", () => {
    const html = renderResults(makeOutcome([], { freshness: "week" }));
    expect(html).not.toContain("<button");
    expect(visibleText(html)).not.toContain("Search the last");
  });

  it("does not call the result empty when entries came back that this page could not display", () => {
    const counts = makeCounts({ raw_hits: 2 });
    const text = visibleText(renderResults(makeOutcome([], { counts, unreadable: 2 })));
    for (const note of [YOU_COM_NO_LINKEDIN_NOTE, NO_POSTS_SEEN_NOTE, NOTHING_SHOWN_NOTE]) {
      expect(text).not.toContain(note);
    }
    expect(text).toContain("Not shown: 2 that this page could not display.");
  });

  it("is quiet about counts it could not read, rather than showing a partial breakdown", () => {
    const text = visibleText(renderResults(makeOutcome([], { counts: null })));
    expect(text).not.toContain("The search returned");
    expect(text).toContain(NOTHING_SHOWN_NOTE);
  });
});

describe("SearchResults -- with posts", () => {
  it("renders one card per signal, with the counts sentence above them", () => {
    const outcome = makeOutcome(
      [makeSignal(), makeSignal({ activity_id: ID_2, embed_url: `${EMBED_URL_PREFIX}${ID_2}` })],
      { counts: makeCounts({ raw_hits: 5, duplicates: 2, off_topic_hidden: 1, shown: 2 }) },
    );
    const html = renderResults(outcome);
    expect((html.match(/<li /g) ?? []).length).toBe(2);
    expect(visibleText(html)).toContain(
      "The search returned 5 results; 2 shown. Not shown: 1 not about this company and 2 duplicates.",
    );
    expect(iframeCount(html)).toBe(0);
  });

  it("explains every non-default tag in visible text, not only in a tooltip (F6)", () => {
    const outcome = makeOutcome([
      makeSignal({ species: "ats_echo" }),
      makeSignal({
        activity_id: ID_2,
        embed_url: `${EMBED_URL_PREFIX}${ID_2}`,
        species: "referral_offer",
      }),
    ]);
    const text = visibleText(renderResults(outcome));
    expect(text).toContain(
      "Job listing share: An automatic share of a job listing, not a personal hiring call.",
    );
    expect(text).toContain("Referral offer: The author appears to be offering referrals.");
    // a species that is not on screen is not explained
    expect(text).not.toContain("Hiring drive:");
  });

  it("explains 'Unclassified' once, above the cards, only when one is on screen", () => {
    const withUnclassified = renderResults(makeOutcome([makeSignal({ species: "unclassified" })]));
    expect(visibleText(withUnclassified)).toContain(
      "Unclassified means no known pattern matched, so it is not confirmed as a hiring call.",
    );
    const withoutUnclassified = renderResults(makeOutcome([makeSignal({ species: "hiring_drive" })]));
    expect(visibleText(withoutUnclassified)).not.toContain("Unclassified means");
  });

  it("notes that cached results can lag, and says nothing about it otherwise", () => {
    expect(visibleText(renderResults(makeOutcome([makeSignal()], { cached: true })))).toContain(
      "cached search",
    );
    expect(visibleText(renderResults(makeOutcome([makeSignal()], { cached: false })))).not.toContain(
      "cached",
    );
  });

  it("shows a saved post as Saved with an Unsave, and keeps the others plain", () => {
    const saved = renderResults(makeOutcome([makeSignal()]), {
      savedByActivity: new Map([[ID, makeSave()]]),
    });
    // aria-disabled, never `disabled`: a disabled button drops keyboard focus
    expect(saved).toMatch(/<button[^>]*aria-disabled="true"[^>]*>Saved<\/button>/);
    expect(saved).not.toMatch(/<button[^>]* disabled[ =>]/);
    expect(visibleText(saved)).toContain("Unsave");

    const unsaved = renderResults(makeOutcome([makeSignal()]));
    expect(unsaved).toMatch(/<button[^>]*>Save<\/button>/);
    expect(visibleText(unsaved)).not.toContain("Unsave");
  });

  it("trusts the server's saved flag only until the saved list has loaded", () => {
    const flagged = makeOutcome([makeSignal({ saved: true })]);
    const before = renderResults(flagged, { savesLoaded: false });
    expect(before).toMatch(/<button[^>]*aria-disabled="true"[^>]*>Saved<\/button>/);
    // Once loaded, an empty list is the truth: the post is not saved.
    const after = renderResults(flagged, { savesLoaded: true });
    expect(after).toMatch(/<button[^>]*>Save<\/button>/);
  });

  it("mounts an embed only for a card the user opened", () => {
    const outcome = makeOutcome([
      makeSignal(),
      makeSignal({ activity_id: ID_2, embed_url: `${EMBED_URL_PREFIX}${ID_2}` }),
    ]);
    expect(iframeCount(renderResults(outcome))).toBe(0);
    const html = renderResults(outcome, { openKeys: new Set([`search:${ID_2}`]) });
    expect(iframeCount(html)).toBe(1);
    expect(html).toContain(`src="${EMBED_URL_PREFIX}${ID_2}"`);
    expect(html).not.toContain(`src="${EMBED_URL_PREFIX}${ID}"`);
  });

  it("shows a per-card error under the card it belongs to", () => {
    const html = renderResults(makeOutcome([makeSignal()]), {
      cardErrors: { [`search:${ID}`]: "Could not save that post." },
    });
    expect(visibleText(html)).toContain("Could not save that post.");
    expect(html).toContain('role="alert"');
  });
});
