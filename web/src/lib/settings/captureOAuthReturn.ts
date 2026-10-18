// Imported first by main.tsx, so that its body runs before the Supabase client or the router has
// read the address: a return from OpenRouter (?code=...&state=...) is taken off the address here,
// and what it said is handed to the connect store the Settings page reads. See openrouterOAuth.ts.

import { captureReturn, connectStore, tabStorage } from "./openrouterOAuth";

try {
  connectStore.receive(captureReturn(window, Date.now(), tabStorage()));
} catch {
  // An address that cannot be rewritten leaves the page as it was; the flow then fails on the
  // state check like any return that was not ours.
}
