import { useCallback, useEffect, useState } from "react";
import { apiFetch, ApiError } from "../lib/api";

// Integrations (Sprint 2.7f, widened Horizon Sprint 5.0) -- the web
// surface over Sprint 2.7's BYOK credential broker (Proposal §11).
// Reachable at /profile/integrations, matching credential_resolver.py's
// own settings_path convention exactly (SETUP_REQUIRED errors will one
// day deep-link straight here). Not a top-level nav item -- the design
// book's information architecture is a fixed five: Today/Discover/
// Applications/Practice/Profile: this is a sub-page of Profile, not a
// sixth destination.
//
// OpenRouter (LLM) plus You.com and Firecrawl (search, for the company
// intelligence pipeline) are wired up. Both search cards share
// SearchProviderCard -- unlike OpenRouter, neither has a "model" concept,
// just a key -- rather than forcing them through OpenRouterCard's shape.
//
// Sprint 2.8f adds the Telegram-link card on the same page -- both cards
// are "connect an external account to this one," the same loose theme
// the page's own title already claims, so this doesn't need a second
// route to earn its place.

interface CredentialSummary {
  id: string;
  service: string;
  provider: string;
  model: string | null;
  is_validated: boolean;
  updated_at: string;
}

export default function Integrations() {
  const [credentials, setCredentials] = useState<CredentialSummary[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const rows = await apiFetch<CredentialSummary[]>("/credentials");
      setCredentials(rows);
      setLoadError(null);
    } catch (e) {
      setLoadError(e instanceof Error ? e.message : "Failed to load integrations");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const openrouter = credentials?.find((c) => c.service === "llm" && c.provider === "openrouter") ?? null;
  const youCom = credentials?.find((c) => c.service === "search" && c.provider === "you_com") ?? null;
  const firecrawl = credentials?.find((c) => c.service === "search" && c.provider === "firecrawl") ?? null;
  const apollo = credentials?.find((c) => c.service === "search" && c.provider === "apollo") ?? null;
  const gmail = credentials?.find((c) => c.service === "oauth" && c.provider === "gmail") ?? null;

  return (
    <div>
      <h1>Integrations</h1>
      <p className="bj-muted">
        Bring your own provider key -- this platform never runs anything on a shared key on your
        behalf.
      </p>
      {loadError && <div className="bj-error">{loadError}</div>}
      {credentials !== null && <OpenRouterCard credential={openrouter} onChanged={load} />}
      {credentials !== null && (
        <SearchProviderCard
          title="You.com"
          provider="you_com"
          placeholder="your-you-com-key"
          note="Validating costs a small real charge (~$0.005) -- a live search call, since You.com has no free key-check endpoint."
          credential={youCom}
          onChanged={load}
        />
      )}
      {credentials !== null && (
        <SearchProviderCard
          title="Firecrawl"
          provider="firecrawl"
          placeholder="fc-..."
          note="Validated against your account's credit usage -- doesn't spend a search/scrape credit."
          credential={firecrawl}
          onChanged={load}
        />
      )}
      {credentials !== null && (
        <SearchProviderCard
          title="Apollo"
          provider="apollo"
          placeholder="your-apollo-key"
          note="Used only for contact enrichment, one already-selected person at a time -- never a bulk search. Validated against Apollo's free health-check endpoint, at no cost."
          credential={apollo}
          onChanged={load}
        />
      )}
      <TelegramLinkCard />
      <GmailConnectCard credential={gmail} onChanged={load} />
    </div>
  );
}

function SearchProviderCard({
  title,
  provider,
  placeholder,
  note,
  credential,
  onChanged,
}: {
  title: string;
  provider: string;
  placeholder: string;
  note: string;
  credential: CredentialSummary | null;
  onChanged: () => Promise<void>;
}) {
  const [secret, setSecret] = useState("");
  const [saving, setSaving] = useState(false);
  const [removing, setRemoving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function save() {
    setSaving(true);
    setError(null);
    try {
      await apiFetch("/credentials", {
        method: "POST",
        body: JSON.stringify({ service: "search", provider, secret }),
      });
      setSecret("");
      await onChanged();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to save that key.");
    } finally {
      setSaving(false);
    }
  }

  async function remove() {
    setRemoving(true);
    setError(null);
    try {
      await apiFetch(`/credentials/search/${provider}`, { method: "DELETE" });
      await onChanged();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to remove that key.");
    } finally {
      setRemoving(false);
    }
  }

  return (
    <div className="bj-card">
      <div className="bj-card-header">
        <h2>{title}</h2>
        {credential ? (
          <span className="bj-badge-emerald">Configured</span>
        ) : (
          <span className="bj-badge-gold">Not configured</span>
        )}
      </div>
      <p className="bj-muted">{note}</p>
      {credential && (
        <div className="bj-muted bj-small">
          Updated {new Date(credential.updated_at).toLocaleString()}
        </div>
      )}
      <label className="bj-field">
        <span>{credential ? "Replace key" : "API key"}</span>
        <input
          type="password"
          value={secret}
          placeholder={placeholder}
          onChange={(e) => setSecret(e.target.value)}
        />
      </label>
      {error && <div className="bj-error">{error}</div>}
      <div className="bj-actions">
        <button
          className="bj-primary"
          onClick={() => void save()}
          disabled={saving || secret.trim() === ""}
        >
          {saving ? "Validating..." : credential ? "Update key" : "Save key"}
        </button>
        {credential && (
          <button onClick={() => void remove()} disabled={removing}>
            {removing ? "Removing..." : "Remove key"}
          </button>
        )}
      </div>
    </div>
  );
}

function OpenRouterCard({
  credential,
  onChanged,
}: {
  credential: CredentialSummary | null;
  onChanged: () => Promise<void>;
}) {
  const [secret, setSecret] = useState("");
  const [model, setModel] = useState(credential?.model ?? "");
  const [saving, setSaving] = useState(false);
  const [removing, setRemoving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function save() {
    setSaving(true);
    setError(null);
    try {
      await apiFetch("/credentials", {
        method: "POST",
        body: JSON.stringify({ service: "llm", provider: "openrouter", secret, model }),
      });
      setSecret("");
      await onChanged();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to save that key.");
    } finally {
      setSaving(false);
    }
  }

  async function remove() {
    setRemoving(true);
    setError(null);
    try {
      await apiFetch("/credentials/llm/openrouter", { method: "DELETE" });
      setModel("");
      await onChanged();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to remove that key.");
    } finally {
      setRemoving(false);
    }
  }

  const canSave = secret.trim() !== "" && model.trim() !== "";

  return (
    <div className="bj-card">
      <div className="bj-card-header">
        <h2>OpenRouter</h2>
        {credential ? (
          <span className="bj-badge-emerald">Configured</span>
        ) : (
          <span className="bj-badge-gold">Not configured</span>
        )}
      </div>
      <p className="bj-muted">
        Your key is encrypted at rest and validated against OpenRouter before it's saved -- an
        invalid key is never stored.
      </p>
      {credential?.model && (
        <div className="bj-muted bj-small">
          Current model: {credential.model} -- updated{" "}
          {new Date(credential.updated_at).toLocaleString()}
        </div>
      )}
      <label className="bj-field">
        <span>{credential ? "Replace key" : "API key"}</span>
        <input
          type="password"
          value={secret}
          placeholder="sk-or-v1-..."
          onChange={(e) => setSecret(e.target.value)}
        />
      </label>
      <label className="bj-field">
        <span>Model</span>
        <input
          type="text"
          value={model}
          placeholder="e.g. anthropic/claude-sonnet-4-6"
          onChange={(e) => setModel(e.target.value)}
        />
      </label>
      {error && <div className="bj-error">{error}</div>}
      <div className="bj-actions">
        <button className="bj-primary" onClick={() => void save()} disabled={saving || !canSave}>
          {saving ? "Validating..." : credential ? "Update key" : "Save key"}
        </button>
        {credential && (
          <button onClick={() => void remove()} disabled={removing}>
            {removing ? "Removing..." : "Remove key"}
          </button>
        )}
      </div>
    </div>
  );
}

function TelegramLinkCard() {
  const [code, setCode] = useState<string | null>(null);
  const [expiresAt, setExpiresAt] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function generate() {
    setBusy(true);
    setError(null);
    try {
      const result = await apiFetch<{ code: string; expires_at: string }>("/link/code", {
        method: "POST",
        body: JSON.stringify({ channel: "telegram" }),
      });
      setCode(result.code);
      setExpiresAt(result.expires_at);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to generate a code.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="bj-card">
      <h2>Telegram</h2>
      <p className="bj-muted">
        Link your Telegram account to use @between_jobs_tech_bot with the same profile and
        applications you see here. If you've already used the bot, linking brings over anything
        you built up there.
      </p>
      {code && (
        <div>
          <div className="bj-muted bj-small">Send this to the bot:</div>
          <div className="bj-link-code">/link {code}</div>
          {expiresAt && (
            <div className="bj-muted bj-small">
              Expires {new Date(expiresAt).toLocaleTimeString()}
            </div>
          )}
        </div>
      )}
      {error && <div className="bj-error">{error}</div>}
      <div className="bj-actions">
        <button className="bj-primary" onClick={() => void generate()} disabled={busy}>
          {busy ? "Generating..." : code ? "Generate a new code" : "Generate a code"}
        </button>
      </div>
    </div>
  );
}

function GmailConnectCard({
  credential,
  onChanged,
}: {
  credential: CredentialSummary | null;
  onChanged: () => Promise<void>;
}) {
  // outreach-contactfinder.md Phase F. Draft-only: the OAuth scope this
  // requests (gmail.compose) can't send an email even if the code tried
  // to -- see gmail_client.py's own docstring.
  const [connecting, setConnecting] = useState(false);
  const [removing, setRemoving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function connect() {
    setConnecting(true);
    setError(null);
    try {
      const result = await apiFetch<{ authorize_url: string }>("/profile/integrations/gmail/connect");
      window.location.href = result.authorize_url;
    } catch (e) {
      if (e instanceof ApiError && e.code === "SETUP_REQUIRED") {
        setError("Gmail draft integration isn't configured on this server yet.");
        return;
      }
      setError(e instanceof Error ? e.message : "Failed to start connecting Gmail.");
    } finally {
      setConnecting(false);
    }
  }

  async function disconnect() {
    setRemoving(true);
    setError(null);
    try {
      await apiFetch("/credentials/oauth/gmail", { method: "DELETE" });
      await onChanged();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to disconnect Gmail.");
    } finally {
      setRemoving(false);
    }
  }

  return (
    <div className="bj-card">
      <div className="bj-card-header">
        <h2>Gmail</h2>
        {credential ? (
          <span className="bj-badge-emerald">Connected</span>
        ) : (
          <span className="bj-badge-gold">Not connected</span>
        )}
      </div>
      <p className="bj-muted">
        Lets ContactFinder land an outreach draft directly in your real Gmail -- draft-only,
        always. Nothing is ever sent automatically; you still open Gmail and press Send yourself.
      </p>
      {error && <div className="bj-error">{error}</div>}
      <div className="bj-actions">
        {credential ? (
          <button onClick={() => void disconnect()} disabled={removing}>
            {removing ? "Disconnecting..." : "Disconnect Gmail"}
          </button>
        ) : (
          <button className="bj-primary" onClick={() => void connect()} disabled={connecting}>
            {connecting ? "Connecting..." : "Connect Gmail"}
          </button>
        )}
      </div>
    </div>
  );
}
