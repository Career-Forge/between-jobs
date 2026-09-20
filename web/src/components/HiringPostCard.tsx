import {
  commentCountLabel,
  postedAtFromActivityId,
  postedLabel,
  registryMatchNote,
  safePostUrl,
  savedLabel,
  speciesInfo,
  validateEmbedUrl,
} from "../lib/hiringSignals";
import { saveButtonId } from "../lib/hiringSignalsPanelModel";
import type { HiringSignal, SavedPost } from "../lib/hiringSignalsTypes";

// Presentational cards for the "Hiring posts" panel (Hiring Signals P3).
//
// Everything here is CONTROLLED: whether a card's embed is open, whether a
// save is in flight, which error to show -- all of it arrives as props from
// HiringSignalsPanel, which owns the state and the requests. That split is
// what makes the privacy-critical part testable without a browser: this file
// imports no API client (the Supabase client throws at import time without env
// vars), so HiringPostCard.test.tsx can render these to static markup and
// assert that no iframe exists until a card is opened, and none ever exists
// for an address that is not exactly the official embed shape.
//
// CLICK-TO-LOAD, on purpose. An embed contacts LinkedIn from the user's
// browser, so nothing loads until the user asks for that one post -- a card
// starts as metadata plus a "Show post" button, and only then is the iframe
// mounted. That is a privacy choice and also keeps a page of results from
// spinning up a dozen frames at once.
//
// NO CLAIMS WE CANNOT BACK. The browser cannot tell whether a cross-origin
// embed is showing a removed post, so the only thing said under an open frame
// is that a blank one MAY mean the post was removed. Cards show only computed
// fields (species tag, posted time, author, comment count, role words) --
// never an "actively hiring" or connection-degree badge, and never LinkedIn's
// name or marks. "Role words found" is gold, the evidence colour, and not
// emerald ("verified fact"): it is a word match over a short snippet, and next
// to an "Unclassified" tag a green badge would read as confirmation.
//
// BUSY BUTTONS ARE aria-disabled, and the handlers check: a disabled button
// loses keyboard focus (see HiringSignalsPanelView.tsx). Save is also the
// target focus returns to after an Unsave, so it carries an id.

// The iframe's sandbox: scripts and same-origin are what LinkedIn's embed
// needs to render itself (it is cross-origin to this app, so allow-same-origin
// gives it ITS origin, not ours); popups let the links inside it open.
const EMBED_SANDBOX = "allow-scripts allow-same-origin allow-popups";

export function PostEmbed({
  regionId,
  embedUrl,
  activityId,
  title,
}: {
  regionId: string;
  embedUrl: string;
  activityId: string;
  title: string;
}) {
  // The single gate every iframe in the feature passes through. A server value
  // that is not exactly the official embed address for THIS post's id gets no
  // frame at all -- a note instead, never a fallback address.
  const src = validateEmbedUrl(embedUrl, activityId);
  return (
    <div id={regionId} className="bj-hs-embed">
      {src !== null ? (
        <iframe
          className="bj-hs-embed-frame"
          src={src}
          title={title}
          loading="lazy"
          referrerPolicy="no-referrer"
          sandbox={EMBED_SANDBOX}
        />
      ) : (
        <div className="bj-muted bj-small">
          This post's embed address did not look right, so it was not loaded.
        </div>
      )}
      <p className="bj-muted bj-small">
        The post can take a while to appear. If the frame stays blank, it may have been removed.
      </p>
    </div>
  );
}

// `context` (" by Jane Example", ", saved 2 days ago") is appended to the link's
// accessible name so a screen reader's list of links is not a column of
// identical "Open post" entries; the visible text stays "Open post" and leads
// the name, as it must.
function OpenPostLink({ postUrl, context }: { postUrl: string; context: string }) {
  // Only a plain https url on a linkedin.com host becomes a link; otherwise
  // the link is simply omitted.
  const href = safePostUrl(postUrl);
  if (href === null) return null;
  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      aria-label={`Open post${context} (opens in a new tab)`}
    >
      Open post
    </a>
  );
}

export interface SignalCardProps {
  signal: HiringSignal;
  now: Date;
  saved: boolean;
  saving: boolean;
  embedOpen: boolean;
  error: string | null;
  onToggleEmbed: () => void;
  onSave: () => void;
  // null when the saved row's id is not known (the server said "saved" but the
  // saved list has not caught up), in which case there is nothing to delete yet.
  onUnsave: (() => void) | null;
  unsaving: boolean;
}

