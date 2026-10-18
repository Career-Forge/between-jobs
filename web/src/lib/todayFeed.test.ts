import { describe, expect, it } from "vitest";
import {
  applicationNames,
  atsScoreOf,
  backgroundSearches,
  backgroundView,
  checkedAt,
  checkedWords,
  clockTime,
  fitReadOf,
  formatTenScale,
  jobLabel,
  kindLabel,
  rowActions,
  rowView,
  searchStatusWords,
  searchesRead,
  searchesTrail,
  todayFacts,
  trackRequestBody,
  type BackgroundSearch,
  type JobMatch,
  type RowContext,
  type TodayItem,
} from "./todayFeed";
import { buildReceipt, buildTrail, findForbiddenWord } from "./trail";

// The feed as the page draws it. Every line a person reads is built from a returned field, so these
// cases hand in exactly the fields a reply carries and check what comes out -- and what does not.

const NOW = new Date("2026-09-22T15:00:00Z");
const UTC = "UTC";
const ok = <T>(value: T) => ({ kind: "ok", value }) as const;
const failed = { kind: "failed" } as const;
const loading = { kind: "loading" } as const;

function item(overrides: Partial<TodayItem> & Pick<TodayItem, "id" | "kind">): TodayItem {
  return {
    headline: "A headline",
    detail: null,
    created_at: "2026-09-22T10:00:00Z",
    application_id: null,
    job_match: null,
    status_proposal: null,
    ...overrides,
  };
}

const MATCH: JobMatch = {
  today_item_id: "m1",
  saved_search_id: "ss-1",
  apply_url: "https://jobs.example/a",
  title: "Analytics Engineer",
  company: "Initech",
  location: "Remote",
  score100: 74,
  bin: "Strong",
  snippet: "s",
  provider: "registry",
};

function context(overrides: Partial<RowContext> = {}): RowContext {
  return { now: NOW, timeZone: UTC, names: new Map(), searchLabels: new Map(), fits: {}, ...overrides };
}

const trailText = (view: ReturnType<typeof rowView>) => buildTrail(view.trail)?.text ?? null;

describe("names", () => {
  const rows = [
    { id: "a1", snapshot: { title: "Data Engineer", company_name: "Acme" } },
    { id: "a2", snapshot: { title: "Analyst", company_name: null } },
    { id: "a3", snapshot: null },
    { id: "", snapshot: { title: "x" } },
    { snapshot: { title: "y" } },
  ];

  it("reads each application's title and company from its snapshot", () => {
    const names = applicationNames(rows);
    expect(names.get("a1")).toEqual({ title: "Data Engineer", company: "Acme" });
    expect(names.get("a2")).toEqual({ title: "Analyst", company: null });
    expect(names.get("a3")).toEqual({ title: null, company: null });
    expect(names.size).toBe(3);
  });

  it("names a job as 'Title at Company', or the title alone, or nothing", () => {
    const names = applicationNames(rows);
    expect(jobLabel(names.get("a1"))).toBe("Data Engineer at Acme");
    expect(jobLabel(names.get("a2"))).toBe("Analyst");
    expect(jobLabel(names.get("a3"))).toBeNull();
    expect(jobLabel(undefined)).toBeNull();
  });
});

