// The model-provider side of the Settings page, as pure functions: what the API's catalogue and
// model lists say, read defensively; the setup state of a saved key; the words around a model.
//
// EVERYTHING IN HERE THAT CAME FROM A PROVIDER IS UNTRUSTED TEXT. The catalogue is the API's own
// (src/between_jobs/api/provider_registry.py), but a model list is a provider's answer, relayed:
// its ids and names are strings a stranger chose. They are checked for shape here, cut to a sane
// length, and drawn as text by React and nowhere else (never as markup, never in an attribute that
// is a URL or a handler). The addresses that become links are held to https.

import type { CredentialSummary } from "./credentials";

// ── the catalogue: GET /credentials/llm/providers ────────────────────────

export interface Region {
  id: string;
  name: string;
  needsWorkspace: boolean;
  getKeyUrl: string | null;
}

export interface CustomEndpoint {
  id: string;
  name: string;
  baseUrl: string;
  getKeyUrl: string | null;
}

export interface LlmProvider {
  id: string;
  name: string;
  /** In words: what a key looks like. Never enforced here. */
  keyHint: string;
  getKeyUrl: string | null;
  manageUrl: string | null;
  /** "best_effort": the provider's list route is undocumented or varies, so no list is possible. */
  listingIsBestEffort: boolean;
  regions: readonly Region[];
  endpoints: readonly CustomEndpoint[];
}

const MAX_TEXT = 200;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function text(value: unknown, max = MAX_TEXT): string | null {
  if (typeof value !== "string") return null;
  const trimmed = value.trim();
  return trimmed === "" ? null : trimmed.slice(0, max);
}

// An address that may become a link: https, a host, no credentials in it. Anything else is no link.
export function safeHttpsUrl(value: unknown): string | null {
  if (typeof value !== "string" || value.length > 500) return null;
  try {
    const url = new URL(value);
    if (url.protocol !== "https:" || url.hostname === "" || url.username !== "" || url.password !== "") return null;
    return url.href;
  } catch {
    return null;
  }
}

function parseRegion(raw: unknown): Region | null {
  if (!isRecord(raw)) return null;
  const id = text(raw.id, 32);
  const name = text(raw.name);
  if (id === null || name === null) return null;
  return { id, name, needsWorkspace: raw.needs_workspace === true, getKeyUrl: safeHttpsUrl(raw.get_key_url) };
}

function parseEndpoint(raw: unknown): CustomEndpoint | null {
  if (!isRecord(raw)) return null;
  const id = text(raw.id, 32);
  const name = text(raw.name);
  const baseUrl = safeHttpsUrl(raw.base_url);
  if (id === null || name === null || baseUrl === null) return null;
  return { id, name, baseUrl: raw.base_url as string, getKeyUrl: safeHttpsUrl(raw.get_key_url) };
}

function parseProvider(raw: unknown): LlmProvider | null {
  if (!isRecord(raw)) return null;
  const id = text(raw.id, 32);
  const name = text(raw.name);
  if (id === null || name === null) return null;
  return {
    id,
    name,
    keyHint: text(raw.key_hint, 400) ?? "",
    getKeyUrl: safeHttpsUrl(raw.get_key_url),
    manageUrl: safeHttpsUrl(raw.manage_url),
    listingIsBestEffort: raw.model_list === "best_effort",
    regions: Array.isArray(raw.regions) ? raw.regions.map(parseRegion).filter((r): r is Region => r !== null) : [],
    endpoints: Array.isArray(raw.endpoints) ? raw.endpoints.map(parseEndpoint).filter((e): e is CustomEndpoint => e !== null) : [],
  };
}

// The providers of a catalogue answer, in the order the server listed them, or null when the answer
// is not a catalogue at all. An entry that cannot be read is left out rather than guessed at.
export function parseProviderCatalog(body: unknown): LlmProvider[] | null {
  if (!isRecord(body) || !Array.isArray(body.providers)) return null;
  const seen = new Set<string>();
  const providers: LlmProvider[] = [];
  for (const raw of body.providers) {
    const provider = parseProvider(raw);
    if (provider === null || seen.has(provider.id)) continue;
    seen.add(provider.id);
    providers.push(provider);
  }
  return providers;
}

export const OPENROUTER_ID = "openrouter";
export const QWEN_ID = "qwen";
export const CUSTOM_ID = "custom";

// ── a saved key's address ────────────────────────────────────────────────

const withoutTrailingSlash = (url: string) => url.replace(/\/+$/, "");

// The custom provider's host a saved address is, or null when it is none of the allowed ones.
export function endpointForAddress(provider: LlmProvider | undefined, baseUrl: string | null | undefined): CustomEndpoint | null {
  if (provider === undefined || provider.id !== CUSTOM_ID || typeof baseUrl !== "string" || baseUrl === "") return null;
  const wanted = withoutTrailingSlash(baseUrl);
  return provider.endpoints.find((endpoint) => withoutTrailingSlash(endpoint.baseUrl) === wanted) ?? null;
}

