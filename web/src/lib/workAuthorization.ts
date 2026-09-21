import type { CanonicalProfile } from "./profileTypes";

// Work-authorization self-report guidance (work-authorization-status.md,
// D1): ONE free-text field for everyone -- `personal.work_authorization`,
// already in the schema -- stays a plain string, region-agnostic, no
// country selector, no per-region branching anywhere in the UI. The field
// shipped with zero guidance since it was added, which is directly why a
// bare "Indian Citizen" once read as a complete answer to a person who was
// never shown what a complete one looks like (the real incident this
// feature fixes -- E3b's drafting prompt extrapolated a specific,
// unverifiable legal claim from that one bare citizenship fact). The fix
// is not a smarter field, it's a properly-labeled one with real examples:
// one real, researched sentence per region below, so a person can find the
// shape closest to their own situation and adapt it in their own words.
//
// Every sentence here is real product copy, not a placeholder -- confirmed
// via live research (see the plan doc), reproduced verbatim. Do not reword
// or "improve" these: the exact vocabulary is load-bearing (Canada never
// says "sponsorship" for employment immigration; India's OCI carve-out is
// private-sector-only; the EU/EEA free-movement line is separate from the
// nationally-fragmented Blue Card/national-permit line below it).

export interface WorkAuthorizationRegionExample {
  region: string;
  sentences: string[];
}

export const WORK_AUTHORIZATION_REGION_EXAMPLES: WorkAuthorizationRegionExample[] = [
  {
    region: "United States",
    sentences: [
      "I'm on F-1 OPT authorization and will need H-1B sponsorship to continue working in the US after it expires.",
    ],
  },
  {
    region: "Canada",
    sentences: [
      "I hold an open Post-Graduation Work Permit valid until March 2028, so I can work for any Canadian employer with no LMIA needed.",
    ],
  },
  {
    region: "EU / EEA",
    sentences: [
      "I'm an Indian citizen currently on an EU Blue Card tied to my employer in Munich, Germany -- it can transfer to a new employer without restarting the process.",
    ],
  },
  {
    // Two real, distinct situations -- never collapsed into one. NRI is a
    // tax/residency label, not an immigration category, and is deliberately
    // not mentioned anywhere here -- it doesn't bear on this field.
    region: "India",
    sentences: [
      "I'm an Indian citizen based in India -- no work authorization is needed for Indian roles, including at multinational employers.",
      "I hold an OCI card and can work for any private employer in India without sponsorship, though I can't hold Indian government positions.",
    ],
  },
];

// India-specific note, shown alongside the examples above. Confirmed live
// (Cloudflare's own Bangalore postings carry the exact same screening
// question as its US postings, via a shared global Greenhouse template):
// large multinationals often carry their global ATS template over
// regardless of local legal necessity, even though India-headquartered
// employers usually have no legal reason to ask at all. The real condition
// is named instead of a reassuring default -- an India-based user is never
// told this "usually doesn't apply" to them; they're given the actual
// pattern and left to judge it against their own employer.
export const WORK_AUTHORIZATION_INDIA_NOTE =
  "India-headquartered employers often skip this question entirely -- there's no local " +
  "legal requirement to ask it. Multinationals frequently carry their global application " +
  "template over anyway and do ask, sometimes in the exact same wording as their US " +
  "postings. Whether it applies to you depends on the employer, not just on where you're " +
  "based.";

// The versioned-edit mutation (useProfileEditor's `mutate` contract, see
// PersonalDetailsCard in Profile.tsx) -- touches personal.work_authorization
// only. Both spreads are shallow, so every other field on the profile (and
// every other field on `personal`) keeps its original object identity --
// there is nothing here that could touch experience/skills/etc.
export function applyWorkAuthorization(
  profile: CanonicalProfile,
  value: string,
): CanonicalProfile {
  return {
    ...profile,
    personal: { ...profile.personal, work_authorization: value },
  };
}