describe("saved searches", () => {
  const rows = [
    { id: "s1", query: "data analyst", location: null, companies: [], remote_only: false, is_active: true, created_at: "2026-09-01T00:00:00Z", last_matched_at: "2026-09-22T06:00:00Z" },
    { id: "s2", query: "", location: "NYC", companies: [], remote_only: false, is_active: false, created_at: "2026-09-10T00:00:00Z", last_matched_at: "2026-09-10T00:00:00Z" },
    { id: "s3", query: "ml", location: null, companies: [], remote_only: true, is_active: true, created_at: "2026-09-20T00:00:00Z", last_matched_at: null },
  ];

  it("labels each search from its own filters", () => {
    expect(backgroundSearches(rows)?.map((search) => search.label)).toEqual(['"data analyst"', "in NYC", '"ml" remote only']);
  });

  it("treats a list with an entry that is not a search as unknown, not as a shorter list", () => {
    expect(backgroundSearches([...rows, { nope: true }])).toBeNull();
    expect(searchesRead(ok([...rows, { nope: true }]))).toEqual({ state: "failed" });
  });

  it("says a search was checked only when its last run is later than the search itself", () => {
    const [ran, sameTime, never] = backgroundSearches(rows) as BackgroundSearch[];
    expect(checkedAt(ran)).toBe("2026-09-22T06:00:00Z");
    // The matcher's time equals the creation time: it never ran.
    expect(checkedAt(sameTime)).toBeNull();
    expect(checkedAt(never)).toBeNull();
    expect(checkedAt({ ...ran, lastMatchedAt: "not a time" })).toBeNull();
  });

  it("carries a read's three states", () => {
    expect(searchesRead(loading)).toEqual({ state: "loading" });
    expect(searchesRead(failed)).toEqual({ state: "failed" });
    expect(searchesRead(ok(rows)).state).toBe("ready");
  });
});

describe("the facts the rows read", () => {
  const searches = searchesRead(ok([{ id: "ss-1", query: "analytics engineer", location: null, companies: [], remote_only: false, is_active: true, created_at: "2026-09-01T00:00:00Z" }]));
  const applications = ok([{ id: "app-1", snapshot: { title: "Data Engineer", company_name: "Acme" } }]);

  it("joins an item to its application's name and to the saved search that found it", () => {
    const facts = todayFacts({
      items: [
        item({ id: "r", kind: "resume_ready", application_id: "app-1", detail: "ATS score 72/100" }),
        item({ id: "m", kind: "high_fit_job", job_match: MATCH }),
        item({
          id: "p",
          kind: "status_proposal",
          application_id: "app-1",
          status_proposal: { id: "sp", proposed_type: "interview.requested", confidence: 0.72, evidence_spans: ["x"], status: "pending" },
        }),
      ],
      applications,
      searches,
      tracked: {},
    });
    if (facts.feed.state !== "ready") throw new Error("expected a feed");
    const [resume, match, proposal] = facts.feed.items;
    expect(resume).toMatchObject({ applicationId: "app-1", job: "Data Engineer at Acme", company: "Acme", atsScore: 72 });
    expect(match).toMatchObject({ applicationId: null, match: { title: "Analytics Engineer", score100: 74, searchLabel: '"analytics engineer"' } });
    expect(proposal).toMatchObject({ company: "Acme", proposal: { proposedType: "interview.requested", confidence: 0.72 } });
    expect(facts.names).toBe("settled");
    expect(facts.savedSearches).toEqual({ state: "ready", total: 1, active: 1 });
  });

  it("is loading until the feed has arrived, and says so for the names and the searches", () => {
    const facts = todayFacts({ items: null, applications: loading, searches: { state: "loading" }, tracked: {} });
    expect(facts).toEqual({ feed: { state: "loading" }, names: "loading", savedSearches: { state: "loading" } });
  });

  it("settles the names when the applications could not be read, and names no job", () => {
    const facts = todayFacts({ items: [item({ id: "r", kind: "resume_ready", application_id: "app-1" })], applications: failed, searches, tracked: {} });
    expect(facts.names).toBe("settled");
    if (facts.feed.state !== "ready") throw new Error("expected a feed");
    expect(facts.feed.items[0]).toMatchObject({ job: null, company: null });
  });

  it("names no search when the saved searches could not be read, and does not invent a label", () => {
    const facts = todayFacts({ items: [item({ id: "m", kind: "high_fit_job", job_match: MATCH })], applications, searches: { state: "failed" }, tracked: {} });
    if (facts.feed.state !== "ready") throw new Error("expected a feed");
    expect(facts.feed.items[0].match?.searchLabel).toBeNull();
    expect(facts.savedSearches).toEqual({ state: "failed" });
  });

  it("carries the application a Track created in this visit, by the job's apply URL", () => {
    const facts = todayFacts({ items: [item({ id: "m", kind: "high_fit_job", job_match: MATCH })], applications, searches, tracked: { [MATCH.apply_url]: "app-9" } });
    if (facts.feed.state !== "ready") throw new Error("expected a feed");
    expect(facts.feed.items[0].trackedApplicationId).toBe("app-9");
  });

  it("reads an ATS score only out of the exact sentence the server writes", () => {
    expect(atsScoreOf("ATS score 72/100")).toBe(72);
    expect(atsScoreOf("  ATS score 5/100 ")).toBe(5);
    for (const other of [null, "", "72", "Score 72/100", "ATS score 72 / 100", "ATS score 1000/100", "ATS score 72/100 and more", "gate said no"]) {
      expect(atsScoreOf(other)).toBeNull();
    }
  });

  it("asks the server to track a match exactly as Discover does", () => {
    expect(trackRequestBody(MATCH)).toEqual({
      apply_url: MATCH.apply_url,
      title: MATCH.title,
      company: MATCH.company,
      location: MATCH.location,
      snippet: MATCH.snippet,
      provider: MATCH.provider,
    });
  });
});

