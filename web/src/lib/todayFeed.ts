// Today's feed as the page draws it: the items the server returns, what each one says about
// itself, and the facts the next-step rows (nextStep/pages/today.ts) are given.
//
// Pure module, no React and no fetch. Everything on screen is built from a returned field:
//  - the trail line of an item names who acted and on what basis from the item's kind, and its
//    "when" is the item's own `created_at` -- the only date the feed returns;
//  - a job's name comes from the application it belongs to (`application_id`), read from the
//    applications list, never taken out of the server's headline text;
//  - a number appears only where a field carries it (a match's score, a proposal's confidence, a
//    fit check's overall score). Where it is missing the slot is left out, never filled in.

import type { ReceiptRowInput, TrailInput } from "./trail";
import { formatTrailDate, formatTrailTime, mono } from "./trail";
import type { TrackDiscoveredJobBody } from "./discoverTypes";
import { savedSearchLabel } from "./discover";
import { parseSavedSearch } from "./savedSearches";
import { gmailConnection, type GmailConnection, type GmailCredentialRow } from "./gmailAccess";
import { CROSS_NAV_HASH } from "./applicationsBoard";
import {
  AUTO_MOVE_PERCENT,
  REVIEW_HASH,
  confidencePercent,
  TODAY_KINDS,
  isMappableProposal,
  PROPOSAL_STEPS,
  workspacePath,
  type SavedSearchesFact,
  type TodayFacts,
  type TodayItemFact,
  type TodayKind,
} from "./nextStep/pages/today";
import type { Fetched } from "./firstRun";

// ── what the server returns ────────────────────────────────────────────────

export type TodayItemKind = TodayKind;

// The today_item_job_matches companion row -- present only on kind="high_fit_job" items.
export interface JobMatch {
  today_item_id: string;
  saved_search_id: string;
  apply_url: string;
  title: string;
  company: string | null;
  location: string | null;
  score100: number;
  bin: string;
  snippet: string;
  provider: string;
}

// The application_status_proposals row -- present only on kind="status_proposal" items. It is the
// same canonical row Accept and Dismiss act on, not a copy.
export interface StatusProposal {
  id: string;
  proposed_type: string;
  confidence: number;
  evidence_spans: string[];
  status: "pending" | "accepted" | "dismissed";
}

export interface TodayItem {
  id: string;
  kind: TodayItemKind;
  headline: string;
  detail: string | null;
  created_at: string;
  // Null on an item that belongs to no application (a high fit job is not tracked yet), and on a
  // status proposal whose application is gone.
  application_id?: string | null;
  job_match: JobMatch | null;
  status_proposal: StatusProposal | null;
}

export function trackRequestBody(match: JobMatch): TrackDiscoveredJobBody {
  return {
    apply_url: match.apply_url,
    title: match.title,
    company: match.company,
    location: match.location,
    snippet: match.snippet,
    provider: match.provider,
  };
}

// The words for each kind, as the empty state names them too.
export const KIND_LABELS: Readonly<Record<TodayItemKind, string>> = {
  high_fit_job: "High fit job",
  status_proposal: "Status proposal",
  resume_ready: "Resume ready",
  resume_failed: "Resume not generated",
  job_tracked: "Job tracked",
  stage_changed: "Stage changed",
};

export function kindLabel(kind: string): string {
  return Object.prototype.hasOwnProperty.call(KIND_LABELS, kind) ? KIND_LABELS[kind as TodayItemKind] : kind.replace(/_/g, " ");
}

export function isKnownKind(kind: string): kind is TodayItemKind {
  return (TODAY_KINDS as readonly string[]).includes(kind);
}

// ── what the other reads add ───────────────────────────────────────────────

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function text(value: unknown): string | null {
  return typeof value === "string" && value.trim() !== "" ? value.trim() : null;
}

export interface ApplicationName {
  title: string | null;
  company: string | null;
}

// The title and company of each application, from the applications list (each row's snapshot).
export function applicationNames(rows: readonly Record<string, unknown>[]): Map<string, ApplicationName> {
  const names = new Map<string, ApplicationName>();
  for (const row of rows) {
    const id = text(row.id);
    if (id === null) continue;
    const snapshot = isRecord(row.snapshot) ? row.snapshot : {};
    names.set(id, { title: text(snapshot.title), company: text(snapshot.company_name) });
  }
  return names;
}

