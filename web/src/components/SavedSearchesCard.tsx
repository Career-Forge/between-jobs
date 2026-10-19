import { useSavedSearches } from "../lib/useSavedSearches";
import { SavedSearchesCardView } from "./SavedSearchesCardView";

// The person's saved searches, with Pause / Resume and Delete, on the Settings page. Thin glue
// over the tested pieces (lib/savedSearches.ts, lib/useSavedSearches.ts,
// components/SavedSearchesCardView.tsx). It is here, and not only on Discover, because the
// tester-programme gate leaves Settings open to someone who has not joined or has withdrawn, and a
// saved search keeps running in the background for them until it is paused or deleted.
export function SavedSearchesCard() {
  const saved = useSavedSearches();
  return <SavedSearchesCardView state={saved.state} actions={{ toggle: saved.toggle, remove: saved.remove, retry: saved.retry }} now={new Date()} />;
}
