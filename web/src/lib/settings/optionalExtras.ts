// Which URL fragments open the Settings page's "Optional extras" disclosure. The disclosure is closed
// by default, so a link that points at it, or at one key inside it, has to open it.
//
//   #search-keys     the section itself. This is the fragment the API and the pages that need a search
//                    key (Discover, Hiring signals) already link to, so it stays the section's id.
//   #search-<key>    one key's row (#search-brave): the ids of the rows, from the cards.
//
// Pure module: no React.

import { SEARCH_PROVIDER_CARDS } from "../providerStatus";

export const OPTIONAL_EXTRAS_ID = "search-keys";

export function opensOptionalExtras(hash: string): boolean {
  const id = hash.replace(/^#/, "");
  if (id === OPTIONAL_EXTRAS_ID) return true;
  return SEARCH_PROVIDER_CARDS.some((card) => id === `search-${card.provider}`);
}