// "Title at Company", or the title alone; null when the application has no title to name.
export function jobLabel(name: ApplicationName | undefined): string | null {
  if (name === undefined || name.title === null) return null;
  return name.company === null ? name.title : `${name.title} at ${name.company}`;
}

// One saved search as Today reads it: enough to label it, count it, and say when it last ran.
export interface BackgroundSearch {
  id: string;
  label: string;
  active: boolean;
  createdAt: string;
  /** The matcher's own time for its last run; null when it has not run. */
  lastMatchedAt: string | null;
}

// Null when a row is not a search: the whole list is then unknown, never a shorter list than the
// server has (the same rule the Integrations card applies).
export function backgroundSearches(rows: readonly Record<string, unknown>[]): BackgroundSearch[] | null {
  const searches: BackgroundSearch[] = [];
  for (const row of rows) {
    const search = parseSavedSearch(row);
    if (search === null) return null;
    searches.push({
      id: search.id,
      label: savedSearchLabel(search),
      active: search.is_active,
      createdAt: search.created_at,
      lastMatchedAt: text(row.last_matched_at),
    });
  }
  return searches;
}

// A search has been checked only when its last run is later than the search itself: the matcher
// sets the time to the start of each run, and a search that was never run keeps its creation time.
export function checkedAt(search: BackgroundSearch): string | null {
  if (search.lastMatchedAt === null) return null;
  const matched = Date.parse(search.lastMatchedAt);
  const created = Date.parse(search.createdAt);
  if (Number.isNaN(matched) || Number.isNaN(created)) return null;
  return matched > created ? search.lastMatchedAt : null;
}

export type SearchesRead =
  | { state: "loading" }
  | { state: "failed" }
  | { state: "ready"; searches: readonly BackgroundSearch[] };

export function searchesRead(read: Fetched<readonly Record<string, unknown>[]>): SearchesRead {
  if (read.kind !== "ok") return read.kind === "loading" ? { state: "loading" } : { state: "failed" };
  const searches = backgroundSearches(read.value);
  return searches === null ? { state: "failed" } : { state: "ready", searches };
}

export function savedSearchesFact(read: SearchesRead): SavedSearchesFact {
  if (read.state !== "ready") return read;
  return { state: "ready", total: read.searches.length, active: read.searches.filter((search) => search.active).length };
}

// ── the facts the rows read ────────────────────────────────────────────────

export interface FactsInput {
  items: readonly TodayItem[] | null;
  applications: Fetched<readonly Record<string, unknown>[]>;
  searches: SearchesRead;
  /** The application a Track in this visit created, by the apply URL it was for. */
  tracked: Readonly<Record<string, string>>;
}

// The ATS score the server writes into a resume item's detail ("ATS score 72/100"); null for any
// other text, so a changed sentence costs the score and never makes one up.
export function atsScoreOf(detail: string | null): number | null {
  if (detail === null) return null;
  const match = /^ATS score (\d{1,3})\/100$/.exec(detail.trim());
  return match === null ? null : Number(match[1]);
}

export function todayFacts(input: FactsInput): TodayFacts {
  const names = input.applications.kind === "ok" ? applicationNames(input.applications.value) : new Map<string, ApplicationName>();
  const labels = new Map(input.searches.state === "ready" ? input.searches.searches.map((search) => [search.id, search.label] as const) : []);
  const items: TodayItemFact[] | null =
    input.items === null
      ? null
      : input.items.map((item) => {
          const applicationId = text(item.application_id);
          const name = applicationId === null ? undefined : names.get(applicationId);
          const match = item.job_match;
          return {
            id: item.id,
            kind: item.kind,
            createdAt: item.created_at,
            applicationId,
            job: jobLabel(name),
            company: name?.company ?? null,
            proposal:
              item.status_proposal === null
                ? null
                : {
                    proposedType: item.status_proposal.proposed_type,
                    confidence: item.status_proposal.confidence,
                    status: item.status_proposal.status,
                  },
            match:
              match === null
                ? null
                : {
                    title: match.title,
                    company: match.company,
                    score100: match.score100,
                    bin: match.bin,
                    searchLabel: labels.get(match.saved_search_id) ?? null,
                  },
            atsScore: item.kind === "resume_ready" ? atsScoreOf(item.detail) : null,
            trackedApplicationId: match === null ? null : (input.tracked[match.apply_url] ?? null),
          };
        });
  return {
    feed: items === null ? { state: "loading" } : { state: "ready", items },
    names: input.applications.kind === "loading" ? "loading" : "settled",
    savedSearches: savedSearchesFact(input.searches),
  };
}

