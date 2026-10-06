import { regionCodeOfNamedCountry } from "./countryNames";
import { normalizeQuestionLabel } from "./questionMatching";

// A small, closed vocabulary of question intents, derived from the question's own
// wording by plain patterns -- never by a model. The service stores the intent next
// to a remembered answer, and a later question with the same intent finds that answer
// even when it is worded differently or sits on another company's form ("Why do you
// want to work at Acme?" -> "Why do you want to work here?").
//
// Two rules keep a wrong reuse from being quiet:
//  - precision over recall. A label is classified only when it is, in effect, the whole
//    question: every pattern is anchored at both ends and its optional tails are a short,
//    closed list, so a label that adds a second ask, a topic ("work with Python") or a
//    long qualifier has no intent and can only ever be found by its exact wording.
//  - the vocabulary is the questions whose answer survives a change of employer.
//    Anything that depends on the specific job (salary, start date, a named skill) is
//    left out on purpose.
//
// The label is tenant-controlled text. It is length-capped before any pattern runs, and
// every pattern is a bounded phrase with no nested quantifier, so a hostile label cannot
// make the scan slow.
//
// Work-eligibility questions (work authorization, visa sponsorship) are the exception to
// "survives a change of employer": the answer depends on the country the question is
// about. Such an answer is remembered, and reused, only together with that country -- the
// country the question names ("...to work in Germany"), else the one the job's posting
// names -- and never when neither is known.

export type QuestionIntent =
  | "why_this_company"
  | "tell_us_about_yourself"
  | "how_did_you_hear"
  | "willing_to_relocate"
  | "work_authorization"
  | "visa_sponsorship";

/** An answer to these depends on which country's rules apply, so it is only remembered, and
 * only reused, together with a country. */
const JURISDICTION_SENSITIVE: ReadonlySet<QuestionIntent> = new Set(["work_authorization", "visa_sponsorship"]);

export function isJurisdictionSensitive(intent: QuestionIntent): boolean {
  return JURISDICTION_SENSITIVE.has(intent);
}

// Long enough for any real single question; anything longer is a paragraph, not a question.
const MAX_INTENT_TEXT_LENGTH = 160;

// NFKC folds full-width and other compatibility forms ("Ｗｈｙ ... ａｕｔｈｏｒｉｚｅｄ") to their plain
// letters, so a look-alike spelling classifies the same as the ordinary one.
function nfkc(label: string): string {
  return label.normalize("NFKC");
}

function normalizeForIntent(label: string): string {
  return normalizeQuestionLabel(nfkc(label))
    .replace(/[‘’]/g, "'")
    .replace(/[?!.:\s]+$/u, "")
    .trim();
}

// ---- pattern pieces ---------------------------------------------------------------

const LEAD_IN = String.raw`(?:please |briefly |in a few sentences,? )?(?:(?:tell us|explain|describe|can you tell us|could you tell us) )?`;

// "here", "us", "this company": the company whose form this is, said without naming it.
const SELF = String.raw`(?:here|us|this (?:company|organi[sz]ation|team)|our (?:company|organi[sz]ation|team)|the (?:company|organi[sz]ation))`;

// A name of one to four words that is not an article or a determiner ("a startup", "the right
// team" are not companies). Nothing here can tell "Acme" from "startups"; see NAMED_COMPANY_CASED.
const NAME = String.raw`(?!(?:a|an|the|any|my|your|this|that|our|these|those)\b)[a-z0-9][a-z0-9&.'-]*(?: [a-z0-9][a-z0-9&.'-]*){0,3}`;

// "work with" is only ever "work with us": "work with Python" is not a company.
const WORK_VERB_SELF = String.raw`(?:work (?:at|for|with)|working (?:at|for|with)|join|joining|apply (?:to|at)|applying (?:to|at))`;
const WORK_VERB_NAMED = String.raw`(?:work (?:at|for)|working (?:at|for)|join|joining|apply (?:to|at)|applying (?:to|at))`;

