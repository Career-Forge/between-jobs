// What the public pages' words may never say, shared by the tests of the home page (pages/Landing.test.tsx)
// and of the two explainer pages (content/publicPages.test.ts), so one list guards all three.

export function strings(value: unknown): string[] {
  if (typeof value === "string") return [value];
  if (Array.isArray(value)) return value.flatMap(strings);
  if (typeof value === "object" && value !== null) return Object.values(value).flatMap(strings);
  return [];
}

// Words a page like this reaches for and this product cannot back. Each is a claim about results, scale,
// proof or speed; none is built, measured or true of what the code does today.
export const FORBIDDEN: [string, RegExp][] = [
  ["verified (the product says 'checked', and never calls a link or a claim verified)", /\bverified\b/i],
  ["guarantee", /guarant/i],
  ["certified", /\bcertified\b/i],
  ["proven", /\bproven\b/i],
  ["superlatives", /\b(?:best|fastest|leading|world-class|revolutionary|cutting-edge|game-chang\w*|unrivall?ed|#1|number one)\b/i],
  ["marketing about AI", /\bai-powered\b|\bsupercharge\w*|\b10x\b|\bmagic\w*/i],
  ["scale and popularity", /\b(?:thousands?|millions?|hundreds)\b|\btrusted by\b|\bloved by\b|\bjoin\b[^.]*\b(?:users|people|job seekers)\b/i],
  ["a testimonial", /testimonial|\bcustomers? (?:say|said|love)\b|[“"][^”"]{10,}[”"]\s*(?:--|-|\u2014)\s*[A-Z]/],
  ["results", /\bget hired\b|\bland(?:ed)? (?:a|the|your) (?:job|offer)\b|\bhours? saved\b|\bsave[sd]? you\b|\bhigher response\b|\bmore interviews\b/i],
  ["automation the product refuses", /\bauto-?appl\w*|\bone-click appl\w*|\bapplies for you\b|\bsubmits for you\b|\bsends for you\b/i],
  ["a platform the extension does not support", /\bworkday\b|\bicims\b|\blinkedin easy apply\b/i],
  ["the product called a studio (it is always 'Between Jobs')", /\bstudio\b/i],
  ["a promise that every block or piece of work carries a provenance line, which only some pages draw", /every (?:block|result|piece|surface)\b[^.]*\b(?:trail|came from)|where each piece came from/i],
  ["every result rated, when only a batch is and none is without a model key", /\b(?:each|every) (?:result|listing)\b[^.]*\b(?:rated|scored|fits)\b/i],
  ["the brand dot, which is a mark and never copy", /\bred dot\b/i],
];
