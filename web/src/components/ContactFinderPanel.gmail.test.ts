import { describe, expect, it } from "vitest";
import { codeBetweenCatchAndCall, sliceBetween } from "../testing/sourcePins";
import panel from "./ContactFinderPanel.tsx?raw";

// The contact panel is stateful and fetches, so its Gmail behaviour is pinned two ways: the view
// under a draft is rendered directly (GmailDraftActions.test.tsx) and the deciding is pure
// (lib/gmailAccess.test.ts, which also runs what a refused request teaches the screen); this reads
// the panel to check the two are wired in, that the old unconditional "Create Gmail draft" button
// is gone, and that the refusal handling and the copy fallback are where they must be.

describe("ContactFinderPanel and Gmail", () => {
  it("draws what sits under a draft with GmailDraftActions, from the person's own Gmail state", () => {
    expect(panel).toContain("<GmailDraftActions");
    expect(panel).toContain("gmailState(access, connection)");
    expect(panel).toContain("gmailAccess(useCapabilities())");
    expect(panel).toContain("subjectAndBody(");
  });

  it("no longer offers Create Gmail draft on its own, whatever state Gmail is in", () => {
    expect(panel).not.toContain("Create Gmail draft");
    expect(panel).not.toContain("Draft created in your Gmail");
  });

  it("asks for the connection only once Gmail is known to be open to the person", () => {
    // Not while it is not known (the offer waits for a definite yes), and not when it is a no.
    expect(panel).toContain('useGmailConnection(access === "available");');
  });

  it("learns from a refused request FIRST: a reconnect answer is remembered, a 'not available' one refreshes what is known", () => {
    // The reconnect answer is also a notice that the code after it shows and returns on, so a
    // lesson placed after that return is never learned and the panel keeps offering the button
    // that just failed. Pinned as the first statement of the catch, with nothing around it.
    // (What the helper does with each kind of refusal is run in lib/gmailAccess.test.ts.)
    const push = sliceBetween(panel, "async function pushToGmail", "function retry(");
    expect(push).not.toBeNull();
    const call =
      "learnFromGmailRefusal(e, { reconnectNeeded: markReconnectNeeded, unavailable: refreshCapabilities });";
    expect(codeBetweenCatchAndCall(push ?? "", call)).toBe("");
    expect(push?.indexOf("failureOf(e,")).toBeGreaterThan(push?.indexOf(call) ?? Infinity);
    // and the handlers it is given are the panel's own
    expect(panel).toContain("const { connection, markReconnectNeeded } = useGmailConnection(");
    expect(panel).toContain('import { refreshCapabilities, useCapabilities } from "../lib/useCapabilities";');
  });

  it("copies through the helper that cannot throw, and tells the person when the browser refused", () => {
    expect(panel).not.toContain("navigator.clipboard");
    const copy = sliceBetween(panel, "async function copyText", "async function pushToGmail");
    expect(copy).not.toBeNull();
    expect(copy).toContain("if (await copyToClipboard(text)) {");
    expect(copy).toContain("setCopyFailedField(field);");
    expect(copy).toContain("setTimeout(() => setCopyFailedField(null), COPY_FAILED_NOTICE_MS);");
    expect(panel).toContain('"Couldn\'t copy"');
  });

  it("hands the view exactly these facts about the draft, the contact and the request", () => {
    for (const line of [
      "state={gmail}",
      "unavailableMessage={gmailUnavailableMessage(access)}",
      "alreadyPushed={Boolean(draft.gmail_draft_id)}",
      "recipient={candidate.enriched_email}",
      "subject={draft.subject}",
      "body={draft.email_body}",
      'pushing={busy.has(key(candidate, "gmail"))}',
      "copied={copiedField === `${candidate.id}-all`}",
      "copyFailed={copyFailedField === `${candidate.id}-all`}",
      "onPush={() => void pushToGmail(candidate)}",
    ]) {
      expect(panel).toContain(line);
    }
    // and copies the subject and the email together, under the key the view reads "copied" from
    expect(panel).toContain("copyText(`${candidate.id}-all`, subjectAndBody({ subject: draft.subject, body: draft.email_body }))");
  });

  it("still shows the server's notice for the failed push (the link to Reconnect Gmail comes from it)", () => {
    // The failure goes through the setup helper (a reconnect answer is a notice with its link) and is drawn
    // with the component that draws the link, on the person's row.
    expect(panel).toContain('fail(candidate, "gmail", failureOf(e, "Couldn\'t create that Gmail draft."));');
    expect(panel).toContain("<SetupRequiredNotice notice={issue.problem.notice} />");
  });

  it("keeps the per-field Copy buttons", () => {
    expect(panel).toContain("Copy");
    expect(panel).toContain("Nothing is sent from here.");
  });
});
