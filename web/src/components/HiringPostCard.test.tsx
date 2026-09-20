import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { EMBED_URL_PREFIX } from "../lib/hiringSignals";
import { saveButtonId } from "../lib/hiringSignalsPanelModel";
import type { HiringSignal, SavedPost } from "../lib/hiringSignalsTypes";
import { expand, findAll, onlyButton, press, prop } from "../testing/reactTree";
import { PostEmbed, SavedPostCard, SignalCard, type SignalCardProps } from "./HiringPostCard";

// These render the presentational cards to static markup -- no browser, no
// network, no API client. What they pin is the privacy-critical contract that
// cannot be checked by running the app authenticated: NO iframe exists until a
// card is opened, and none EVER exists for an address that is not exactly the
// official embed shape for that post. (Ids, names and urls are synthetic.)

const ID = "7000000000000000001";
const GOOD_EMBED = `${EMBED_URL_PREFIX}${ID}`;
const NOW = new Date("2026-09-19T12:00:00Z");

function makeSignal(overrides: Partial<HiringSignal> = {}): HiringSignal {
  return {
    activity_id: ID,
    post_url: `https://www.linkedin.com/posts/example-${ID}`,
    embed_url: GOOD_EMBED,
    author_name: "Jane Example",
    posted_at: "2026-09-16T12:00:00Z",
    age_hint: null,
    species: "unclassified",
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
    embed_url: GOOD_EMBED,
    created_at: "2026-09-18T12:00:00Z",
    ...overrides,
  };
}

function renderSignal(
  signal: HiringSignal,
  overrides: Partial<SignalCardProps> = {},
): string {
  return renderToStaticMarkup(
    <ul>
      <SignalCard
        signal={signal}
        now={NOW}
        saved={false}
        saving={false}
        embedOpen={false}
        error={null}
        onToggleEmbed={() => {}}
        onSave={() => {}}
        onUnsave={null}
        unsaving={false}
        {...overrides}
      />
    </ul>,
  );
}

function renderSaved(
  save: SavedPost,
  overrides: { embedOpen?: boolean; removing?: boolean; error?: string | null } = {},
): string {
  return renderToStaticMarkup(
    <ul>
      <SavedPostCard
        save={save}
        now={NOW}
        embedOpen={overrides.embedOpen ?? false}
        removing={overrides.removing ?? false}
        error={overrides.error ?? null}
        onToggleEmbed={() => {}}
        onRemove={() => {}}
      />
    </ul>,
  );
}

// The words a reader would see, not the attributes: tags stripped.
function visibleText(html: string): string {
  return html.replace(/<[^>]*>/g, " ").replace(/\s+/g, " ");
}

function iframeCount(html: string): number {
  return (html.match(/<iframe/g) ?? []).length;
}