describe("the held-back chip's number", () => {
  const held = { result: { resume: null, gate_outcome: "REJECT_MISMATCH", fit: { overall_score: 3.64 } } };

  it("is the fit check's own score while the latest result is still held back", () => {
    expect(fitReadOf(held)).toEqual({ state: "held", score: 3.64 });
    expect(formatTenScale(3.64)).toBe("3.6");
    expect(formatTenScale(7)).toBe("7");
  });

  it("is absent once a newer run produced a resume, or the gate let it through", () => {
    expect(fitReadOf({ result: { ...held.result, resume: { artifact_id: "a" } } })).toEqual({ state: "unavailable" });
    expect(fitReadOf({ result: { ...held.result, gate_outcome: "proceed" } })).toEqual({ state: "unavailable" });
  });

  it("is absent when there is no result, no fit, or a score that is not a number", () => {
    for (const body of [null, {}, { result: null }, { result: { resume: null } }, { result: { resume: null, fit: null } }, { result: { resume: null, fit: { overall_score: "3" } } }, { result: { resume: null, fit: { overall_score: Number.NaN } } }]) {
      expect(fitReadOf(body)).toEqual({ state: "unavailable" });
    }
  });

  it("treats a result with no resume key at all as having none", () => {
    expect(fitReadOf({ result: { fit: { overall_score: 2 } } })).toEqual({ state: "held", score: 2 });
  });
});

