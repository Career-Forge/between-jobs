import { useCallback, useEffect, useState } from "react";
import { SavedSearchesCard } from "../components/SavedSearchesCard";
import { ProblemView } from "../components/SetupRequiredNotice";
import { apiFetch } from "../lib/api";
import { discordInstallUrl, isDiscordAvailable, isTelegramAvailable, telegramBotUsername } from "../lib/capabilities";
import {
  SEARCH_PROVIDER_CARDS,
  cardNote,
  providerNotice,
  type SearchProviderCardSpec,
} from "../lib/providerStatus";
import {
  type GmailAccess,
  gmailAccess,
  gmailAvailabilityFailed,
  gmailConnection,
  gmailState,
  gmailTestingMode,
  gmailUnavailableMessage,
  learnFromGmailRefusal,
} from "../lib/gmailAccess";
import { friendlyApiMessage } from "../lib/rateLimitMessage";
import { type Problem, problemOf } from "../lib/setupRequired";
import { refreshCapabilities, useCapabilities } from "../lib/useCapabilities";
import { useHiringSignalsEnabled } from "../lib/useHiringSignalsStatus";

// Integrations (Sprint 2.7f, widened Horizon Sprint 5.0) -- the web
// surface over Sprint 2.7's BYOK credential broker (Proposal §11).
// Reachable at /profile/integrations, matching credential_resolver.py's
// own settings_path convention exactly: a SETUP_REQUIRED error's link lands
// here (lib/setupRequired.ts). Not a top-level nav item -- the design
// book's information architecture is a fixed five: Today/Discover/
// Applications/Practice/Profile: this is a sub-page of Profile, not a
// sixth destination.
//
// OpenRouter (LLM) plus the search providers (You.com and Firecrawl for the
// company intelligence pipeline; Brave and Serper too for Discover's job search
// and an application's "Hiring posts"). The search cards share
// SearchProviderCard -- unlike OpenRouter, none has a "model" concept, just a
// key -- rather than forcing them through OpenRouterCard's shape. Hiring posts'
// setup message names Brave, Serper, Firecrawl and You.com, so all four have a
// card here: a step the page tells you to take has to be one you can take.
// The Brave and Serper notes mention hiring posts ONLY for a person Hiring signals is enabled for
// (useHiringSignalsEnabled, via cardNote): for anyone else the server treats the feature as not
// existing, so the page must not say their key is used for it.
//
// The page is also where the tester-programme gate (lib/enrollment.ts) lets a person who has
// not joined, or has withdrawn, switch things off: remove a key, disconnect Gmail, and pause or
// delete a saved search (the last card), which keep running in the background on their key.
//
// Sprint 2.8f adds the Telegram-link card on the same page -- both cards
// are "connect an external account to this one," the same loose theme
// the page's own title already claims, so this doesn't need a second
// route to earn its place. It shows only on a server that has a bot (P2.4):
// GET /capabilities says so, and until that answer is known the card stays out.
// The Discord card is the same idea, shown only when /capabilities says the server has the
// Discord app configured (a link code could not be redeemed otherwise).

interface CredentialSummary {
  id: string;
  service: string;
  provider: string;
  model: string | null;
  is_validated: boolean;
  updated_at: string;
  // OAuth-specific (Gmail reply/status parsing) -- the real scope
  // string Google granted, captured at connect time. null for every
  // credential saved before this field existed, or for a non-OAuth
  // BYOK credential that never had a scope concept at all.
  scope: string | null;
}


export default function Integrations() {
  const capabilities = useCapabilities();
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

  // False while the answer is awaited, when it is "not enabled", and when asking failed.
  const hiringSignalsEnabled = useHiringSignalsEnabled();
  const openrouter = credentials?.find((c) => c.service === "llm" && c.provider === "openrouter") ?? null;
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
      {credentials !== null &&
        SEARCH_PROVIDER_CARDS.map((card) => (
          <SearchProviderCard
            key={card.provider}
            card={card}
            hiringSignalsEnabled={hiringSignalsEnabled}
            credential={
              credentials.find((c) => c.service === "search" && c.provider === card.provider) ?? null
            }
            onChanged={load}
          />
        ))}
      {isTelegramAvailable(capabilities) && (
        <TelegramLinkCard botUsername={telegramBotUsername(capabilities)} />
      )}
      {isDiscordAvailable(capabilities) && <DiscordLinkCard installUrl={discordInstallUrl(capabilities)} />}
      {credentials !== null && (
        <GmailConnectCard
          credential={gmail}
          access={gmailAccess(capabilities)}
          availabilityFailed={gmailAvailabilityFailed(capabilities)}
          testingMode={gmailTestingMode(capabilities)}
          onChanged={load}
        />
      )}
      <SavedSearchesCard />
    </div>
  );
}