const WHY_INTRO = String.raw`(?:do you want to|do you wish to|would you like to|you want to|you would like to|did you (?:choose|decide) to|are you (?:interested in|excited about|drawn to))`;
const INTEREST = String.raw`(?:interests|excites|attracts|draws|appeals to)`;

const CLAUSES: Partial<Record<QuestionIntent, string[]>> = {
  why_this_company: [
    String.raw`why ${WHY_INTRO} ${WORK_VERB_SELF} ${SELF}`,
    String.raw`why (?:do you want to|would you like to|you want to|you would like to|are you interested in) (?:work|working) here`,
    String.raw`why (?:us|here|this company|our company)`,
    String.raw`what ${INTEREST} you (?:most )?(?:about|to) (?:${WORK_VERB_SELF} )?${SELF}`,
  ],
  tell_us_about_yourself: [
    String.raw`(?:tell us|tell me|can you tell us|could you tell us|describe|introduce) (?:a (?:little|bit|little bit) )?(?:about )?(?:yourself|you)(?: (?:briefly|in a few sentences))?`,
    String.raw`a (?:brief|short) (?:introduction|bio)(?: of yourself)?`,
  ],
  how_did_you_hear: [
    String.raw`(?:how|where) did you (?:first )?(?:hear|find out|learn) (?:about|of) (?:us|this (?:job|role|position|opening|opportunity|company)|the (?:job|role|position|opening|company))`,
  ],
  // Only the bare question. "...relocate to New York" names a destination, which is a
  // different question with its own answer.
  willing_to_relocate: [
    String.raw`(?:are you |would you be )?(?:willing|open|able|prepared) to relocate`,
    String.raw`(?:are you open to|would you consider) relocat(?:ion|ing)`,
  ],
};

const INTENT_PATTERNS: Array<[QuestionIntent, RegExp]> = (
  Object.entries(CLAUSES) as Array<[QuestionIntent, string[]]>
).map(([intent, clauses]) => [intent, new RegExp(`^${LEAD_IN}(?:${clauses.join("|")})$`, "u")]);

// A company called by name: only after a work verb, and only when the name is written like a
// name (an upper-case letter or a digit) in the label as the tenant wrote it. This is what keeps
// "work for startups" and "work at night" from classifying; a capitalised phrase still does.
const WHY_NAMED_PATTERN = new RegExp(
  `^${LEAD_IN}(?:why ${WHY_INTRO} ${WORK_VERB_NAMED} ${NAME}|what ${INTEREST} you (?:most )?about ${WORK_VERB_NAMED} ${NAME})$`,
  "u",
);
const NAMED_COMPANY_CASED = /\b(?:work(?:ing)? (?:at|for)|join(?:ing)?|apply(?:ing)? (?:to|at)) [A-Z0-9]/u;

// ---- work eligibility: a closed grammar, each with a place that has to be a country -------

// The place a work-eligibility question is about. The slot is generic; whether what it caught is
// a country, or "the country where this job is located", is decided in code, never by a pattern
// that lets any phrase through.
const PLACE = String.raw`(?<place>[a-z][a-z .'&-]{0,60}?)`;

const WORK_ABLE = String.raw`(?:(?:legally|lawfully|currently|presently|now) )*(?:authori[sz]ed|eligible|permitted|entitled) to work`;
const WORK_TAIL = String.raw`(?: here| for us| for this (?:company|organi[sz]ation)| (?:in|within) (?:the )?${PLACE})?(?: without (?:any )?restrictions)?`;

const WORK_AUTH_PATTERNS: RegExp[] = [
  new RegExp(
    String.raw`^(?:are you|am i|will you be|please (?:confirm|state|indicate|specify) (?:that )?you are) ${WORK_ABLE}${WORK_TAIL}$`,
    "u",
  ),
  new RegExp(String.raw`^(?:do you have|will you have|have you got) the (?:legal )?right to work${WORK_TAIL}$`, "u"),
  new RegExp(
    String.raw`^(?:(?:what is|what's|please (?:state|indicate|specify|confirm)) your (?:current )?)?(?:work|employment) authori[sz]ation(?: status)?(?: (?:in|for) (?:the )?${PLACE})?$`,
    "u",
  ),
];

