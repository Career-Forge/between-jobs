// @vitest-environment jsdom
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { EMBED_URL_PREFIX } from "../lib/hiringSignals";
import { initialPanelState, type PanelState } from "../lib/hiringSignalsPanelModel";
import type { HiringSignal, SavedPost, SearchOutcome } from "../lib/hiringSignalsTypes";
import { text } from "../testing/forms";
import { render } from "../testing/render";
import { HiringSignalsPanelView, type PanelActions } from "./HiringSignalsPanelView";

// The Hiring posts block's trail line, drawn by the real view: one line for the whole block (what searched
// and what the person saved together), and none while a search is out, has failed, or nothing has happened.
// (The grammar of the line itself is lib/researchTrails.test.ts; the rest of the view is
// HiringSignalsPanelView.test.tsx.) Outside a DocumentLayout a block's trail is a plain trail line.

const ID = "7000000000000000001";
const NOW = new Date("2026-09-19T12:00:00Z");
const noop = () => undefined;
const actions: PanelActions = { setFreshness: noop, search: noop, toggleEmbed: noop, save: noop, unsave: noop, reloadSaves: noop };

const signal: HiringSignal = {
  activity_id: ID,
  post_url: `https://www.linkedin.com/posts/example-${ID}`,
  embed_url: `${EMBED_URL_PREFIX}${ID}`,
  author_name: "Jane Example",
  posted_at: "2026-09-16T12:00:00Z",
  age_hint: null,
  species: "unclassified",
  comment_count: null,
  role_match: null,
  registry_match: null,
  saved: false,
};

const save: SavedPost = {
  id: "save-1",
  activity_id: ID,
  post_url: `https://www.linkedin.com/feed/update/urn:li:activity:${ID}`,
  embed_url: `${EMBED_URL_PREFIX}${ID}`,
  created_at: "2026-09-18T12:00:00Z",
};

function outcome(cached = false): SearchOutcome {
  return {
    response: {
      provider: "firecrawl",
      cached,
      freshness: "week",
      query_label: "Acme -- software engineer -- last 7 days",
      signals: [signal],
      counts: { raw_hits: 1, rejected: 0, duplicates: 0, off_topic_hidden: 0, echoes_hidden: 0, job_seekers_hidden: 0, too_old_hidden: 0, shown: 1 },
    },
    unreadable: 0,
  };
}

function view(overrides: Partial<PanelState>) {
  const state = initialPanelState({ savesStatus: "ready", ...overrides });
  return render(
    <MemoryRouter>
      <HiringSignalsPanelView state={state} now={NOW} actions={actions} />
    </MemoryRouter>,
  );
}

const trails = (container: HTMLElement) => [...container.querySelectorAll(".bj-trail")].map((node) => text(node));

describe("the Hiring posts block's trail", () => {
  it("draws no line while idle, with nothing saved", () => {
    expect(trails(view({}).container)).toEqual([]);
  });

  it("draws none while a search is out or after one failed, and the saved line alone when posts are saved", () => {
    expect(trails(view({ search: { kind: "loading" } }).container)).toEqual([]);
    expect(trails(view({ search: { kind: "failed", failure: { kind: "error", message: "The search failed.", retryable: true }, freshness: "week" } }).container)).toEqual([]);
    expect(trails(view({ search: { kind: "loading" }, saves: [save] }).container)).toEqual(["● You saved these · 1 post"]);
  });

  it("says where the results came from once a search is back, with 'cached result' only for a cached one", () => {
    expect(trails(view({ search: { kind: "ready", outcome: outcome() } }).container)).toEqual(["○ via Firecrawl · tagged by code, no model involved"]);
    expect(trails(view({ search: { kind: "ready", outcome: outcome(true) } }).container)).toEqual(["○ via Firecrawl · tagged by code, no model involved · cached result"]);
  });

  it("folds the saved posts into that one line: a single trail for the block, never two stacked", () => {
    const found = trails(view({ search: { kind: "ready", outcome: outcome() }, saves: [save, { ...save, id: "save-2" }] }).container);
    expect(found).toEqual(["○ via Firecrawl · tagged by code, no model involved · ● You saved 2 posts"]);
  });
});
