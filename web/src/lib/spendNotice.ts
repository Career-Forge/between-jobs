// The one sentence this app uses to say, before a control is pressed, what pressing it costs the person.
//
//   Pressing Check coverage makes a real model call on your own key.
//
// Every model call here is paid for with the person's own key, so every control that makes one says so
// next to the control, in the same words. The words live in this one module (not typed out at each
// control) so a page intro, a note under a button and a row in a band can never drift into three slightly
// different promises, and so a new control gets the same shape by calling the helper rather than by
// remembering it.
//
// Three rules the callers keep, and the tests pin:
//
//   - Say how many. A press that makes one call says "a real model call"; a press that can make several
//     says SEVERAL_MODEL_CALLS, or writes its own count in `what` ("up to two ...", "a second one if ...").
//     Never the singular for a press that can make more.
//   - A press that spends something other than a model call names that thing. A search-key press that
//     makes no model call says "search call", never "model call", and a `what` may go on after the call to
//     say what else is used ("... on your own key and can run several searches on each search key you
//     have connected").
//   - Plain text only, no dashes, no trailing period in `what` (the helper adds it).

/** One real model call, made with the person's own key. */
export const A_MODEL_CALL = "a real model call on your own key";

/** More than one real model call in a single press, made with the person's own key. */
export const SEVERAL_MODEL_CALLS = "several real model calls on your own key";

/**
 * What ticking "Also generate a cover letter" adds to a Generate press: one more call for the letter, and a
 * second if the first letter fails its checks. Written as a label's parenthesis, not a sentence.
 */
export const COVER_LETTER_EXTRA = "a real, extra model call, and a second if the letter fails its checks";

/**
 * The word a notice opens with. A button is "Pressing" it; the one control that starts its work the
 * moment a file is picked, with no button to press afterwards, is "Choosing" the file.
 */
export type SpendAction = "Pressing" | "Choosing";

/**
 * "Pressing <press> makes <what>." -- `press` is the control's own label as the person reads it
 * ("Check coverage"), `what` is the spend ({@link A_MODEL_CALL}, {@link SEVERAL_MODEL_CALLS}, or one of
 * those followed by more words) without a closing period. `action` is "Pressing" unless the work starts
 * on a choice rather than a press ("Choosing a file makes ...").
 */
export function spendNotice(press: string, what: string, action: SpendAction = "Pressing"): string {
  return `${action} ${press} makes ${what}.`;
}