export function SignalCard({
  signal,
  now,
  saved,
  saving,
  embedOpen,
  error,
  onToggleEmbed,
  onSave,
  onUnsave,
  unsaving,
}: SignalCardProps) {
  const species = speciesInfo(signal.species);
  const comments = commentCountLabel(signal.comment_count);
  const registryNote = registryMatchNote(signal.registry_match);
  const author = signal.author_name;
  const regionId = `hs-embed-search-${signal.activity_id}`;
  const forWhom = author !== null ? ` by ${author}` : "";

  return (
    <li className="bj-hs-card">
      <div className="bj-hs-card-meta">
        <span className={species.badgeClass} title={species.description}>
          {species.label}
        </span>
        {signal.role_match === true && <span className="bj-badge-gold">Role words found</span>}
        <span className="bj-muted bj-small">
          {postedLabel(signal.posted_at, signal.age_hint, now)}
        </span>
      </div>

      {(author !== null || comments !== null) && (
        <div className="bj-small bj-hs-card-text">
          {author !== null && <span>{author}</span>}
          {author !== null && comments !== null && " · "}
          {comments !== null && <span className="bj-muted">{comments}</span>}
        </div>
      )}
      {registryNote !== null && <div className="bj-muted bj-small">{registryNote}</div>}

      <div className="bj-hs-actions">
        <button
          type="button"
          onClick={onToggleEmbed}
          aria-expanded={embedOpen}
          aria-controls={embedOpen ? regionId : undefined}
          aria-label={`${embedOpen ? "Hide post" : "Show post"}${forWhom}`}
        >
          {embedOpen ? "Hide post" : "Show post"}
        </button>
        <button
          type="button"
          id={saveButtonId(signal.activity_id)}
          className={saved ? "bj-hs-saved" : undefined}
          onClick={() => {
            if (!saving && !saved) onSave();
          }}
          aria-disabled={saving || saved ? true : undefined}
          aria-label={`${saved ? "Saved" : saving ? "Saving" : "Save"} post${forWhom}`}
        >
          {saved ? "Saved" : saving ? "Saving..." : "Save"}
        </button>
        {saved && onUnsave !== null && (
          <button
            type="button"
            className="bj-link-button"
            onClick={() => {
              if (!unsaving) onUnsave();
            }}
            aria-disabled={unsaving ? true : undefined}
            aria-label={`Unsave post${forWhom}`}
          >
            {unsaving ? "Unsaving..." : "Unsave"}
          </button>
        )}
        <OpenPostLink postUrl={signal.post_url} context={forWhom} />
      </div>

      {embedOpen && (
        <PostEmbed
          regionId={regionId}
          embedUrl={signal.embed_url}
          activityId={signal.activity_id}
          title={`Hiring post${forWhom} (embedded)`}
        />
      )}
      {error !== null && (
        <div className="bj-error" role="alert">
          {error}
        </div>
      )}
    </li>
  );
}

export interface SavedPostCardProps {
  save: SavedPost;
  now: Date;
  embedOpen: boolean;
  removing: boolean;
  error: string | null;
  onToggleEmbed: () => void;
  onRemove: () => void;
}

export function SavedPostCard({
  save,
  now,
  embedOpen,
  removing,
  error,
  onToggleEmbed,
  onRemove,
}: SavedPostCardProps) {
  // A saved post is a pointer (ids and urls only), so the list endpoint sends
  // no posted_at -- but the time is readable off the id itself, which is why
  // this card is not stuck saying "time unknown".
  const postedAt = postedAtFromActivityId(save.activity_id);
  const regionId = `hs-embed-saved-${save.id}`;
  const savedText = savedLabel(save.created_at, now);
  // Saved cards carry no author, so their button names lead with the visible
  // text ("Show post") and tell them apart by when each was saved.
  const which = `, ${savedText.toLowerCase()}`;

  return (
    <li className="bj-hs-card">
      <div className="bj-hs-card-meta">
        <span className="bj-small">{savedText}</span>
        <span className="bj-muted bj-small">
          {postedLabel(postedAt === null ? null : postedAt.toISOString(), null, now)}
        </span>
      </div>

      <div className="bj-hs-actions">
        <button
          type="button"
          onClick={onToggleEmbed}
          aria-expanded={embedOpen}
          aria-controls={embedOpen ? regionId : undefined}
          aria-label={`${embedOpen ? "Hide post" : "Show post"}${which}`}
        >
          {embedOpen ? "Hide post" : "Show post"}
        </button>
        <button
          type="button"
          onClick={() => {
            if (!removing) onRemove();
          }}
          aria-disabled={removing ? true : undefined}
          aria-label={`Remove post${which}`}
        >
          {removing ? "Removing..." : "Remove"}
        </button>
        <OpenPostLink postUrl={save.post_url} context={which} />
      </div>

      {embedOpen && (
        <PostEmbed
          regionId={regionId}
          embedUrl={save.embed_url}
          activityId={save.activity_id}
          title="Saved hiring post (embedded)"
        />
      )}
      {error !== null && (
        <div className="bj-error" role="alert">
          {error}
        </div>
      )}
    </li>
  );
}