// Qwen's region a saved address was built for, or null when it is none of the listed ones. QwenCloud
// has one address; a Model Studio key's is "<workspace>.<region>.maas.aliyuncs.com".
export function regionForAddress(provider: LlmProvider | undefined, baseUrl: string | null | undefined): Region | null {
  if (provider === undefined || provider.id !== QWEN_ID || typeof baseUrl !== "string" || baseUrl === "") return null;
  let host: string;
  try {
    host = new URL(baseUrl).hostname.toLowerCase();
  } catch {
    return null;
  }
  if (host === "maas.qwencloudapi.com") return provider.regions.find((region) => !region.needsWorkspace) ?? null;
  const match = /^[^.]+\.([a-z0-9-]+)\.maas\.aliyuncs\.com$/.exec(host);
  return match === null ? null : (provider.regions.find((region) => region.needsWorkspace && region.id === match[1]) ?? null);
}

// ── a saved key's setup state ────────────────────────────────────────────

export type LlmSetupState = "ready" | "needs_model" | "needs_key";

// The server's `setup_state` when it sent one we know. Otherwise the same rule it uses
// (credential_resolver.llm_setup_state): checked and with a model is ready, checked without one
// needs a model, anything else needs the key.
export function llmSetupState(row: Pick<CredentialSummary, "is_validated" | "model"> & { setup_state?: unknown }): LlmSetupState {
  if (row.setup_state === "ready" || row.setup_state === "needs_model" || row.setup_state === "needs_key") return row.setup_state;
  if (row.is_validated !== true) return "needs_key";
  return typeof row.model === "string" && row.model !== "" ? "ready" : "needs_model";
}

export function llmRows(rows: readonly CredentialSummary[]): CredentialSummary[] {
  return rows.filter((row) => row.service === "llm");
}

export function searchRows(rows: readonly CredentialSummary[]): CredentialSummary[] {
  return rows.filter((row) => row.service === "search");
}

// The name to show for a provider id the catalogue may not have (while it loads, or when it failed).
export function providerName(id: string, catalog: readonly LlmProvider[] | null): string {
  return catalog?.find((provider) => provider.id === id)?.name ?? id;
}

// ── models: POST /credentials/llm/models ─────────────────────────────────

export interface ModelEntry {
  id: string;
  name: string;
  contextLength: number | null;
  created: number | null;
}

export interface ModelList {
  provider: string;
  /** "unavailable": this provider has no list route; the person types a model id. */
  listing: "live" | "unavailable";
  /** The server cut the list (at 500, newest first). */
  truncated: boolean;
  models: readonly ModelEntry[];
  /** Entries the provider sent that were left out because their id is not one this page can use. */
  skipped: number;
}

// The server's own rule for a model id (models.py MODEL_ID_PATTERN), so that an id typed here is
// refused before a request and an id in a provider's list that the server would refuse is not offered.
export const MODEL_ID_PATTERN = /^(?!.*(?:\.\.|\/\/))[A-Za-z0-9][A-Za-z0-9._:/@+~-]{0,199}$/;

export function isModelId(value: string): boolean {
  return MODEL_ID_PATTERN.test(value);
}

function positiveInt(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) && value > 0 ? Math.floor(value) : null;
}

export function parseModelList(body: unknown): ModelList | null {
  if (!isRecord(body) || !Array.isArray(body.models) || typeof body.provider !== "string") return null;
  const listing = body.listing === "unavailable" ? "unavailable" : "live";
  const models: ModelEntry[] = [];
  const seen = new Set<string>();
  let skipped = 0;
  for (const raw of body.models) {
    if (!isRecord(raw) || typeof raw.id !== "string" || !isModelId(raw.id)) {
      skipped += 1;
      continue;
    }
    if (seen.has(raw.id)) continue;
    seen.add(raw.id);
    const name = text(raw.name, 120);
    models.push({
      id: raw.id,
      // A name that only repeats the id adds nothing; it is dropped so the row shows the id once.
      name: name === null || name === raw.id ? "" : name,
      contextLength: positiveInt(raw.context_length),
      created: positiveInt(raw.created),
    });
  }
  return { provider: body.provider, listing, truncated: body.truncated === true, models, skipped };
}

// The models matching what the person typed: every word of the query must appear in the id or the
// name, in any order, ignoring case. An empty query matches everything.
export function filterModels(models: readonly ModelEntry[], query: string): ModelEntry[] {
  const words = query.toLowerCase().split(/\s+/).filter((word) => word !== "");
  if (words.length === 0) return [...models];
  return models.filter((model) => {
    const haystack = `${model.id} ${model.name}`.toLowerCase();
    return words.every((word) => haystack.includes(word));
  });
}

// 128000 -> "128K tokens", 1048576 -> "1M tokens", 8192 -> "8K tokens". Null when none was returned.
export function formatContext(length: number | null): string | null {
  if (length === null) return null;
  if (length >= 1_000_000) {
    const millions = Math.round((length / 1_000_000) * 10) / 10;
    return `${Number.isInteger(millions) ? millions : millions.toFixed(1)}M tokens`;
  }
  if (length >= 1000) return `${Math.round(length / 1000)}K tokens`;
  return `${length} tokens`;
}

// How many rows of a long list are drawn at once. The rest are reached by the filter.
export const MODEL_ROWS_SHOWN = 100;