// ── one item, as a row ─────────────────────────────────────────────────────

// What the fit check's own read said about a held-back item, when it could be read.
export type FitRead = { state: "loading" } | { state: "unavailable" } | { state: "held"; score: number };

// The number the "Resume not generated" chip carries, from GET /applications/{id}/prepare-result:
// the fit check's overall score on its own 0 to 10 scale, and only while that latest result is
// still a held-back one (no resume, and the gate did not let it through). A newer run that
// produced a resume, a result with no fit, or a score that is not a number is "unavailable": the
// chip then says only that the fit check held it back.
export function fitReadOf(body: unknown): FitRead {
  if (!isRecord(body) || !isRecord(body.result)) return { state: "unavailable" };
  const result = body.result;
  if (result.resume !== null && result.resume !== undefined) return { state: "unavailable" };
  if (result.gate_outcome === "proceed") return { state: "unavailable" };
  const fit = isRecord(result.fit) ? result.fit : null;
  const score = fit === null ? null : fit.overall_score;
  return typeof score === "number" && Number.isFinite(score) ? { state: "held", score } : { state: "unavailable" };
}

// A 0 to 10 score as the fit check wrote it, to one decimal place at most.
export function formatTenScale(score: number): string {
  return String(Math.round(score * 10) / 10);
}

export interface RowContext {
  /** The moment "this year" is read from, for the trail's dates. */
  now: Date;
  names: ReadonlyMap<string, ApplicationName>;
  searchLabels: ReadonlyMap<string, string>;
  fits: Readonly<Record<string, FitRead | undefined>>;
  timeZone?: string;
}

export interface ChipView {
  tone: "gold";
  text: string;
}

export interface RowView {
  kind: TodayItemKind | string;
  label: string;
  chip: ChipView | null;
  /** The headline, or for a held-back resume the job (the server's headline says "needs attention"). */
  title: string;
  serifTitle: boolean;
  details: string[];
  quotes: string[];
  trail: TrailInput;
  receipt: ReceiptRowInput[];
}

// Whether a proposal can still be accepted or dismissed through the proposal routes: only a
// pending one. The server refuses both for any other status (409).
export function isPending(proposal: StatusProposal | null): proposal is StatusProposal {
  return proposal !== null && proposal.status === "pending";
}

