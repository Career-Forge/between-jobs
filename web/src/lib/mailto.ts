// A `mailto:` link for an outreach draft, for the person who cannot (or has not yet) let the app
// create the draft in Gmail for them: it opens their own mail app with the subject and body filled
// in, and they press Send themselves. Nothing here sends anything.
//
// Pure module, no React.
//
// WHAT IS GUARDED.
//   - The recipient is only ever the address the person already has for this contact (the work
//     email they chose to look up), and only if it looks like one: no comma, semicolon, query
//     character or quote that would add a second recipient or a header. Anything else is no
//     recipient at all, never a repaired guess, and the mail app asks the person who to write to.
//   - The subject and the body are percent-encoded whole, so nothing in them (an `&cc=` typed
//     into a draft, a `?`, a `%`) is read as part of the link's own syntax. The subject is a
//     single line. Line breaks in the body are CRLF, which is what RFC 6068 asks for.
//   - There is a length ceiling. Mail apps and the operating system hand-off do not take long
//     links (Outlook and older Windows handlers stop around 2,000 characters), and one that
//     silently cuts the body would leave the person to send half an email. Past the ceiling there
//     is no link and the reason is "too_long": the caller says to copy the text instead.

export const MAX_MAILTO_LENGTH = 1800;

// A plain address: letters, digits and . _ + ' - in the part before the @, a dotted host after it.
// Deliberately narrower than the RFC (no quoted local parts, no %, no comment syntax).
const PLAIN_ADDRESS = /^[A-Za-z0-9._+'-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+$/;

export interface MailtoDraft {
  to: string | null;
  subject: string;
  body: string;
}

export type MailtoLink = { href: string } | { href: null; reason: "too_long" };

// Control characters other than the line break and the tab, which a mail app has no use for and
// which could confuse the hand-off.
const CONTROL_CHARACTERS = /[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f-\u009f]/g;

function oneLine(text: string): string {
  return text.replace(CONTROL_CHARACTERS, "").replace(/\s+/g, " ").trim();
}

function bodyText(text: string): string {
  return text.replace(CONTROL_CHARACTERS, "").replace(/\r\n|\r|\n/g, "\r\n");
}

/** The address as it may go into a link, or null when it is not a plain address. */
export function mailtoRecipient(address: string | null | undefined): string | null {
  if (typeof address !== "string") return null;
  const trimmed = address.trim();
  return PLAIN_ADDRESS.test(trimmed) ? trimmed : null;
}

export function buildMailto(draft: MailtoDraft): MailtoLink {
  const to = mailtoRecipient(draft.to);
  const recipient = to === null ? "" : encodeURIComponent(to).replace(/%40/g, "@");
  const href =
    `mailto:${recipient}` +
    `?subject=${encodeURIComponent(oneLine(draft.subject))}` +
    `&body=${encodeURIComponent(bodyText(draft.body))}`;
  return href.length > MAX_MAILTO_LENGTH ? { href: null, reason: "too_long" } : { href };
}

/** The subject and the body as one block of text to paste into any mail app. */
export function subjectAndBody(draft: Pick<MailtoDraft, "subject" | "body">): string {
  return `Subject: ${oneLine(draft.subject)}\n\n${draft.body}`;
}
