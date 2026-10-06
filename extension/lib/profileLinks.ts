// Which of the profile's links a form's link box should get, when the form's own label and
// the signed field map disagree about what the box is for.
//
// A signed map names one Lever box "portfolio or GitHub" and gives it the portfolio link,
// else the GitHub link. But an organisation chooses what to call that box: one calls it
// "Other website", another "GitHub URL". Filling a portfolio link into a box labelled
// "GitHub URL" is wrong, and so is trusting which profile field a link was typed into: a
// person can put their GitHub address in the "portfolio" field. So a link is classified by
// what it IS -- its host -- and the box by what its label says.
//
//   label says GitHub only   -> a link on github.com, from either profile field; if there is
//                               none the box is left empty and the reason is reported (a
//                               profile with no GitHub link, or a GitHub entry that is not a
//                               github.com address, each say so truthfully). A portfolio is
//                               never put in a box that asks for GitHub.
//   label says website only  -> a link that is not GitHub or LinkedIn first, else the GitHub
//                               link (it is a website too).
//   label says both, neither, or cannot be read -> the map's own order, unchanged.
//
// The label is the form's text and untrusted: it only ever chooses between two links the
// person already put in their own profile.

export type LinkKind = "github" | "linkedin" | "site";
export type LinkWant = "github" | "site" | "either";

function hostOf(url: string): string | null {
  const trimmed = url.trim();
  if (trimmed === "") return null;
  try {
    const host = new URL(/^[a-z][a-z0-9+.-]*:\/\//iu.test(trimmed) ? trimmed : `https://${trimmed}`).hostname.toLowerCase();
    return host.replace(/^www\./u, "");
  } catch {
    return null;
  }
}

export function linkKind(url: string): LinkKind | null {
  const host = hostOf(url);
  if (host === null) return null;
  if (host === "github.com") return "github";
  if (host === "linkedin.com" || host.endsWith(".linkedin.com")) return "linkedin";
  return host.includes(".") ? "site" : null;
}

// A link that is GitHub's in some other form than a profile address: a gist, GitHub Pages.
function isOtherGithubHost(url: string): boolean {
  const host = hostOf(url);
  return host !== null && (host.endsWith(".github.com") || host.endsWith(".github.io"));
}

const GITHUB_WORD = /\bgit\s?hub\b/iu;
const SITE_WORD = /\b(?:portfolio|web\s?site|personal (?:web)?site|blog|homepage)\b/iu;

/** What a link box's visible label asks for. Only a label that names exactly one of the two
 * decides; both, neither, or no readable label is "either". */
export function linkWantFromLabel(label: string | null): LinkWant {
  if (label === null) return "either";
  const github = GITHUB_WORD.test(label);
  const site = SITE_WORD.test(label);
  if (github && !site) return "github";
  if (site && !github) return "site";
  return "either";
}

export interface ChosenLink {
  value: string | null;
  /** Set when the box is left empty because the profile has nothing that fits it. */
  skipped?: string;
}

/**
 * The link for a "portfolio or GitHub" box, given the profile's two links (in the map's order
 * of preference) and what the box's label asks for.
 */
export function chooseLinkForBox(links: readonly string[], want: LinkWant): ChosenLink {
  const present = links.filter((link) => link.trim() !== "");
  if (want === "either") return { value: present[0] ?? null };
  const classified = present.map((link) => ({ link, kind: linkKind(link) }));
  if (want === "github") {
    const github = classified.find((c) => c.kind === "github");
    if (github !== undefined) return { value: github.link };
    if (present.length === 0) return { value: null };
    // Something is there but none of it is a github.com profile. Say which kind of "none": a
    // profile with no GitHub link at all (only a portfolio, say) versus a GitHub entry that is
    // not an address this extension can use (a bare handle, a gist, a GitHub Pages site). The
    // second is not "has none", and a bare handle is never turned into a URL by guessing.
    const unusable = classified.some((c) => c.kind === null || isOtherGithubHost(c.link));
    return {
      value: null,
      skipped: unusable
        ? "the form asks for a GitHub profile link, and no link in your profile is a github.com address"
        : "the form asks for a GitHub link and your profile has none",
    };
  }
  const site = classified.find((c) => c.kind === "site") ?? classified.find((c) => c.kind === "github");
  return { value: site?.link ?? null };
}
