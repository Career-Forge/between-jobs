// Turning the free text a profile holds for "country" ("US", "USA", "United States",
// "india") into the names a form's country list might use. Matching is against the options
// the form actually offers, so this only has to produce candidates, not a full gazetteer;
// anything that matches no option is left empty and reported by the caller, never guessed.

/** Lower case, accents and punctuation removed, whitespace collapsed: "Côte d'Ivoire" and
 * "cote d ivoire" are the same key; "U.S.A." is "usa". */
export function countryKey(text: string): string {
  return text
    .normalize("NFKD")
    .replace(/\p{M}+/gu, "")
    .toLowerCase()
    .replace(/[^\p{L}\p{N}\s]/gu, " ")
    .replace(/\s+/gu, " ")
    .trim();
}

// Spellings that are not an ISO 3166 code and that Intl does not know. Deliberately short:
// the very common ones, each unambiguous.
const ALIAS_TO_REGION: Record<string, string> = {
  usa: "US",
  "united states of america": "US",
  america: "US",
  uk: "GB",
  "great britain": "GB",
  britain: "GB",
  uae: "AE",
};

let regionNames: Intl.DisplayNames | null | undefined;

function englishRegionName(code: string): string | null {
  if (regionNames === undefined) {
    try {
      regionNames = new Intl.DisplayNames(["en"], { type: "region", fallback: "none" });
    } catch {
      regionNames = null;
    }
  }
  try {
    const name = regionNames?.of(code.toUpperCase());
    // CLDR has an entry for the reserved code ZZ; it is not a country.
    return name !== undefined && name !== "Unknown Region" ? name : null;
  } catch {
    return null;
  }
}

/** The ISO region a profile country denotes, when it is one: a two-letter code or a known
 * alias. A country written out in full is not looked up here (it is matched by name). */
export function regionCodeOf(profileCountry: string): string | null {
  const key = countryKey(profileCountry);
  // Dotted spellings key with spaces ("U.S.A." is "u s a", "U.S." is "u s"): the squeezed form
  // is what the alias table and the two-letter test see.
  const squeezed = key.replace(/\s+/gu, "");
  const alias = ALIAS_TO_REGION[key] ?? ALIAS_TO_REGION[squeezed];
  if (alias !== undefined) return alias;
  return /^[a-z]{2}$/u.test(squeezed) ? squeezed.toUpperCase() : null;
}

/** Every name the form's list might use for the profile's country, as comparison keys: the
 * text as written, and -- for a code or alias -- its English name. */
export function countryCandidateKeys(profileCountry: string): string[] {
  const keys = new Set<string>();
  const written = countryKey(profileCountry);
  if (written !== "") keys.add(written);
  const region = regionCodeOf(profileCountry);
  if (region !== null) {
    const name = englishRegionName(region);
    if (name !== null) keys.add(countryKey(name));
  }
  return [...keys];
}

/** The name to type into a searchable list: the English name for a code or alias, else the
 * text as the profile has it. */
export function countrySearchText(profileCountry: string): string {
  const region = regionCodeOf(profileCountry);
  return (region === null ? null : englishRegionName(region)) ?? profileCountry.trim();
}

// ---- a country named in running text ("work in Germany") -----------------------------------------

// Spellings that name a country in a sentence but are not its English name in CLDR.
const NAMED_COUNTRY_ALIASES: Record<string, string> = {
  us: "US",
  usa: "US",
  "united states of america": "US",
  america: "US",
  uk: "GB",
  "great britain": "GB",
  britain: "GB",
  uae: "AE",
  turkey: "TR",
  "czech republic": "CZ",
  "ivory coast": "CI",
  burma: "MM",
  "hong kong": "HK",
  macau: "MO",
};

// The ISO 3166-1 alpha-2 codes. CLDR also names codes that are not countries (the EU, the UN),
// that duplicate one ("UK" is the reserved alias of GB) or that no longer exist ("DD", which
// still reads "Germany"), none of which a work-eligibility question can be about.
const ISO_COUNTRY_CODES = new Set(
  (
    "AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ BA BB BD BE BF BG BH BI BJ BL BM BN BO BQ BR BS BT BV BW BY BZ " +
    "CA CC CD CF CG CH CI CK CL CM CN CO CR CU CV CW CX CY CZ DE DJ DK DM DO DZ EC EE EG EH ER ES ET FI FJ FK FM FO " +
    "FR GA GB GD GE GF GG GH GI GL GM GN GP GQ GR GS GT GU GW GY HK HM HN HR HT HU ID IE IL IM IN IO IQ IR IS IT JE " +
    "JM JO JP KE KG KH KI KM KN KP KR KW KY KZ LA LB LC LI LK LR LS LT LU LV LY MA MC MD ME MF MG MH MK ML MM MN MO " +
    "MP MQ MR MS MT MU MV MW MX MY MZ NA NC NE NF NG NI NL NO NP NR NU NZ OM PA PE PF PG PH PK PL PM PN PR PS PT PW " +
    "PY QA RE RO RS RU RW SA SB SC SD SE SG SH SI SJ SK SL SM SN SO SR SS ST SV SX SY SZ TC TD TF TG TH TJ TK TL TM " +
    "TN TO TR TT TV TW TZ UA UG UM US UY UZ VA VC VE VG VI VN VU WF WS YE YT ZA ZM ZW"
  ).split(" "),
);

/** How many ISO 3166-1 alpha-2 codes the list above holds (249), exposed so a test can pin it. */
export const ISO_COUNTRY_CODE_COUNT = ISO_COUNTRY_CODES.size;

let nameToRegion: Map<string, string> | undefined;

function regionByEnglishName(): Map<string, string> {
  if (nameToRegion !== undefined) return nameToRegion;
  const map = new Map<string, string>();
  for (const code of ISO_COUNTRY_CODES) {
    const name = englishRegionName(code);
    if (name !== null && !map.has(countryKey(name))) map.set(countryKey(name), code);
  }
  nameToRegion = map;
  return map;
}

/**
 * The ISO region a country NAMED in running text denotes ("Germany", "the United States",
 * "U.S.", "UK"), or null when the text is not exactly one country's name. A closed set: every
 * English country name Intl knows, plus a short list of common alternatives. Anything else --
 * a phrase, two countries, a region such as "the EU" -- is null, never a guess.
 */
export function regionCodeOfNamedCountry(text: string): string | null {
  const key = countryKey(text).replace(/^the /u, "");
  if (key === "") return null;
  // Also a US state's name: nothing in a bare "Georgia" says which one is meant.
  if (key === "georgia") return null;
  const alias = NAMED_COUNTRY_ALIASES[key] ?? NAMED_COUNTRY_ALIASES[key.replace(/\s+/gu, "")];
  if (alias !== undefined) return alias;
  return regionByEnglishName().get(key) ?? null;
}
