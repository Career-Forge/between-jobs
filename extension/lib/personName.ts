// Splitting one full-name string into the "first name" and "last name" boxes some forms
// have. The profile holds a single `name`; nothing in it says which part is which, so this
// is a spelling heuristic and is written to be honest about it:
//
//  - it only rearranges the words it was given. It never invents, translates or re-cases a
//    part, and nothing it was given is dropped (an honorific at the front is the one thing it
//    does not carry over).
//  - a part it cannot know stays empty and says so, instead of being filled with a guess.
//    That is the case for a name of one word (there is no last name to place) and for a name
//    written in a script whose word order the spelling does not reveal.
//
// The rule, for a name written in Latin, Cyrillic or Greek letters:
//   - trailing parts after a comma that are a generational suffix or a post-nominal ("Jr.",
//     "III", "Ph.D.", "PMP") are set aside first and stay with the family name.
//   - "Family, Given" (what is left is two parts around one comma) is read as written.
//   - otherwise the LAST word is the family name, together with any lower-case particle
//     directly in front of it ("van der", "de la", "bin", ...) and a trailing generational
//     suffix ("Jr.", "III"); everything before that is given names.
//   - a hyphenated word stays one word ("Jean-Luc", "Smith-Jones").
// A family name made of two plain words with no particle ("Garcia Marquez") cannot be told
// from a middle name and is split after the first of them like any other three-word name;
// that is the one wrong answer this rule knowingly accepts, and the person sees the boxes.
// A post-nominal this file does not list ("Jane Doe, XYZ") is not guessed at either: a short
// all-capitals word after a comma, behind a name of two or more words, leaves both boxes empty
// and says so, rather than putting the credential in the first-name box.

export type NameSplitIssue =
  | "single_name" // one word: the first name is that word, the last name is unknown
  | "order_unknown"; // the script does not reveal which word is the family name: both unknown

export interface SplitName {
  first: string | null;
  last: string | null;
  issue: NameSplitIssue | null;
}

// Han, Hiragana, Katakana, Hangul (and the CJK compatibility forms), Thai, Lao, Khmer,
// Myanmar, Tibetan: written without a reliable given/family order that the spelling shows
// (family name first in much of East Asia, but not always, and often romanised the other
// way round in a profile).
const UNKNOWN_ORDER_SCRIPT =
  /[\p{Script=Han}\p{Script=Hiragana}\p{Script=Katakana}\p{Script=Hangul}\p{Script=Thai}\p{Script=Lao}\p{Script=Khmer}\p{Script=Myanmar}\p{Script=Tibetan}]/u;

const HONORIFICS = new Set(["mr", "mrs", "ms", "miss", "mx", "dr", "prof", "sir", "dame"]);
// Generational suffixes and post-nominals that trail a name. A bare "V" is left out: it is as
// likely a middle initial as "the fifth".
const SUFFIXES = new Set([
  "jr",
  "sr",
  "ii",
  "iii",
  "iv",
  "phd",
  "md",
  "esq",
  "cpa",
  "mba",
  "msc",
  "bsc",
  "dds",
  "dvm",
  "pmp",
  "cfa",
  "cissp",
  "csm",
  "rn",
  "ms",
  "np",
  "pe",
]);
// Post-nominals that are also ordinary surnames ("Jack Ma", "Mariama Ba"): a suffix only when
// they follow a comma ("Jane Doe, MA"), never as the last word of a name written without one.
const COMMA_ONLY_SUFFIXES = new Set(["ma", "ba", "pa"]);
const PARTICLES = new Set([
  "van",
  "von",
  "der",
  "den",
  "ter",
  "ten",
  "de",
  "del",
  "della",
  "di",
  "da",
  "dos",
  "das",
  "du",
  "la",
  "le",
  "bin",
  "ibn",
  "bint",
  "al",
  "el",
  "af",
  "av",
]);

