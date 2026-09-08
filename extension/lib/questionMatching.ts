// browser-extension.md's "known-question memory" design: DOM research
// found none of Greenhouse/Lever/Ashby expose a screening question's
// MEANING in a durable key, so the real memory key is the rendered
// label's own normalized text -- this is the one function that has to
// produce the SAME normalized form on both the matching path (asking
// "have I answered this before?") and the saving path (so a later
// exact-question match actually hits), or the whole tier-1 match tier
// silently never fires.
export function normalizeQuestionLabel(label: string): string {
  return label
    .trim()
    .toLowerCase()
    .replace(/[✱*]+\s*$/, "") // Lever's own required-field marker, confirmed live
    .replace(/\s+/g, " ")
    .trim();
}
