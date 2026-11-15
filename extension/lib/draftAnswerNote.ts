// What the side panel says beside "Draft answer", and beside "Try again" after a draft that failed or
// was declined, about what pressing it costs. Both buttons start the same thing: a saved answer to the
// same question is looked for first, and only when there is none does the service ask the person's own
// model, once to draft the answer and once more to check the claims in it. The second call is made only
// when a draft came back, and a question the service will not draft costs nothing, which is why the
// sentence says "up to two". The words live here, not in the panel, so the two buttons cannot drift
// apart and a test can pin them without rendering the panel.
//
// This extension is a separate package from the web app and does not import from it, so the sentence is
// written out here in the same shape the web app uses: "Pressing X makes ... on your own key."
// Plain text, no dashes.

export type DraftAnswerButton = "Draft answer" | "Try again";

export function draftAnswerNote(press: DraftAnswerButton): string {
  return `Pressing ${press} makes up to two real model calls on your own key, one to draft the answer and one to check the claims in it, unless you already saved an answer to this question.`;
}
