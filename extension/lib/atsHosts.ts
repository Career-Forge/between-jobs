import type { AtsType } from "./types";

// Which ATS a hostname belongs to. Lived in entrypoints/content.ts until
// E6, when background.ts needed the same answer to stop trusting the
// `atsType`/`url` pair a content script sends it: content scripts run next
// to attacker-controlled pages, so the service worker re-derives the
// relationship rather than interpolating whatever string arrives.
const ATS_HOST_SUFFIXES: [suffix: string, atsType: AtsType][] = [
  [".lever.co", "lever"],
  [".greenhouse.io", "greenhouse"],
  [".ashbyhq.com", "ashby"],
];

export function detectAtsType(hostname: string): AtsType | null {
  for (const [suffix, type] of ATS_HOST_SUFFIXES) {
    if (hostname === suffix.slice(1) || hostname.endsWith(suffix)) return type;
  }
  return null;
}

export function isAtsType(value: unknown): value is AtsType {
  return value === "lever" || value === "greenhouse" || value === "ashby";
}

// The application-form route each ATS serves on top of the posting's own
// URL. Lever's apply form lives at `<posting>/apply`, Ashby's at
// `<posting>/application`; Greenhouse renders the form on the posting URL
// itself. Job tracking stores the posting URL, so a tab sitting on the
// form route never matched it.
const FORM_ROUTE_SUFFIX: Partial<Record<AtsType, string>> = {
  lever: "/apply",
  ashby: "/application",
};

/**
 * The URL to ask the backend about for this tab: scheme + host + path with
 * the ATS's form-route suffix and any trailing slash removed, and NO query
 * string or fragment. Those carry per-visit tracking parameters (utm_*,
 * gh_src, lever-source) and, on some ATS links, per-candidate tokens --
 * none of which belong in a GET query string that lands in server and
 * proxy access logs, and none of which the stored posting URL contains.
 *
 * Returns null for anything that isn't an https URL on the expected ATS's
 * own host, so a mislabeled `atsType` from a compromised content script
 * can't be used to probe the lookup route with an arbitrary URL.
 */
export function canonicalLookupUrl(href: string, atsType: AtsType): string | null {
  let url: URL;
  try {
    url = new URL(href);
  } catch {
    return null;
  }
  if (url.protocol !== "https:" || detectAtsType(url.hostname) !== atsType) return null;
  let path = url.pathname.replace(/\/+$/, "");
  const suffix = FORM_ROUTE_SUFFIX[atsType];
  if (suffix !== undefined && path.toLowerCase().endsWith(suffix)) {
    path = path.slice(0, -suffix.length);
  }
  return `${url.origin}${path}`;
}