const SPONSOR_SUBJECT = String.raw`(?:will you|do you|would you|are you going to)`;
const SPONSOR_WHEN = String.raw`(?:(?:now|currently|ever)(?:,? or (?:will you )?in the future)?|in the future),? `;
const SPONSOR_OBJECT = String.raw`(?:(?:an? )?(?:(?:employment|work|immigration) )?(?:visa )?sponsorship|(?:an? )?(?:(?:employment|work) )?visa)`;
const SPONSOR_TAIL =
  String.raw`(?: (?:now or in the future|in the future))?` +
  String.raw`(?: for (?:an? )?(?:(?:employment|work) )?visa(?: status)?(?: \([^()?]{1,40}\))?)?` +
  String.raw`(?: to work (?:here|for us|for this (?:company|organi[sz]ation)))?` +
  String.raw`(?: (?:in|within|to work in) (?:the )?${PLACE})?` +
  String.raw`(?: (?:now or in the future|in the future))?`;

const SPONSORSHIP_PATTERNS: RegExp[] = [
  new RegExp(String.raw`^${SPONSOR_SUBJECT} (?:${SPONSOR_WHEN})?(?:require|need) ${SPONSOR_OBJECT}${SPONSOR_TAIL}$`, "u"),
  new RegExp(String.raw`^(?:is|will) (?:(?:visa|immigration|employment) )?sponsorship (?:be )?(?:required|needed)$`, "u"),
  new RegExp(String.raw`^(?:(?:visa|immigration|employment) )?sponsorship(?: (?:required|needed|status))?$`, "u"),
];

// "this country", "the country where this job is located": the job's own country, said without
// naming it. It names no country of its own, so the job's posting supplies it.
const THIS_JOBS_COUNTRY =
  /^(?:this country|country where (?:this|the) (?:job|role|position|office) is (?:located|based)|location of (?:this|the) (?:job|role|position))$/u;

type Placed = { country: string | null };

/** The first pattern of `patterns` that matches `text` with a place that is a country (or none, or
 * the job's own), and the country it names, if it names one. */
function matchPlaced(patterns: RegExp[], text: string): Placed | null {
  for (const pattern of patterns) {
    const match = pattern.exec(text);
    if (match === null) continue;
    const place = match.groups?.place;
    if (place === undefined) return { country: null };
    if (THIS_JOBS_COUNTRY.test(place)) return { country: null };
    const country = regionCodeOfNamedCountry(place);
    if (country !== null) return { country };
  }
  return null;
}

// ---- the classifier ---------------------------------------------------------------

export interface QuestionAnalysis {
  intent: QuestionIntent;
  /** For a work-eligibility question that names exactly one country, that country (ISO code);
   * null otherwise. */
  namedCountry: string | null;
}

/**
 * The intent of a question label, or null when it has none the vocabulary recognises -- the usual
 * case. Null is not "no answer": the question is simply matched by its exact wording only. A
 * label that fits two intents ("Are you authorized to work here and will you require
 * sponsorship?") is null too: one stored answer cannot stand for both.
 */
export function analyzeQuestion(label: string): QuestionAnalysis | null {
  const text = normalizeForIntent(label);
  if (text === "" || text.length > MAX_INTENT_TEXT_LENGTH) return null;
  const hits: QuestionAnalysis[] = [];
  for (const [intent, pattern] of INTENT_PATTERNS) {
    if (pattern.test(text)) hits.push({ intent, namedCountry: null });
  }
  // The company-by-name form of "why this company" is one hit with the closed forms, not two.
  if (!hits.some((h) => h.intent === "why_this_company") && WHY_NAMED_PATTERN.test(text) && NAMED_COMPANY_CASED.test(nfkc(label))) {
    hits.push({ intent: "why_this_company", namedCountry: null });
  }
  const workAuth = matchPlaced(WORK_AUTH_PATTERNS, text);
  if (workAuth !== null) hits.push({ intent: "work_authorization", namedCountry: workAuth.country });
  const sponsorship = matchPlaced(SPONSORSHIP_PATTERNS, text);
  if (sponsorship !== null) hits.push({ intent: "visa_sponsorship", namedCountry: sponsorship.country });
  return hits.length === 1 ? (hits[0] ?? null) : null;
}