describe("a row's words", () => {
  it("logs a tracked job and a stage change as code, with no model", () => {
    for (const kind of ["job_tracked", "stage_changed"] as const) {
      const view = rowView(item({ id: "t", kind, headline: "🆕 Tracking Data Engineer @ Acme" }), context());
      expect(view.chip).toBeNull();
      expect(view.title).toBe("🆕 Tracking Data Engineer @ Acme");
      expect(trailText(view)).toBe("Logged by code · no model involved · Sep 22");
      expect(buildTrail(view.trail)?.glyphs).toEqual(["code"]);
    }
  });

  it("marks a ready resume as waiting on review in gold, drafted by a model on the person's key", () => {
    const view = rowView(item({ id: "r", kind: "resume_ready", detail: "ATS score 72/100" }), context());
    expect(view.chip).toEqual({ tone: "gold", text: "Ready for your review" });
    expect(view.details).toEqual(["ATS score 72/100"]);
    expect(trailText(view)).toBe("Drafted by your model · on your key · Sep 22");
    expect(buildTrail(view.trail)?.glyphs).toEqual(["model"]);
  });

  describe("a resume the fit check held back", () => {
    const held = item({ id: "f", kind: "resume_failed", application_id: "app-1", headline: "⚠️ Resume generation needs attention for X", detail: "Fit too low" });
    const names = new Map([["app-1", { title: "Data Engineer", company: "Acme" }]]);

    it("is a calm card: the job in serif, a gold chip with the score, the gate's reason", () => {
      const view = rowView(held, context({ names, fits: { "app-1": { state: "held", score: 3.6 } } }));
      expect(view.label).toBe("Resume not generated");
      expect(view.title).toBe("Data Engineer at Acme");
      expect(view.serifTitle).toBe(true);
      expect(view.chip).toEqual({ tone: "gold", text: "Fit check held it back · 3.6 / 10" });
      expect(view.details).toEqual(["Fit too low"]);
      expect(trailText(view)).toBe("engine · Held back by the fit check · code-weighted from your model's sub-scores · Sep 22");
    });

    it("says only that it was held back when the score could not be read", () => {
      for (const fit of [undefined, { state: "loading" } as const, { state: "unavailable" } as const]) {
        const view = rowView(held, context({ names, fits: fit === undefined ? {} : { "app-1": fit } }));
        expect(view.chip?.text).toBe("Fit check held it back");
      }
    });

    it("does not take the job out of the server's headline", () => {
      const view = rowView(held, context());
      expect(view.title).toBe("An application you track");
      expect(view.title).not.toContain("needs attention");
    });

    it("puts the score in the receipt only when there is one", () => {
      const withScore = buildReceipt(rowView(held, context({ names, fits: { "app-1": { state: "held", score: 3.6 } } })).receipt);
      expect(withScore?.rows).toContainEqual({ label: "fit check", value: "3.6 / 10" });
      const without = buildReceipt(rowView(held, context({ names })).receipt);
      expect(without?.rows.some((row) => "label" in row && row.label === "fit check")).toBe(false);
    });
  });

  describe("a high fit job", () => {
    it("shows the score, the company and place, and the saved search that found it", () => {
      const view = rowView(
        item({ id: "m", kind: "high_fit_job", headline: "🎯 High-fit match: Analytics Engineer @ Initech", detail: "Strong on SQL", job_match: MATCH }),
        context({ searchLabels: new Map([["ss-1", '"analytics engineer"']]) }),
      );
      expect(view.chip).toEqual({ tone: "gold", text: "Strong -- 74 / 100" });
      expect(view.details).toEqual(["Initech -- Remote", "Strong on SQL"]);
      expect(trailText(view)).toBe('engine · Found by saved search "analytics engineer" · via registry · Sep 22');
      expect(buildTrail(view.trail)?.glyphs).toEqual(["engine"]);
    });

    it("says 'a saved search' when the list has no search by that id, and 'Unknown company' for no company", () => {
      const view = rowView(item({ id: "m", kind: "high_fit_job", job_match: { ...MATCH, company: null, location: null } }), context());
      expect(view.details).toEqual(["Unknown company"]);
      expect(trailText(view)).toBe("engine · Found by a saved search · via registry · Sep 22");
    });

    it("has no chip or provider when the companion row is missing", () => {
      const view = rowView(item({ id: "m", kind: "high_fit_job" }), context());
      expect(view.chip).toBeNull();
      expect(trailText(view)).toBe("engine · Found by a saved search · Sep 22");
    });
  });

  describe("a status proposal", () => {
    const proposal = (type: string, confidence: number, spans: string[] = ["Can we talk Thursday?"]) =>
      item({ id: "p", kind: "status_proposal", detail: "72% confidence -- review and confirm", status_proposal: { id: "sp", proposed_type: type, confidence, evidence_spans: spans, status: "pending" } });

    it("names the model, the Gmail reply, the confidence and the auto-move line", () => {
      const view = rowView(proposal("interview.requested", 0.72), context());
      expect(trailText(view)).toBe("Read by your model · from a Gmail reply · Sep 22 · 72% · under the 85% auto-move line");
      expect(view.chip).toEqual({ tone: "gold", text: "Needs your decision" });
      expect(view.quotes).toEqual(["Can we talk Thursday?"]);
      // The detail repeats the confidence the trail carries, so it is not shown twice.
      expect(view.details).toEqual([]);
    });

    it("is not called a decision once it has been decided", () => {
      for (const status of ["accepted", "dismissed"] as const) {
        const decided = item({ id: "p", kind: "status_proposal", status_proposal: { id: "sp", proposed_type: "interview.requested", confidence: 0.72, evidence_spans: [], status } });
        expect(rowView(decided, context()).chip).toBeNull();
      }
    });

    it("shows the share the stored confidence stands for in the trail and the receipt", () => {
      const view = rowView(proposal("interview.requested", 0.57), context());
      expect(trailText(view)).toBe("Read by your model · from a Gmail reply · Sep 22 · 57% · under the 85% auto-move line");
      expect(view.receipt).toContainEqual({ label: "confidence", value: "57%" });
    });

    it("is not called a decision when there is no stage to move to", () => {
      const view = rowView(proposal("recruiter.replied", 0.5), context());
      expect(view.chip).toBeNull();
      expect(view.quotes).toHaveLength(1);
    });

    it("leaves the confidence out when it is not a share, and never says 'under the line' for one over it", () => {
      expect(trailText(rowView(proposal("interview.requested", Number.NaN), context()))).toBe("Read by your model · from a Gmail reply · Sep 22");
      expect(trailText(rowView(proposal("interview.requested", 0.9), context()))).toBe("Read by your model · from a Gmail reply · Sep 22 · 90%");
    });

    it("drops blank evidence", () => {
      expect(rowView(proposal("interview.requested", 0.7, ["  ", "Real words"]), context()).quotes).toEqual(["Real words"]);
    });

    it("has no quotes and no confidence when the proposal row is gone", () => {
      const view = rowView(item({ id: "p", kind: "status_proposal" }), context());
      expect(view.quotes).toEqual([]);
      expect(trailText(view)).toBe("Read by your model · from a Gmail reply · Sep 22");
    });
  });

  it("leaves the date out of every trail when the item's time cannot be read", () => {
    const view = rowView(item({ id: "t", kind: "job_tracked", created_at: "yesterday" }), context());
    expect(trailText(view)).toBe("Logged by code · no model involved");
  });

  it("writes a date from another year with its year", () => {
    const view = rowView(item({ id: "t", kind: "job_tracked", created_at: "2025-12-30T10:00:00Z" }), context());
    expect(trailText(view)).toBe("Logged by code · no model involved · Dec 30, 2025");
  });

  it("never writes a word a trail does not say, in any trail or receipt", () => {
    const kinds = ["job_tracked", "resume_ready", "resume_failed", "stage_changed", "high_fit_job", "status_proposal"] as const;
    for (const kind of kinds) {
      const view = rowView(
        item({ id: "x", kind, application_id: "app-1", job_match: kind === "high_fit_job" ? MATCH : null, status_proposal: kind === "status_proposal" ? { id: "s", proposed_type: "offer.received", confidence: 0.6, evidence_spans: [], status: "pending" } : null }),
        context(),
      );
      expect(findForbiddenWord(trailText(view) ?? "")).toBeNull();
    }
  });

  it("falls back to the kind's own words for a kind the page does not know", () => {
    expect(kindLabel("interview_prep")).toBe("interview prep");
    expect(kindLabel("constructor")).toBe("constructor");
    expect(kindLabel("high_fit_job")).toBe("High fit job");
    const view = rowView(item({ id: "u", kind: "interview_prep" as never, headline: "Prep" }), context());
    expect(view.title).toBe("Prep");
    expect(trailText(view)).toContain("Logged by code");
  });
});

