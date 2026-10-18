// The origin the page's link-preview tags (og:image, twitter:image) point at.
//
// Those two tags must be absolute URLs, because the crawlers that read them do not resolve a
// relative one, so the build has to know where the site lives. VITE_SITE_URL names it; left unset it
// is the hosted site, which is right for the hosted build and a harmless default for anyone else
// (the preview image is the Between Jobs card either way). A self-hosted or staging deployment sets
// VITE_SITE_URL to its own origin so its preview image is fetched from itself.
//
// There is deliberately no og:url: one static value cannot be right for every route of a
// single-page app, and a crawler that finds none uses the address it was given.

export const HOSTED_SITE_ORIGIN = "https://between-jobs.tech";

/** The https origin to build absolute URLs on, with no trailing slash. Throws, naming the variable
 *  and not quoting its value, when the value is not a bare https origin. */
export function resolveSiteOrigin(raw: string | undefined): string {
  if (raw === undefined || raw.trim() === "") return HOSTED_SITE_ORIGIN;
  const trimmed = raw.trim();
  let url: URL;
  try {
    url = new URL(trimmed);
  } catch {
    throw new Error("VITE_SITE_URL is not a valid absolute URL.");
  }
  if (url.protocol !== "https:" || !/^https:\/\/[^/\s]/i.test(trimmed)) {
    throw new Error("VITE_SITE_URL is not of the form https://host[:port].");
  }
  if (url.username !== "" || url.password !== "" || url.search !== "" || url.hash !== "" || url.pathname !== "/") {
    throw new Error("VITE_SITE_URL must be an origin only: no credentials, path, query or fragment.");
  }
  return url.origin;
}