export function classifyQuestionIntent(label: string): QuestionIntent | null {
  return analyzeQuestion(label)?.intent ?? null;
}

// A label that is about work eligibility but is not, in effect, the whole of one of the questions
// above (a compound label, an unusual wording): it has no intent, but its answer is still tied to a
// country. Deliberately over-inclusive -- a false positive only means an answer is kept together
// with a country, or not kept when there is none.
// ("eligible to work" and "permitted to work" are left out: "...eligible to work the overnight
// shift" is about a rota, not a right.)
const WORK_ELIGIBILITY_WORDS =
  /\bauthori[sz]ed to work\b|\bright to work\b|\bwork authori[sz]ation\b|\bwork permit\b|\bsponsorship\b|\bvisa\b|\bimmigration\b/u;
const MAX_LOOSE_SCAN_LENGTH = 5000;

function looksLikeWorkEligibility(label: string): boolean {
  return WORK_ELIGIBILITY_WORDS.test(normalizeForIntent(label.slice(0, MAX_LOOSE_SCAN_LENGTH)));
}

/** True for a question whose answer is tied to a country: a recognised work-eligibility question,
 * or any label that is not a recognised question of another kind but talks about working
 * authorization, sponsorship or a visa. */
export function isEligibilityQuestion(label: string): boolean {
  const analysis = analyzeQuestion(label);
  return analysis === null ? looksLikeWorkEligibility(label) : isJurisdictionSensitive(analysis.intent);
}

/** A country code the service understands: the two upper-case letters of ISO 3166-1. */
export function asJurisdiction(value: unknown): string | null {
  return typeof value === "string" && /^[A-Z]{2}$/.test(value) ? value : null;
}

export interface MemoryTags {
  canonicalIntent?: string;
  jurisdiction?: string;
}

/**
 * What to send when saving an answer, or null for "do not remember this one".
 *
 * A work-eligibility answer is tagged with a country: the one the question names, else the job's.
 * When neither is known it is not remembered at all -- an answer saved without a country would
 * count as true everywhere, which it is not, and "the posting does not say" is not a country.
 * Every other answer is universal: its intent is sent and no jurisdiction, so it is offered on
 * any company's form.
 */
export function memoryTagsForSave(label: string, jobJurisdiction: string | null): MemoryTags | null {
  const analysis = analyzeQuestion(label);
  const intentTag = analysis === null ? {} : { canonicalIntent: analysis.intent };
  const eligibility = analysis === null ? looksLikeWorkEligibility(label) : isJurisdictionSensitive(analysis.intent);
  if (!eligibility) return intentTag;
  const country = analysis?.namedCountry ?? jobJurisdiction;
  if (country === null) return null;
  return { ...intentTag, jurisdiction: country };
}

/**
 * What to send when looking an answer up: the intent when there is one, and the country that
 * applies. For a work-eligibility question that is the country the question names, else the
 * job's; with neither known, nothing but the exact wording is asked for (no intent), so no answer
 * can be found by alias under a country that was never established. For every other question the
 * job's country is sent whenever it is known: the service shows a tagged answer only for the same
 * country and an untagged one for any, so sending it never hides an answer that applies.
 */
export function memoryTagsForLookup(label: string, jobJurisdiction: string | null): MemoryTags {
  const analysis = analyzeQuestion(label);
  const intentTag = analysis === null ? {} : { canonicalIntent: analysis.intent };
  const eligibility = analysis === null ? looksLikeWorkEligibility(label) : isJurisdictionSensitive(analysis.intent);
  if (!eligibility) {
    return { ...intentTag, ...(jobJurisdiction === null ? {} : { jurisdiction: jobJurisdiction }) };
  }
  const country = analysis?.namedCountry ?? jobJurisdiction;
  return country === null ? {} : { ...intentTag, jurisdiction: country };
}