export function rowView(item: TodayItem, context: RowContext): RowView {
  const when = formatTrailDate(item.created_at, { now: context.now, timeZone: context.timeZone });
  const applicationId = text(item.application_id);
  const job = applicationId === null ? null : jobLabel(context.names.get(applicationId));
  const logged: ReceiptRowInput = { label: "logged", value: text(item.created_at) };
  const base = { kind: item.kind, label: kindLabel(item.kind), chip: null, title: item.headline, serifTitle: false, details: [] as string[], quotes: [] as string[] };
  const detail = text(item.detail);

  switch (item.kind) {
    case "job_tracked":
    case "stage_changed":
      return {
        ...base,
        details: detail === null ? [] : [detail],
        trail: { glyph: "code", actor: "Logged by code", basis: "no model involved", when },
        receipt: [{ label: "kind", value: item.kind }, logged],
      };
    case "resume_ready":
      return {
        ...base,
        chip: { tone: "gold", text: "Ready for your review" },
        details: detail === null ? [] : [detail],
        trail: { glyph: "model", actor: "Drafted by your model", basis: "on your key", when },
        receipt: [{ label: "kind", value: item.kind }, logged],
      };
    case "resume_failed": {
      const fit = applicationId === null ? undefined : context.fits[applicationId];
      return {
        ...base,
        // The server's headline reads "needs attention"; the card is calm and says what happened.
        title: job ?? "An application you track",
        serifTitle: true,
        chip: { tone: "gold", text: fit?.state === "held" ? `Fit check held it back · ${formatTenScale(fit.score)} / 10` : "Fit check held it back" },
        details: detail === null ? [] : [detail],
        trail: { glyph: "engine", actor: "Held back by the fit check", basis: "code-weighted from your model's sub-scores", when },
        receipt: [
          { label: "kind", value: item.kind },
          logged,
          { label: "fit check", value: fit?.state === "held" ? `${formatTenScale(fit.score)} / 10` : null },
        ],
      };
    }
    case "high_fit_job": {
      const match = item.job_match;
      const searchLabel = match === null ? null : (context.searchLabels.get(match.saved_search_id) ?? null);
      const location = match?.location ? ` -- ${match.location}` : "";
      return {
        ...base,
        chip: match === null ? null : { tone: "gold", text: `${match.bin} -- ${match.score100} / 100` },
        details: [
          ...(match === null ? [] : [`${match.company ?? "Unknown company"}${location}`]),
          ...(detail === null ? [] : [detail]),
        ],
        trail: {
          glyph: "engine",
          actor: searchLabel === null ? "Found by a saved search" : `Found by saved search ${searchLabel}`,
          basis: match === null ? null : `via ${match.provider}`,
          when,
        },
        receipt: [
          { label: "kind", value: item.kind },
          logged,
          { label: "provider", value: match?.provider },
          { label: "bin", value: match?.bin },
          { label: "score", value: match === null ? null : `${match.score100} / 100` },
          { label: "saved search", value: searchLabel },
        ],
      };
    }
    case "status_proposal": {
      const proposal = item.status_proposal;
      const percent = proposal === null ? null : confidencePercent(proposal.confidence);
      const mappable = proposal !== null && isMappableProposal(proposal.proposed_type);
      return {
        ...base,
        // Waiting on the person only while it is still pending; a decided one is just a record.
        chip: mappable && isPending(proposal) ? { tone: "gold", text: "Needs your decision" } : null,
        quotes: proposal === null ? [] : proposal.evidence_spans.filter((span) => span.trim() !== ""),
        trail: {
          glyph: "model",
          actor: "Read by your model",
          basis: "from a Gmail reply",
          when,
          extra:
            percent === null
              ? null
              : proposal !== null && proposal.confidence < AUTO_MOVE_PERCENT / 100
                ? `${percent}% · under the ${AUTO_MOVE_PERCENT}% auto-move line`
                : `${percent}%`,
        },
        receipt: [
          { label: "kind", value: item.kind },
          logged,
          { label: "proposed", value: proposal?.proposed_type },
          { label: "confidence", value: proposal === null || percent === null ? null : `${percent}%` },
          { label: "status", value: proposal?.status },
          { label: "moves to", value: proposal !== null && mappable ? PROPOSAL_STEPS[proposal.proposed_type].stage : null },
        ],
      };
    }
    default:
      return {
        ...base,
        details: detail === null ? [] : [detail],
        trail: { glyph: "code", actor: "Logged by code", basis: "no model involved", when },
        receipt: [{ label: "kind", value: item.kind }, logged],
      };
  }
}

// ── what a row can do ──────────────────────────────────────────────────────

// The controls of an item, in the order they are drawn. A link goes to the application's workspace;
// the others are handlers the page owns. "dismiss" is last and is the only one the band repeats for
// the item it holds (the band carries the item's other actions itself).
export type RowAction =
  | { id: "link"; label: string; to: string }
  | { id: "accept" | "track" | "generate-anyway" | "dismiss"; label: string };

export function rowActions(item: TodayItem, trackedApplicationId: string | null): RowAction[] {
  const applicationId = text(item.application_id);
  const actions: RowAction[] = [];
  switch (item.kind) {
    case "status_proposal": {
      const type = item.status_proposal?.proposed_type;
      if (type !== undefined && isMappableProposal(type) && isPending(item.status_proposal)) actions.push({ id: "accept", label: `Accept: move to ${PROPOSAL_STEPS[type].stage}` });
      break;
    }
    case "high_fit_job":
      if (item.job_match !== null) {
        actions.push(
          trackedApplicationId === null
            ? { id: "track", label: "Track" }
            : { id: "link", label: "Open workspace", to: workspacePath(trackedApplicationId) },
        );
      }
      break;
    case "resume_ready":
      if (applicationId !== null) actions.push({ id: "link", label: "Open workspace", to: workspacePath(applicationId, REVIEW_HASH) });
      break;
    case "resume_failed":
      if (applicationId !== null) {
        actions.push({ id: "link", label: "See why", to: workspacePath(applicationId, CROSS_NAV_HASH.generate) });
        actions.push({ id: "generate-anyway", label: "Generate anyway" });
      }
      break;
    default:
      if (applicationId !== null) actions.push({ id: "link", label: "Open workspace", to: workspacePath(applicationId) });
  }
  actions.push({ id: "dismiss", label: "Dismiss" });
  return actions;
}

// ── the background trail ───────────────────────────────────────────────────