describe("click-to-load embeds", () => {
  it("mounts no iframe until the card is opened, and offers 'Show post'", () => {
    const html = renderSignal(makeSignal());
    expect(iframeCount(html)).toBe(0);
    expect(html).not.toContain(GOOD_EMBED);
    expect(html).toContain("Show post");
    expect(html).toContain('aria-expanded="false"');
  });

  it("mounts exactly one hardened iframe, from the validated address only, once opened", () => {
    const html = renderSignal(makeSignal(), { embedOpen: true });
    expect(iframeCount(html)).toBe(1);
    expect(html).toContain(`src="${GOOD_EMBED}"`);
    expect(html).toContain('loading="lazy"');
    expect(html).toMatch(/referrerpolicy="no-referrer"/i);
    expect(html).toContain('sandbox="allow-scripts allow-same-origin allow-popups"');
    expect(html).toContain('title="Hiring post by Jane Example (embedded)"');
    expect(html).toContain('aria-expanded="true"');
    expect(html).toContain("Hide post");
  });

  it("says only that a frame that STAYS blank MAY mean removal -- it cannot tell, so it does not claim to", () => {
    const html = renderSignal(makeSignal(), { embedOpen: true });
    // Measured live: LinkedIn's frame takes roughly 5-18 seconds to paint, so a
    // blank frame right after "Show post" is normal and must not read as removal.
    expect(visibleText(html)).toContain("The post can take a while to appear.");
    expect(visibleText(html)).toContain("If the frame stays blank, it may have been removed.");
    expect(visibleText(html)).not.toMatch(/no longer available|has been removed/i);
  });

  const badEmbeds: [string, string][] = [
    ["another host", "https://evil.example/embed/feed/update/urn:li:activity:" + ID],
    ["a userinfo trick", `https://www.linkedin.com@evil.example/embed/feed/update/urn:li:activity:${ID}`],
    ["plain http", GOOD_EMBED.replace("https", "http")],
    ["a query string", `${GOOD_EMBED}?x=1`],
    ["a javascript: url", "javascript:alert(1)"],
    ["a data: url", "data:text/html,<script>alert(1)</script>"],
    ["an id that is not this post's", `${EMBED_URL_PREFIX}7000000000000000099`],
    ["an empty value", ""],
  ];
  it.each(badEmbeds)("renders no iframe at all for %s, even when opened", (_name, embed_url) => {
    const html = renderSignal(makeSignal({ embed_url }), { embedOpen: true });
    expect(iframeCount(html)).toBe(0);
    expect(html).not.toContain("javascript:");
    expect(html).not.toContain("evil.example");
    expect(visibleText(html)).toContain("did not look right, so it was not loaded");
  });

  it("applies the same gate to a saved post", () => {
    expect(iframeCount(renderSaved(makeSave()))).toBe(0);
    const open = renderSaved(makeSave(), { embedOpen: true });
    expect(iframeCount(open)).toBe(1);
    expect(open).toContain(`src="${GOOD_EMBED}"`);
    const bad = renderSaved(makeSave({ embed_url: "https://evil.example/x" }), { embedOpen: true });
    expect(iframeCount(bad)).toBe(0);
  });

  it("PostEmbed on its own refuses an address that does not match the given id", () => {
    const html = renderToStaticMarkup(
      <PostEmbed regionId="r" embedUrl={GOOD_EMBED} activityId="7000000000000000002" title="t" />,
    );
    expect(iframeCount(html)).toBe(0);
  });
});

describe("Open post link", () => {
  it("links out safely for a linkedin.com post url", () => {
    const html = renderSignal(makeSignal());
    expect(html).toContain(`href="https://www.linkedin.com/posts/example-${ID}"`);
    expect(html).toContain('target="_blank"');
    expect(html).toContain('rel="noopener noreferrer"');
    expect(visibleText(html)).toContain("Open post");
  });

  it("names each link by its post, so a screen reader's list of links is not identical entries", () => {
    expect(renderSignal(makeSignal())).toContain(
      'aria-label="Open post by Jane Example (opens in a new tab)"',
    );
    expect(renderSignal(makeSignal({ author_name: null }))).toContain(
      'aria-label="Open post (opens in a new tab)"',
    );
    expect(renderSaved(makeSave())).toContain(
      'aria-label="Open post, saved 1 day ago (opens in a new tab)"',
    );
  });

  it("drops a tracking query string from the link", () => {
    const html = renderSignal(makeSignal({ post_url: `https://www.linkedin.com/posts/x?rcm=abc#f` }));
    expect(html).toContain('href="https://www.linkedin.com/posts/x"');
    expect(html).not.toContain("rcm=");
  });

  it.each([
    ["another host", "https://www.example.com/posts/x"],
    ["a javascript: url", "javascript:alert(1)"],
    ["plain http", "http://www.linkedin.com/posts/x"],
    ["a lookalike host", "https://linkedin.com.evil.example/posts/x"],
    ["an empty value", ""],
  ])("omits the link entirely for %s", (_name, post_url) => {
    const html = renderSignal(makeSignal({ post_url }));
    expect(html).not.toContain("<a ");
    expect(visibleText(html)).not.toContain("Open post");
  });
});