describe("the background trail", () => {
  const search = (id: string, overrides: Record<string, unknown> = {}) => ({
    id,
    query: id,
    location: null,
    companies: [],
    remote_only: false,
    is_active: true,
    created_at: "2026-09-01T00:00:00Z",
    last_matched_at: null,
    ...overrides,
  });

  it("counts active and paused, and says the newest check", () => {
    const view = backgroundView(
      searchesRead(ok([search("a", { last_matched_at: "2026-09-22T06:00:00Z" }), search("b", { is_active: false }), search("c", { last_matched_at: "2026-09-21T06:00:00Z" })])),
      ok([]),
    );
    expect(view.searches).toMatchObject({ state: "ready", total: 3, active: 2, paused: 1, checkedAt: "2026-09-22T06:00:00Z" });
    if (view.searches.state !== "ready") throw new Error("expected searches");
    expect(view.searches.entries.map((entry) => entry.status)).toEqual(["checked", "paused", "checked"]);
  });

  it("has no check time when no search has run", () => {
    const view = backgroundView(searchesRead(ok([search("a")])), ok([]));
    expect(view.searches).toMatchObject({ checkedAt: null });
    if (view.searches.state !== "ready") throw new Error("expected searches");
    expect(view.searches.entries[0].status).toBe("not_checked");
  });

  it("reads Gmail from the credentials in its three states, and as its own failure", () => {
    const gmail = (rows: unknown) => backgroundView({ state: "loading" }, rows as never).gmail;
    expect(gmail(loading)).toEqual({ state: "loading" });
    expect(gmail(failed)).toEqual({ state: "failed" });
    expect(gmail(ok([]))).toEqual({ state: "ready", connection: "none" });
    expect(gmail(ok([{ service: "oauth", provider: "gmail", is_validated: true, scope: "https://www.googleapis.com/auth/gmail.readonly" }]))).toEqual({ state: "ready", connection: "connected" });
    expect(gmail(ok([{ service: "oauth", provider: "gmail", is_validated: false, scope: "https://www.googleapis.com/auth/gmail.readonly" }]))).toEqual({ state: "ready", connection: "reconnect_needed" });
    expect(gmail(ok([{ service: "oauth", provider: "gmail", is_validated: true, scope: null }]))).toEqual({ state: "ready", connection: "reconnect_needed" });
  });

  it("passes a loading or failed searches read through unchanged", () => {
    expect(backgroundView({ state: "loading" }, ok([])).searches).toEqual({ state: "loading" });
    expect(backgroundView({ state: "failed" }, ok([])).searches).toEqual({ state: "failed" });
  });

  describe("the words for a check", () => {
    it("is the time for today, and the day with the time for any other", () => {
      expect(checkedWords("2026-09-22T06:00:00Z", NOW, UTC)).toBe("checked 06:00");
      expect(checkedWords("2026-09-21T06:00:00Z", NOW, UTC)).toBe("checked Sep 21, 06:00");
      expect(checkedWords("garbage", NOW, UTC)).toBeNull();
    });

    it("says what a search is, in the words a person reads", () => {
      expect(searchStatusWords({ label: "a", status: "paused", at: null }, NOW, UTC)).toBe("Paused");
      expect(searchStatusWords({ label: "a", status: "not_checked", at: null }, NOW, UTC)).toBe("Not checked yet");
      expect(searchStatusWords({ label: "a", status: "checked", at: "2026-09-22T06:00:00Z" }, NOW, UTC)).toBe("Checked 06:00");
      expect(searchStatusWords({ label: "a", status: "checked", at: "garbage" }, NOW, UTC)).toBe("Not checked yet");
    });
  });

  describe("the saved-search line", () => {
    const ready = (rows: unknown[]) => {
      const view = backgroundView(searchesRead(ok(rows as Record<string, unknown>[])), ok([]));
      if (view.searches.state !== "ready") throw new Error("expected searches");
      return view.searches;
    };

    it("is a model's work on the person's key, counted, with its last check", () => {
      const line = searchesTrail(ready([search("a", { last_matched_at: "2026-09-22T06:00:00Z" }), search("b", { is_active: false })]), NOW, UTC);
      expect(buildTrail(line?.trail ?? {})?.text).toBe("1 saved search active · scored by your model on your key · checked 06:00 · 1 paused");
      expect(buildTrail(line?.trail ?? {})?.glyphs).toEqual(["model"]);
      expect(buildReceipt(line?.receipt ?? [])?.rows).toEqual([
        { label: '"a"', value: "Checked 06:00" },
        { label: '"b"', value: "Paused" },
      ]);
    });

    it("says 'not checked yet' for searches that have not run", () => {
      const line = searchesTrail(ready([search("a"), search("b")]), NOW, UTC);
      expect(buildTrail(line?.trail ?? {})?.text).toBe("2 saved searches active · scored by your model on your key · not checked yet");
    });

    it("makes no claim of checking when every search is paused", () => {
      const line = searchesTrail(ready([search("a", { is_active: false })]), NOW, UTC);
      expect(buildTrail(line?.trail ?? {})?.text).toBe("No saved search is active · scored by your model on your key · 1 paused");
    });

    it("is nothing when there is no saved search", () => {
      expect(searchesTrail(ready([]), NOW, UTC)).toBeNull();
    });
  });

  it("writes the client time as the band's failure line does", () => {
    expect(clockTime(new Date("2026-09-22T09:14:00"))).toBe("09:14");
  });
});

