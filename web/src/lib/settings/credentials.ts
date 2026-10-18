// A saved credential as GET /credentials returns it (the API strips the ciphertext). The model keys
// carry a `setup_state`; the others do not.

export interface CredentialSummary {
  id: string;
  service: string;
  provider: string;
  model: string | null;
  is_validated: boolean;
  updated_at: string;
  // OAuth-specific (Gmail reply/status parsing) -- the real scope string Google granted, captured at
  // connect time. null for every credential saved before this field existed, or for a non-OAuth
  // BYOK credential that never had a scope concept at all.
  scope: string | null;
  // The address a model key goes to, for the providers where it is stored (Qwen's region, the
  // custom provider's host). Absent or null for the rest.
  base_url?: string | null;
  setup_state?: "ready" | "needs_model" | "needs_key";
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

// The rows of a GET /credentials answer that have the fields a row needs, or null when it is not a
// list. A row that cannot be read is left out: it is not drawn as a key the person does not have.
export function parseCredentialRows(body: unknown): CredentialSummary[] | null {
  if (!Array.isArray(body)) return null;
  const rows: CredentialSummary[] = [];
  for (const raw of body) {
    if (!isRecord(raw)) continue;
    if (typeof raw.service !== "string" || typeof raw.provider !== "string") continue;
    rows.push({
      id: typeof raw.id === "string" ? raw.id : `${raw.service}/${raw.provider}`,
      service: raw.service,
      provider: raw.provider,
      model: typeof raw.model === "string" && raw.model !== "" ? raw.model : null,
      is_validated: raw.is_validated === true,
      updated_at: typeof raw.updated_at === "string" ? raw.updated_at : "",
      scope: typeof raw.scope === "string" ? raw.scope : null,
      base_url: typeof raw.base_url === "string" ? raw.base_url : null,
      ...(raw.setup_state === "ready" || raw.setup_state === "needs_model" || raw.setup_state === "needs_key"
        ? { setup_state: raw.setup_state }
        : {}),
    });
  }
  return rows;
}