describe("what a result card shows", () => {
  it("shows only computed fields: species, posted time, author, comments", () => {
    const html = renderSignal(makeSignal({ comment_count: 12, species: "hiring_drive" }));
    const text = visibleText(html);
    expect(text).toContain("Hiring drive");
    expect(text).toContain("Posted 3 days ago");
    expect(text).toContain("Jane Example");
    expect(text).toContain("12 comments");
  });

  it("labels an unclassified post neutrally and never as confirmed hiring", () => {
    const text = visibleText(renderSignal(makeSignal({ species: "unclassified" })));
    expect(text).toContain("Unclassified");
    expect(text).not.toMatch(/actively hiring|confirmed hiring|is hiring/i);
  });

  it("shows the role badge only when it is true -- never for false or unknown", () => {
    expect(visibleText(renderSignal(makeSignal({ role_match: true })))).toContain("Role words found");
    expect(visibleText(renderSignal(makeSignal({ role_match: false })))).not.toContain("Role words found");
    expect(visibleText(renderSignal(makeSignal({ role_match: null })))).not.toContain("Role words found");
  });

  it("colours the role badge as evidence (gold), not as a verified fact (emerald), and does not claim a match (F7)", () => {
    const html = renderSignal(makeSignal({ role_match: true }));
    expect(html).toContain('class="bj-badge-gold">Role words found<');
    expect(html).not.toContain("bj-badge-emerald");
    expect(visibleText(html)).not.toContain("Matches this role");
  });

  it("leaves out the author and comment count when they are not known, without inventing any", () => {
    const text = visibleText(renderSignal(makeSignal({ author_name: null, comment_count: null })));
    expect(text).not.toContain("Jane Example");
    expect(text).not.toContain("comment");
    expect(text).not.toContain("null");
  });

  it("says 'time unknown' when neither the post time nor the index's stamp is available", () => {
    const text = visibleText(makeSignalHtml({ posted_at: null, age_hint: null }));
    expect(text).toContain("Posted time unknown");
  });

  it("falls back to the index's own stamp and labels it as such", () => {
    const text = visibleText(makeSignalHtml({ posted_at: null, age_hint: "3 days ago" }));
    expect(text).toContain("Posted 3 days ago (per search index)");
  });

  it("notes a possible registry match, and says nothing when it does not apply", () => {
    expect(visibleText(renderSignal(makeSignal({ registry_match: "possible" })))).toContain(
      "May match a job listing we already track.",
    );
    expect(visibleText(renderSignal(makeSignal({ registry_match: null })))).not.toContain("job listing we");
  });

  it("never names the source site or invents an 'actively hiring' or connection badge", () => {
    const text = visibleText(
      renderSignal(makeSignal({ role_match: true, comment_count: 3, species: "referral_offer" }), {
        embedOpen: true,
      }),
    );
    expect(text).not.toMatch(/linkedin|actively hiring|1st|2nd|3rd\+|connection/i);
  });

  it("renders a hostile author name as text, never as markup", () => {
    const html = renderSignal(makeSignal({ author_name: '<img src=x onerror="alert(1)">' }));
    expect(html).not.toContain("<img");
    expect(html).toContain("&lt;img");
  });
});

describe("save controls", () => {
  it("offers 'Save' when unsaved, and disables it while a save is in flight", () => {
    const idle = renderSignal(makeSignal());
    expect(idle).toMatch(/<button[^>]*>Save<\/button>/);
    expect(idle).not.toMatch(/<button[^>]*aria-disabled[^>]*>Save</);

    const inFlight = renderSignal(makeSignal(), { saving: true });
    expect(inFlight).toMatch(/<button[^>]*aria-disabled="true"[^>]*>Saving\.\.\.<\/button>/);
    // never the `disabled` attribute: it would drop the person's keyboard focus (F4)
    expect(inFlight).not.toMatch(/<button[^>]* disabled[ =>]/);
  });

  it("shows a disabled 'Saved' once saved, so a repeated click has nothing to click", () => {
    const html = renderSignal(makeSignal(), { saved: true });
    expect(html).toMatch(/<button[^>]*aria-disabled="true"[^>]*>Saved<\/button>/);
    expect(html).not.toMatch(/<button[^>]* disabled[ =>]/);
    expect(html).not.toMatch(/<button[^>]*>Save<\/button>/);
  });

  it("gives the Save button the id focus returns to after an Unsave", () => {
    expect(renderSignal(makeSignal())).toContain(`id="${saveButtonId(ID)}"`);
  });

  it("offers a way to un-save from a saved result card, but only when the saved row is known", () => {
    // "Unsave", not "Remove": on a result card "Remove" could be read as
    // removing the card from the results.
    const known = renderSignal(makeSignal(), { saved: true, onUnsave: () => {} });
    expect(visibleText(known)).toContain("Unsave");
    expect(known).toContain('aria-label="Unsave post by Jane Example"');
    const unknown = renderSignal(makeSignal(), { saved: true, onUnsave: null });
    expect(visibleText(unknown)).not.toContain("Unsave");
    const notSaved = renderSignal(makeSignal(), { saved: false, onUnsave: () => {} });
    expect(visibleText(notSaved)).not.toContain("Unsave");
  });

  it("disables Unsave while it is in flight", () => {
    const html = renderSignal(makeSignal(), { saved: true, onUnsave: () => {}, unsaving: true });
    expect(html).toMatch(/<button[^>]*aria-disabled="true"[^>]*>Unsaving\.\.\.<\/button>/);
    expect(html).not.toMatch(/<button[^>]* disabled[ =>]/);
  });

  it("shows a card-level error as an alert", () => {
    const html = renderSignal(makeSignal(), { error: "Could not save that post." });
    expect(html).toContain('role="alert"');
    expect(visibleText(html)).toContain("Could not save that post.");
  });
});

