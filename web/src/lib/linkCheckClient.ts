// The one request "Check posting" makes: POST /applications/{id}/check-link, no body. The server picks the
// link itself (the application's saved posting address) and reads the company's own board; the browser
// sends nothing about it and keeps nothing of the reply beyond the page's own state.
//
// It never throws: a reply that is not an answer (a 429 for the person's own limit, the 503 of the kill
// switch, a network failure, a body this page cannot read) comes back as an entry with its words, so the
// page has no try/catch of its own around it. The fetcher is passed in (in the app it is `apiFetch`)
// because api.ts pulls in the Supabase client, which throws at import time without env vars.

import { failureWords, parseLinkCheck, UNREADABLE_WORDS, type LinkCheckEntry } from "./linkCheck";

export type LinkCheckFetcher = <T>(path: string, init?: RequestInit) => Promise<T>;

export function linkCheckPath(applicationId: string): string {
  return `/applications/${encodeURIComponent(applicationId)}/check-link`;
}

export async function checkApplicationLink(
  fetcher: LinkCheckFetcher,
  applicationId: string,
  now: () => number = Date.now,
): Promise<Exclude<LinkCheckEntry, { kind: "pending" }>> {
  try {
    const result = parseLinkCheck(await fetcher<unknown>(linkCheckPath(applicationId), { method: "POST" }));
    return result === null ? { kind: "unanswered", words: UNREADABLE_WORDS } : { kind: "answered", result, receivedAt: now() };
  } catch (error) {
    return { kind: "unanswered", words: failureWords(error) };
  }
}
