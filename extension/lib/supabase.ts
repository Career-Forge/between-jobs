import { createClient, type SupportedStorage } from "@supabase/supabase-js";

// browser-extension.md D2 -- the extension owns an independent Supabase
// Auth session (not the web tab's), so Proposal §29.5's storage rules
// ("prefer in-memory session storage for access tokens... short-lived
// tokens and server-side revocation") land on `chrome.storage` rather
// than `localStorage`, which doesn't exist in an MV3 service worker at
// all. There is no official Supabase-shipped chrome.storage adapter
// (supabase/discussions#21923 is still open) -- this is the current,
// confirmed-working DIY pattern.
//
// `chrome.storage.session`, not `.local` -- a real gap an adversarial
// review caught: `chrome.storage.local` is readable by ANY context the
// manifest's `storage` permission covers, content scripts included, so
// "the content script never imports lib/supabase.ts" was a source-code
// discipline, not a real isolation boundary; any future code path that
// ever called `chrome.storage.local.get(...)` from content.ts (or a
// compromised dependency) would read the live session straight off a
// page on jobs.lever.co. `chrome.storage.session`'s access level
// defaults to `TRUSTED_CONTEXTS` -- extension pages and the service
// worker only, content scripts excluded by the browser itself, no extra
// call needed. Real tradeoff, accepted deliberately: session storage
// doesn't survive a full browser restart, so a signed-in user has to
// sign in again after one (not on every service-worker idle-kill, which
// happens far more often and does NOT clear it) -- closer to Proposal
// §29.5's own "short-lived session storage" language than the original
// `.local` choice was anyway.
const chromeStorageAdapter: SupportedStorage = {
  async getItem(key) {
    const result = await chrome.storage.session.get(key);
    return (result[key] as string | undefined) ?? null;
  },
  async setItem(key, value) {
    await chrome.storage.session.set({ [key]: value });
  },
  async removeItem(key) {
    await chrome.storage.session.remove(key);
  },
};

/** A fresh client every call, deliberately NOT memoized as a singleton.
 * The service worker and the side panel are separate JS realms that can't
 * share an in-memory object, and a `GoTrueClient` caches its session in
 * memory once created -- memoizing here would mean a sign-in in the side
 * panel (which writes to `chrome.storage.session`) could go unnoticed by
 * an already-initialized background client until something explicitly
 * refreshed it. Creating fresh each call makes every `getSession()`
 * initialize from the current storage contents, so a sign-in is visible
 * to the other context immediately -- the extra client-construction cost
 * is negligible next to a real network call. */
export function getSupabaseClient(): ReturnType<typeof createClient> {
  return createClient(import.meta.env.WXT_SUPABASE_URL, import.meta.env.WXT_SUPABASE_PUBLISHABLE_KEY, {
    auth: {
      storage: chromeStorageAdapter,
      persistSession: true,
      detectSessionInUrl: false,
      flowType: "pkce",
      // No chrome.alarms-driven refresh loop exists (an earlier version
      // of this comment claimed one that was never actually built --
      // caught by adversarial review). What actually keeps sessions
      // fresh: auth-js's own `getSession()` performs a just-in-time
      // refresh check on every call, unconditionally, regardless of this
      // flag (confirmed directly against the installed auth-js source);
      // `lib/api.ts`'s `accessToken()` calls `getSession()` immediately
      // before every backend request, so a stale token is refreshed
      // right before it would matter. `autoRefreshToken`'s own
      // setTimeout-based proactive loop is left off because it would die
      // with the service worker anyway (MV3 kills an idle worker after
      // ~30s) -- it would only ever pre-refresh a token that the
      // just-in-time check would have refreshed regardless.
      autoRefreshToken: false,
    },
    // auth-js's fetch fallback references XMLHttpRequest, which doesn't
    // exist in a service worker -- bind the native fetch explicitly
    // (confirmed against a real, currently-open supabase-js issue).
    global: { fetch: (...args: Parameters<typeof fetch>) => fetch(...args) },
  });
}