function bare(token: string): string {
  return token.replace(/[.,]+$/u, "").toLowerCase();
}

// "Ph.D." and "M.D." are "phd" and "md": every dot goes, not only a trailing one.
function suffixKey(token: string): string {
  return token.replace(/[.,]/gu, "").toLowerCase();
}

function isSuffix(token: string): boolean {
  return SUFFIXES.has(suffixKey(token));
}

function isCommaSuffixPart(part: string): boolean {
  return part.split(" ").every((token) => SUFFIXES.has(suffixKey(token)) || COMMA_ONLY_SUFFIXES.has(suffixKey(token)));
}

// A short word of capitals (with dots) that is not a listed suffix: most likely a credential
// ("XYZ"), possibly an all-caps given name. Which one it is cannot be told from its spelling.
function isUnlistedCredentialShape(part: string): boolean {
  return !part.includes(" ") && /^[A-Z][A-Z.]{1,7}$/u.test(part);
}

function isAllCaps(text: string): boolean {
  return text === text.toUpperCase() && text !== text.toLowerCase();
}

export function splitPersonName(fullName: string): SplitName {
  const text = fullName.normalize("NFC").replace(/\s+/gu, " ").trim();
  if (text === "") return { first: null, last: null, issue: null };
  if (UNKNOWN_ORDER_SCRIPT.test(text)) return { first: null, last: null, issue: "order_unknown" };
  const allCaps = isAllCaps(text);

  // Comma-separated parts, with empty ones (a trailing or leading comma) dropped.
  const parts = text
    .split(",")
    .map((part) => part.trim())
    .filter((part) => part !== "");
  if (parts.length === 0) return { first: null, last: null, issue: null };

  // Trailing parts that are a suffix or a post-nominal ("Jane Doe, Ph.D.", "Smith, John, Jr.")
  // are set aside; they belong to the family name.
  const trailing: string[] = [];
  while (parts.length > 1 && isCommaSuffixPart(parts[parts.length - 1]!)) trailing.unshift(parts.pop()!);

  if (parts.length === 2) {
    const [first, second] = parts as [string, string];
    // "Jane Doe, XYZ": a credential this file does not know, or a given name in capitals.
    if (!allCaps && first.includes(" ") && isUnlistedCredentialShape(second)) {
      return { first: null, last: null, issue: "order_unknown" };
    }
    // "Family, Given", as written.
    return { first: second, last: [first, ...trailing].join(" "), issue: null };
  }
  return splitWords(parts.join(" "), trailing, allCaps);
}

function splitWords(text: string, trailing: string[], allCaps: boolean): SplitName {
  let tokens = text.split(" ");
  while (tokens.length > 1 && HONORIFICS.has(bare(tokens[0]!))) tokens = tokens.slice(1);

  // A trailing suffix belongs to the family name; set it aside while the rest is split.
  let end = tokens.length;
  while (end > 1 && isSuffix(tokens[end - 1]!)) end--;
  const core = tokens.slice(0, end);
  const suffix = [...tokens.slice(end), ...trailing];
  // One word, with or without a suffix: there is no last name to place.
  if (core.length === 1) return { first: [...core, ...suffix].join(" "), last: null, issue: "single_name" };

  // The family name: the last word and the lower-case particles right in front of it. At least
  // one word is always left for the given name.
  let familyStart = core.length - 1;
  while (familyStart > 1 && isParticle(core[familyStart - 1]!, allCaps)) familyStart--;
  return {
    first: core.slice(0, familyStart).join(" "),
    last: [...core.slice(familyStart), ...suffix].join(" "),
    issue: null,
  };
}

function isParticle(token: string, allCaps: boolean): boolean {
  // Particles are written in lower case ("van der Berg"); a capitalised "Van" is as likely a
  // given name. In an all-caps name the case carries no information, so compare without it.
  if (!allCaps && token !== token.toLowerCase()) return false;
  return PARTICLES.has(token.toLowerCase());
}