describe("saved post card", () => {
  it("reads the post's time off its own id, so it is not stuck saying 'time unknown'", () => {
    const id = ((BigInt(NOW.getTime() - 5 * 86_400_000) << 22n) | 12345n).toString();
    const text = visibleText(
      renderSaved(makeSave({ activity_id: id, embed_url: `${EMBED_URL_PREFIX}${id}` })),
    );
    expect(text).toContain("Saved 1 day ago");
    expect(text).toContain("Posted 5 days ago");
  });

  it("offers Remove, disabling it while a removal is in flight", () => {
    expect(visibleText(renderSaved(makeSave()))).toContain("Remove");
    const busy = renderSaved(makeSave(), { removing: true });
    expect(busy).toMatch(/<button[^>]*aria-disabled="true"[^>]*>Removing\.\.\.<\/button>/);
    expect(busy).not.toMatch(/<button[^>]* disabled[ =>]/);
  });

  it("tells saved cards apart for assistive tech by when each was saved", () => {
    const html = renderSaved(makeSave());
    expect(html).toContain('aria-label="Show post, saved 1 day ago"');
    expect(html).toContain('aria-label="Remove post, saved 1 day ago"');
  });
});

function makeSignalHtml(overrides: Partial<HiringSignal>): string {
  return renderSignal(makeSignal(overrides));
}


// A button that is aria-disabled stays focusable, so pressing it must do nothing
// (the guard moved from the `disabled` attribute into the handler).
describe("pressing a busy button is a no-op (F4)", () => {
  function signalCard(overrides: Partial<SignalCardProps> = {}) {
    return SignalCard({
      signal: makeSignal(),
      now: NOW,
      saved: false,
      saving: false,
      embedOpen: false,
      error: null,
      onToggleEmbed: () => {},
      onSave: () => {},
      onUnsave: null,
      unsaving: false,
      ...overrides,
    });
  }

  it("Save calls onSave when idle, and does nothing while saving or once saved", () => {
    let calls = 0;
    const onSave = () => {
      calls += 1;
    };
    press(onlyButton(expand(signalCard({ onSave })), "Save"));
    expect(calls).toBe(1);
    press(onlyButton(expand(signalCard({ onSave, saving: true })), "Saving..."));
    press(onlyButton(expand(signalCard({ onSave, saved: true })), "Saved"));
    expect(calls).toBe(1);
  });

  it("Unsave calls onUnsave, and does nothing while it is in flight", () => {
    let calls = 0;
    const onUnsave = () => {
      calls += 1;
    };
    press(onlyButton(expand(signalCard({ saved: true, onUnsave })), "Unsave"));
    expect(calls).toBe(1);
    press(onlyButton(expand(signalCard({ saved: true, onUnsave, unsaving: true })), "Unsaving..."));
    expect(calls).toBe(1);
  });

  it("marks the busy buttons aria-disabled and never `disabled`", () => {
    for (const overrides of [{ saving: true }, { saved: true }]) {
      const buttons = findAll(expand(signalCard(overrides)), (el) => el.type === "button");
      expect(buttons.some((b) => prop(b, "aria-disabled") === true)).toBe(true);
      expect(buttons.every((b) => prop(b, "disabled") === undefined)).toBe(true);
    }
  });

  it("Remove on a saved card calls onRemove, and does nothing while removing", () => {
    let calls = 0;
    const onRemove = () => {
      calls += 1;
    };
    const card = (removing: boolean) =>
      SavedPostCard({
        save: makeSave(),
        now: NOW,
        embedOpen: false,
        removing,
        error: null,
        onToggleEmbed: () => {},
        onRemove,
      });
    press(onlyButton(expand(card(false)), "Remove"));
    expect(calls).toBe(1);
    press(onlyButton(expand(card(true)), "Removing..."));
    expect(calls).toBe(1);
  });
});
