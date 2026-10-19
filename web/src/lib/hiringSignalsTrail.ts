// The trail lines of the Hiring signals page, built from the fields a reply returned and nothing else
// (lib/trail.ts is the grammar and the omit rule). Pure: the caller passes `now`.
//
//   the search          "○ via Firecrawl · software engineer -- Pune -- last 3 days · Global wording"
//   a result card       "○ Tagged by code · no model involved · Posted Sep 18"
//   a saved post        "● You saved this · Sep 22"
//   a saved search      "● You saved this · Sep 22"
//
// The tag on a card (hiring drive, referral, and so on) is a pattern match over the post's text done
// by the server: code, not a model. A post's time is decoded from its own address, so it is a
// returned field. Nothing here names the site the posts come from, and nothing claims a post is a
// hiring call: "Unclassified" says no pattern matched.

import { providerLabel } from "./hiringSignals";
import type { SavedHiringSearch, TabSearchResponse, TabSignal } from "./hiringSignalsTabTypes";
import { localeLabel } from "./hiringSignalsTab";
import type { SavedPost } from "./hiringSignalsTypes";
import { formatTrailDate, type TrailInput } from "./trail";

// The line above a search's results: what searched, for what, in which wording. Plain code and the
// person's own provider index, no model.
export function sourceTrail(response: TabSearchResponse): TrailInput {
  return {
    glyph: "code",
    actor: `via ${providerLabel(response.provider)}`,
    basis: response.query_label,
    extra: response.locale === null ? null : `${localeLabel(response.locale)} wording`,
  };
}

export function signalTrail(signal: TabSignal, now: Date, timeZone?: string): TrailInput {
  const date = signal.posted_at === null ? null : formatTrailDate(signal.posted_at, { now, timeZone });
  return {
    glyph: "code",
    actor: "Tagged by code",
    basis: "no model involved",
    when: date === null ? null : `Posted ${date}`,
  };
}

// The day the person saved a post or a search, when the stored date can be read.
export function savedTrail(item: Pick<SavedPost, "created_at"> | Pick<SavedHiringSearch, "created_at">, now: Date, timeZone?: string): TrailInput {
  return {
    glyph: "you",
    actor: "You saved this",
    when: formatTrailDate(item.created_at, { now, timeZone }),
  };
}