describe("what a row can do", () => {
  const ids = (actions: ReturnType<typeof rowActions>) => actions.map((action) => (action.id === "link" ? `link:${action.label}:${action.to}` : action.id));

  it("lets a proposal with a stage be accepted, and one without only be dismissed", () => {
    const mappable = item({ id: "p", kind: "status_proposal", status_proposal: { id: "s", proposed_type: "offer.received", confidence: 0.6, evidence_spans: [], status: "pending" } });
    const unmappable = item({ id: "p", kind: "status_proposal", status_proposal: { id: "s", proposed_type: "recruiter.replied", confidence: 0.6, evidence_spans: [], status: "pending" } });
    expect(rowActions(mappable, null)).toEqual([{ id: "accept", label: "Accept: move to Offer" }, { id: "dismiss", label: "Dismiss" }]);
    expect(ids(rowActions(unmappable, null))).toEqual(["dismiss"]);
    expect(ids(rowActions(item({ id: "p", kind: "status_proposal" }), null))).toEqual(["dismiss"]);
  });

  it("offers a proposal that was already decided Dismiss only: Accept would be refused", () => {
    for (const status of ["accepted", "dismissed"] as const) {
      const decided = item({ id: "p", kind: "status_proposal", status_proposal: { id: "s", proposed_type: "offer.received", confidence: 0.6, evidence_spans: [], status } });
      expect(ids(rowActions(decided, null))).toEqual(["dismiss"]);
    }
  });

  it("offers Track for a match, and Open workspace once it is tracked", () => {
    const match = item({ id: "m", kind: "high_fit_job", job_match: MATCH });
    expect(ids(rowActions(match, null))).toEqual(["track", "dismiss"]);
    expect(ids(rowActions(match, "app 9"))).toEqual(["link:Open workspace:/applications/app%209", "dismiss"]);
    expect(ids(rowActions(item({ id: "m", kind: "high_fit_job" }), null))).toEqual(["dismiss"]);
  });

  it("links a ready resume to the workspace's review section, and a held-back one to the fit check", () => {
    expect(ids(rowActions(item({ id: "r", kind: "resume_ready", application_id: "a1" }), null))).toEqual(["link:Open workspace:/applications/a1#ch-check", "dismiss"]);
    expect(ids(rowActions(item({ id: "f", kind: "resume_failed", application_id: "a1" }), null))).toEqual(["link:See why:/applications/a1#generate", "generate-anyway", "dismiss"]);
  });

  it("keeps Dismiss only for an item with no application to open", () => {
    for (const kind of ["resume_ready", "resume_failed", "job_tracked", "stage_changed"] as const) {
      expect(ids(rowActions(item({ id: "x", kind }), null))).toEqual(["dismiss"]);
    }
  });

  it("links a tracked job or a stage change to its workspace", () => {
    for (const kind of ["job_tracked", "stage_changed"] as const) {
      expect(ids(rowActions(item({ id: "x", kind, application_id: "a1" }), null))).toEqual(["link:Open workspace:/applications/a1", "dismiss"]);
    }
  });
});