// Exported for providerStatus.test.tsx -- the rest of this file stays page-local plumbing.
export function SearchProviderCard({
  card,
  hiringSignalsEnabled,
  credential,
  onChanged,
}: {
  card: SearchProviderCardSpec;
  // Hiring signals is on for this person (see the note at the top of this file).
  hiringSignalsEnabled: boolean;
  credential: CredentialSummary | null;
  onChanged: () => Promise<void>;
}) {
  const { title, provider, placeholder } = card;
  const note = cardNote(card, hiringSignalsEnabled);
  // Whether this provider has had a successful run against the real service (lib/providerStatus.ts).
  const notice = providerNotice(provider);
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
      setError(friendlyApiMessage(e, "Failed to save that key."));
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
        <span className="bj-badge-group">
          {notice !== null && <span className="bj-badge-muted">{notice.label}</span>}
          {credential ? (
            <span className="bj-badge-emerald">Configured</span>
          ) : (
            <span className="bj-badge-gold">Not configured</span>
          )}
        </span>
      </div>
      <p className="bj-muted">{note}</p>
      {notice !== null && (
        <p className="bj-muted bj-small">
          {notice.detail}
          {notice.help !== null && ` ${notice.help}`}
        </p>
      )}
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
      setError(friendlyApiMessage(e, "Failed to save that key."));
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

function TelegramLinkCard({ botUsername }: { botUsername: string | null }) {
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
        Link your Telegram account to use {botUsername ? `@${botUsername}` : "this server's Telegram bot"}{" "}
        with the same profile and applications you see here. If you've already used the bot,
        linking brings over anything you built up there.
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

// The Discord card. Discord only hands a command or a button tap to the app, never text typed in
// the chat, so the code goes in as the /link command (it has a field for the code), not as a
// message. A code can only be redeemed here if the server has the Discord app configured, which is
// why the card is shown only when /capabilities says so.
function DiscordLinkCard({ installUrl }: { installUrl: string | null }) {
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
        body: JSON.stringify({ channel: "discord" }),
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
      <h2>Discord</h2>
      <p className="bj-muted">
        Link your Discord account to use this server's Discord app with the same profile and
        applications you see here. If you've already used the app, linking brings over anything you
        built up there. The app answers slash commands in a direct message with it; text you type in
        that chat is not delivered to it.
      </p>
      {installUrl && (
        <p className="bj-muted bj-small">
          Not added yet?{" "}
          <a href={installUrl} target="_blank" rel="noopener noreferrer">
            Add the app to Discord
          </a>
          .
        </p>
      )}
      {code && (
        <div>
          <div className="bj-muted bj-small">In a direct message with the app, run:</div>
          <div className="bj-link-code">/link code: {code}</div>
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

// The Gmail card, in one of five honest states (lib/gmailAccess.ts decides which):
//   - Checking / Couldn't check: it is not known whether Gmail is open to this person (the
//     server's settings are still being asked, or asking failed; this page is reachable before
//     they are known). Nothing is offered and no connection is claimed, whatever the credential
//     list holds: no Connect, no "Connected". "Disconnect Gmail" stays when a connection is stored.
//   - Not available: Gmail is not open to this person yet (the server limits it to named people)
//     or the server has none. No Connect button. A connection they made before stays where it is,
//     unused, and "Disconnect Gmail" is always there.
//   - Not connected: Connect Gmail.
//   - Connected: Disconnect Gmail, and -- only while the operator says Google's OAuth client is in
//     Testing mode -- a note that Google ends the connection after 7 days.
//   - Needs reconnect: Google stopped accepting the connection, or it was made before the app
//     asked for read access. Reconnect Gmail (and Disconnect). The server keeps what was stored and
//     stops using it until then.
// Draft-only throughout: gmail.compose can't send an email even if the code tried to -- see
// gmail_client.py's own docstring.
export function GmailConnectCard({
  credential,
  access,
  availabilityFailed = false,
  testingMode,
  onChanged,
}: {
  credential: CredentialSummary | null;
  access: GmailAccess;
  // With access "unknown": asking the server failed, rather than still being under way.
  availabilityFailed?: boolean;
  testingMode: boolean;
  onChanged: () => Promise<void>;
}) {
  const connection = gmailConnection(credential === null ? [] : [credential]);
  const state = gmailState(access, connection);
  const [connecting, setConnecting] = useState(false);
  const [removing, setRemoving] = useState(false);
  const [error, setError] = useState<Problem | null>(null);

  async function connect() {
    setConnecting(true);
    setError(null);
    try {
      const result = await apiFetch<{ authorize_url: string }>("/profile/integrations/gmail/connect");
      window.location.href = result.authorize_url;
    } catch (e) {
      // The server can contradict what this page believed (Gmail was just closed to this person):
      // ask again, and the card then says so. A setup failure is the server's own sentence, an
      // operator's setting, so it carries no link to fix it here.
      learnFromGmailRefusal(e, { unavailable: refreshCapabilities });
      setError(problemOf(e, "Failed to start connecting Gmail."));
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

  const connectButton = (label: string) => (
    <button className="bj-primary" onClick={() => void connect()} disabled={connecting}>
      {connecting ? "Connecting..." : label}
    </button>
  );
  const disconnectButton = (
    <button onClick={() => void disconnect()} disabled={removing}>
      {removing ? "Disconnecting..." : "Disconnect Gmail"}
    </button>
  );

  return (
    <div className="bj-card">
      <div className="bj-card-header">
        <h2>Gmail</h2>
        {state === "unavailable" ? (
          <span className="bj-badge-muted">Not available</span>
        ) : state === "availability_unknown" ? (
          <span className="bj-badge-muted">{availabilityFailed ? "Couldn't check" : "Checking"}</span>
        ) : state === "reconnect_needed" ? (
          <span className="bj-badge-gold">Needs reconnect</span>
        ) : state === "connected" ? (
          <span className="bj-badge-emerald">Connected</span>
        ) : (
          <span className="bj-badge-gold">Not connected</span>
        )}
      </div>
      {state === "unavailable" ? (
        <>
          <p className="bj-muted">
            {gmailUnavailableMessage(access)} Everything else works, and you can still copy an
            outreach draft or open it in your mail app.
          </p>
          {credential !== null && (
            <p className="bj-muted bj-small">
              You have a Gmail connection from before. It is not being used, and nothing has been
              deleted. Disconnect it if you would rather we did not keep it.
            </p>
          )}
        </>
      ) : state === "availability_unknown" ? (
        <>
          <p className="bj-muted" role="status">
            {availabilityFailed
              ? "We couldn't check whether Gmail is available for your account. Reload the page to try again."
              : "Checking whether Gmail is available for your account..."}
          </p>
          {credential !== null && (
            <p className="bj-muted bj-small">
              You have a Gmail connection saved. You can disconnect it here at any time.
            </p>
          )}
        </>
      ) : (
        <p className="bj-muted">
          Lets ContactFinder land an outreach draft directly in your real Gmail -- draft-only,
          always. Nothing is ever sent automatically; you still open Gmail and press Send yourself.
          Also lets the platform check a thread you've drafted into for a reply, so a response can
          surface as a real update -- it never reads anything else in your inbox.
        </p>
      )}
      {state === "connected" && testingMode && (
        <p className="bj-muted bj-small">
          Gmail is still in beta, so Google ends the connection after 7 days: expect to reconnect
          about once a week. When it does, this card says Needs reconnect and the app stops using
          Gmail until you do. Everything already saved stays.
        </p>
      )}
      {state === "reconnect_needed" && connection.kind === "reconnect_needed" && (
        <p className="bj-muted bj-small">
          {connection.cause === "access_ended"
            ? "Google stopped accepting this connection. It may have expired, or you may have revoked access or changed your Google password. Until you reconnect, drafts aren't created in Gmail and replies aren't checked. Everything already saved stays, and drafts already in your Gmail stay there."
            : "Connected before reply-checking existed -- reconnect to grant the extra read access."}
        </p>
      )}
      <ProblemView problem={error} />
      <div className="bj-actions">
        {state === "not_connected"
          ? connectButton("Connect Gmail")
          : state === "reconnect_needed"
            ? connectButton("Reconnect Gmail")
            : null}
        {credential !== null && disconnectButton}
      </div>
    </div>
  );
}
