import { useState } from "react";
import { ProblemView } from "../SetupRequiredNotice";
import { apiFetch } from "../../lib/api";
import { discordInstallUrl, isDiscordAvailable, isTelegramAvailable, telegramBotUsername } from "../../lib/capabilities";
import {
  type GmailAccess,
  gmailAccess,
  gmailAvailabilityFailed,
  gmailConnection,
  gmailState,
  gmailTestingMode,
  gmailUnavailableMessage,
  learnFromGmailRefusal,
} from "../../lib/gmailAccess";
import { type Problem, problemOf } from "../../lib/setupRequired";
import { refreshCapabilities } from "../../lib/useCapabilities";
import type { CapabilitiesState } from "../../lib/capabilities";
import type { CredentialSummary } from "../../lib/settings/credentials";
import "../../styles/settings.css";

// "Connections": the accounts that are linked to this one rather than keys that are pasted in --
// Telegram, Discord and Gmail. They are the same cards the page always had, with the same logic and
// the same words; what changed is where they sit and how they look. None of them holds the page's
// cyan fill: that belongs to the next-step band, so "Generate a code" and "Connect Gmail" are
// ordinary outlined buttons.
//
// The Telegram-link card shows only on a server that has a bot (P2.4): GET /capabilities says so, and
// until that answer is known the card stays out. The Discord card is the same idea, shown only when
// /capabilities says the server has the Discord app configured (a link code could not be redeemed
// otherwise).

export function TelegramLinkCard({ botUsername }: { botUsername: string | null }) {
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
    <div className="bj-set-card">
      <h3>Telegram</h3>
      <p className="bj-set-note">
        Link your Telegram account to use {botUsername ? `@${botUsername}` : "this server's Telegram bot"}{" "}
        with the same profile and applications you see here. If you've already used the bot,
        linking brings over anything you built up there.
      </p>
      {code && (
        <div>
          <div className="bj-set-hint">Send this to the bot:</div>
          <div className="bj-link-code">/link {code}</div>
          {expiresAt && (
            <div className="bj-set-hint">
              Expires {new Date(expiresAt).toLocaleTimeString()}
            </div>
          )}
        </div>
      )}
      {error && <div className="bj-set-error">{error}</div>}
      <div className="bj-set-actions">
        <button onClick={() => void generate()} disabled={busy}>
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
export function DiscordLinkCard({ installUrl }: { installUrl: string | null }) {
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
    <div className="bj-set-card">
      <h3>Discord</h3>
      <p className="bj-set-note">
        Link your Discord account to use this server's Discord app with the same profile and
        applications you see here. If you've already used the app, linking brings over anything you
        built up there. The app answers slash commands in a direct message with it; text you type in
        that chat is not delivered to it.
      </p>
      {installUrl && (
        <p className="bj-set-hint">
          Not added yet?{" "}
          <a href={installUrl} target="_blank" rel="noopener noreferrer">
            Add the app to Discord
          </a>
          .
        </p>
      )}
      {code && (
        <div>
          <div className="bj-set-hint">In a direct message with the app, run:</div>
          <div className="bj-link-code">/link code: {code}</div>
          {expiresAt && (
            <div className="bj-set-hint">
              Expires {new Date(expiresAt).toLocaleTimeString()}
            </div>
          )}
        </div>
      )}
      {error && <div className="bj-set-error">{error}</div>}
      <div className="bj-set-actions">
        <button onClick={() => void generate()} disabled={busy}>
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
    <button onClick={() => void connect()} disabled={connecting}>
      {connecting ? "Connecting..." : label}
    </button>
  );
  const disconnectButton = (
    <button onClick={() => void disconnect()} disabled={removing}>
      {removing ? "Disconnecting..." : "Disconnect Gmail"}
    </button>
  );

  return (
    <div className="bj-set-card">
      <div className="bj-set-card-head">
        <h3>Gmail</h3>
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
          <p className="bj-set-note">
            {gmailUnavailableMessage(access)} Everything else works, and you can still copy an
            outreach draft or open it in your mail app.
          </p>
          {credential !== null && (
            <p className="bj-set-hint">
              You have a Gmail connection from before. It is not being used, and nothing has been
              deleted. Disconnect it if you would rather we did not keep it.
            </p>
          )}
        </>
      ) : state === "availability_unknown" ? (
        <>
          <p className="bj-set-note" role="status">
            {availabilityFailed
              ? "We couldn't check whether Gmail is available for your account. Reload the page to try again."
              : "Checking whether Gmail is available for your account..."}
          </p>
          {credential !== null && (
            <p className="bj-set-hint">
              You have a Gmail connection saved. You can disconnect it here at any time.
            </p>
          )}
        </>
      ) : (
        <p className="bj-set-note">
          Lets ContactFinder land an outreach draft directly in your real Gmail -- draft-only,
          always. Nothing is ever sent automatically; you still open Gmail and press Send yourself.
          Also lets the platform check a thread you've drafted into for a reply, so a response can
          surface as a real update -- it never reads anything else in your inbox.
        </p>
      )}
      {state === "connected" && testingMode && (
        <p className="bj-set-hint">
          Gmail is still in beta, so Google ends the connection after 7 days: expect to reconnect
          about once a week. When it does, this card says Needs reconnect and the app stops using
          Gmail until you do. Everything already saved stays.
        </p>
      )}
      {state === "reconnect_needed" && connection.kind === "reconnect_needed" && (
        <p className="bj-set-hint">
          {connection.cause === "access_ended"
            ? "Google stopped accepting this connection. It may have expired, or you may have revoked access or changed your Google password. Until you reconnect, drafts aren't created in Gmail and replies aren't checked. Everything already saved stays, and drafts already in your Gmail stay there."
            : "Connected before reply-checking existed -- reconnect to grant the extra read access."}
        </p>
      )}
      <ProblemView problem={error} />
      <div className="bj-set-actions">
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

export interface ConnectionsSectionProps {
  capabilities: CapabilitiesState;
  /** The Gmail credential, or null. Pass null until the credential list has been read. */
  gmail: CredentialSummary | null;
  /** The credential list has been read: until it has, Gmail is not drawn (it would read "Not connected" for a connection that exists). */
  credentialsReady: boolean;
  onChanged: () => Promise<void>;
}

export function ConnectionsSection({ capabilities, gmail, credentialsReady, onChanged }: ConnectionsSectionProps) {
  return (
    <section id="connections" className="bj-set-section" aria-labelledby="connections-h">
      <h2 id="connections-h" tabIndex={-1}>
        Connections
      </h2>
      <p className="bj-set-lede">Accounts you link to this one. Each is optional, and none of them can send anything for you.</p>
      {isTelegramAvailable(capabilities) && <TelegramLinkCard botUsername={telegramBotUsername(capabilities)} />}
      {isDiscordAvailable(capabilities) && <DiscordLinkCard installUrl={discordInstallUrl(capabilities)} />}
      {credentialsReady && (
        <GmailConnectCard
          credential={gmail}
          access={gmailAccess(capabilities)}
          availabilityFailed={gmailAvailabilityFailed(capabilities)}
          testingMode={gmailTestingMode(capabilities)}
          onChanged={onChanged}
        />
      )}
    </section>
  );
}