export type SearchStatus = "paused" | "checked" | "not_checked";

export interface SearchEntry {
  label: string;
  status: SearchStatus;
  /** The matcher's last run, when the search has been checked. */
  at: string | null;
}

export interface BackgroundView {
  searches:
    | { state: "loading" }
    | { state: "failed" }
    | {
        state: "ready";
        total: number;
        active: number;
        paused: number;
        /** The newest time any search was checked; null when none has been. */
        checkedAt: string | null;
        entries: readonly SearchEntry[];
      };
  gmail: { state: "loading" } | { state: "failed" } | { state: "ready"; connection: GmailConnection["kind"] };
}

// The fields of a credential row the Gmail check reads. A row missing one is read as it is: a
// missing `is_validated` is not `false`, so a row the server did not mark as ended is not called ended.
function gmailRowOf(row: Record<string, unknown>): GmailCredentialRow {
  return {
    service: typeof row.service === "string" ? row.service : "",
    provider: typeof row.provider === "string" ? row.provider : "",
    is_validated: row.is_validated !== false,
    scope: typeof row.scope === "string" ? row.scope : null,
  };
}

export function backgroundView(searches: SearchesRead, credentials: Fetched<readonly Record<string, unknown>[]>): BackgroundView {
  const gmail: BackgroundView["gmail"] =
    credentials.kind === "loading"
      ? { state: "loading" }
      : credentials.kind === "failed"
        ? { state: "failed" }
        : { state: "ready", connection: gmailConnection(credentials.value.map(gmailRowOf)).kind };
  if (searches.state !== "ready") return { searches, gmail };
  const entries: SearchEntry[] = searches.searches.map((search) => {
    const at = checkedAt(search);
    return { label: search.label, status: !search.active ? "paused" : at === null ? "not_checked" : "checked", at };
  });
  const times = searches.searches.flatMap((search) => {
    const at = checkedAt(search);
    return at === null ? [] : [at];
  });
  const newest = times.reduce<string | null>((best, at) => (best === null || Date.parse(at) > Date.parse(best) ? at : best), null);
  const active = searches.searches.filter((search) => search.active).length;
  return {
    searches: { state: "ready", total: searches.searches.length, active, paused: searches.searches.length - active, checkedAt: newest, entries },
    gmail,
  };
}

// "checked 06:00" for a time today, "checked Sep 21, 06:00" for another day: a bare clock time
// from an earlier day would read as fresher than it is.
export function checkedWords(at: string, now: Date, timeZone?: string): string | null {
  const time = formatTrailTime(at, { timeZone });
  if (time === null) return null;
  const day = formatTrailDate(at, { now, timeZone });
  const today = formatTrailDate(now.toISOString(), { now, timeZone });
  return day === null || day === today ? `checked ${time}` : `checked ${day}, ${time}`;
}

export function searchStatusWords(entry: SearchEntry, now: Date, timeZone?: string): string {
  if (entry.status === "paused") return "Paused";
  if (entry.status === "not_checked" || entry.at === null) return "Not checked yet";
  const words = checkedWords(entry.at, now, timeZone);
  return words === null ? "Not checked yet" : `${words[0].toUpperCase()}${words.slice(1)}`;
}

function plural(count: number, one: string, many: string): string {
  return `${count} ${count === 1 ? one : many}`;
}

// The saved-search line of the trail, and the receipt that opens under it.
export function searchesTrail(view: Extract<BackgroundView["searches"], { state: "ready" }>, now: Date, timeZone?: string): { trail: TrailInput; receipt: ReceiptRowInput[] } | null {
  if (view.total === 0) return null;
  const when =
    view.checkedAt === null
      ? "not checked yet"
      : (checkedWords(view.checkedAt, now, timeZone) ?? "not checked yet");
  return {
    trail: {
      glyph: "model",
      actor: view.active === 0 ? "No saved search is active" : `${plural(view.active, "saved search", "saved searches")} active`,
      basis: "scored by your model on your key",
      when: view.active === 0 ? null : mono(when),
      extra: view.paused > 0 ? `${view.paused} paused` : null,
    },
    receipt: view.entries.map((entry) => ({ label: entry.label, value: searchStatusWords(entry, now, timeZone) })),
  };
}

// The client's own wall-clock time, as the band's failure line writes it: "09:14".
export function clockTime(now: Date): string {
  return formatTrailTime(now.toISOString()) ?? "";
}
