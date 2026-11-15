import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { anchorsOf, tagsOf, textOfMarkup } from "../testing/markup";
import { expand, findAll, type HostElement, isHost, press, prop, textOf } from "../testing/reactTree";
import { GMAIL_DRAFT_NOTE, GmailDraftActions, type GmailDraftActionsProps } from "./GmailDraftActions";

// What sits under an outreach draft for each state Gmail can be in (lib/gmailAccess.ts decides the
// state; the contact panel owns the requests). Nothing here sends mail.

const SUBJECT = "Loved your PyData talk";
const BODY = "Hi Jane,\n\nWould love to chat about the platform team.\n\nBest,\nA";
const RECIPIENT = "jane.doe@acme.example";

function props(extra: Partial<GmailDraftActionsProps>): GmailDraftActionsProps {
  return {
    state: "connected",
    unavailableMessage: null,
    alreadyPushed: false,
    recipient: RECIPIENT,
    subject: SUBJECT,
    body: BODY,
    pushing: false,
    copied: false,
    copyFailed: false,
    onPush: () => undefined,
    onCopyAll: () => undefined,
    ...extra,
  };
}

function html(extra: Partial<GmailDraftActionsProps>): string {
  return renderToStaticMarkup(
    <MemoryRouter>
      <GmailDraftActions {...props(extra)} />
    </MemoryRouter>,
  );
}

const MAIL_APP = "Open in your mail app";
const COPY = "Copy subject and email";
// The sentence beside the button, word for word: the press makes no model call, and what can come later does.
const NOTE =
  "Pressing Create Gmail draft puts the draft in your Gmail and makes no model call. Once you send it, a reply to it can be read by your model on your own key, then show up in Today as an update or, when the model is confident of it, move this application's stage.";
const EM_OR_EN_DASH = new RegExp(`[${String.fromCharCode(0x2013)}${String.fromCharCode(0x2014)}]`);

// The one row the button and its note sit in, read from what the component returns.
function actionsRow(extra: Partial<GmailDraftActionsProps>) {
  const [row] = findAll(expand(GmailDraftActions(props(extra))), (el) => el.type === "div" && prop(el, "className") === "bj-actions");
  return row;
}

describe("a draft already in Gmail", () => {
  it("says so, whatever the state, and offers nothing else", () => {
    for (const state of [
      "connected",
      "unknown",
      "availability_unknown",
      "not_connected",
      "reconnect_needed",
      "unavailable",
    ] as const) {
      const text = textOfMarkup(html({ state, alreadyPushed: true }));
      expect(text).toBe("Draft created in your Gmail");
    }
  });
});

describe("with Gmail connected, or not known yet", () => {
  it.each(["connected", "unknown"] as const)("(%s) offers to create the draft once the contact has a work email", (state) => {
    const markup = html({ state });
    expect(textOfMarkup(markup)).toBe(`Create Gmail draft${NOTE}`);
    expect(markup).not.toContain("bj-primary");
    expect(markup).not.toContain("mailto:");
    expect(textOfMarkup(markup)).not.toContain(COPY);
  });

  it.each(["connected", "unknown"] as const)("(%s) asks for a work email first when there is none", (state) => {
    const markup = html({ state, recipient: null });
    expect(textOfMarkup(markup)).toBe("Find this contact's work email to create a Gmail draft.");
    expect(markup).not.toContain("<button");
  });

  it("says Creating draft... and marks the button aria-disabled (not disabled, so focus stays) while the request is out", () => {
    const markup = html({ pushing: true });
    expect(textOfMarkup(markup)).toBe(`Creating draft...${NOTE}`);
    expect(tagsOf(markup, "button")[0]).toHaveProperty("aria-disabled", "true");
    expect(tagsOf(markup, "button")[0]).not.toHaveProperty("disabled");
  });

  it("ignores a press while the request is out", () => {
    const onPush = vi.fn();
    const nodes = expand(GmailDraftActions(props({ onPush, pushing: true })));
    press(findAll(nodes, (el) => el.type === "button")[0]);
    expect(onPush).not.toHaveBeenCalled();
  });

  it("creates the draft when the button is pressed, and only then", () => {
    const onPush = vi.fn();
    const nodes = expand(GmailDraftActions(props({ onPush })));
    const buttons = findAll(nodes, (el) => el.type === "button");
    expect(buttons).toHaveLength(1);
    expect(onPush).not.toHaveBeenCalled();
    press(buttons[0]);
    expect(onPush).toHaveBeenCalledTimes(1);
  });
});

describe("what Create Gmail draft costs, said beside it", () => {
  it.each(["connected", "unknown"] as const)("(%s) says beside the button that the press makes no model call, and what can come later", (state) => {
    const row = actionsRow({ state });
    const [button, note] = row.children;
    expect(isHost(button) && button.type).toBe("button");
    expect(textOf(button)).toBe("Create Gmail draft");
    // The note follows the button in the same row, in the muted small style, and is the sentence word for word.
    expect(isHost(note) && note.type).toBe("span");
    expect(prop(note as HostElement, "className")).toBe("bj-muted bj-small");
    expect(textOf(note)).toBe(NOTE);
    expect(GMAIL_DRAFT_NOTE).toBe(NOTE);
  });

  it("stays beside the button while the draft is being created", () => {
    const row = actionsRow({ pushing: true });
    expect(textOf(row.children[0])).toBe("Creating draft...");
    expect(textOf(row.children[1])).toBe(NOTE);
  });

  it("says a press makes no model call and does not use the spend sentence for a press that has no spend", () => {
    expect(NOTE.startsWith("Pressing Create Gmail draft puts the draft in your Gmail and makes no model call.")).toBe(true);
    expect(NOTE).not.toMatch(/makes a real model call|several real model calls/);
    expect(NOTE).toContain("on your own key");
    expect(NOTE).not.toMatch(EM_OR_EN_DASH);
    expect(NOTE).not.toMatch(/verified|unknown model/i);
  });

  it("is not shown where there is no button: no work email yet, a draft already in Gmail, or no Gmail", () => {
    expect(textOfMarkup(html({ recipient: null }))).not.toContain("Pressing Create Gmail draft");
    expect(textOfMarkup(html({ alreadyPushed: true }))).not.toContain("Pressing Create Gmail draft");
    for (const state of ["not_connected", "reconnect_needed", "availability_unknown", "unavailable"] as const) {
      expect(textOfMarkup(html({ state }))).not.toContain("Pressing Create Gmail draft");
    }
  });
});

describe("without Gmail", () => {
  const states = [
    { state: "not_connected", unavailableMessage: null },
    { state: "reconnect_needed", unavailableMessage: null },
    { state: "availability_unknown", unavailableMessage: null },
    { state: "unavailable", unavailableMessage: "Gmail isn't available for your account yet." },
    { state: "unavailable", unavailableMessage: "Gmail isn't set up on this server." },
  ] as const;

  it.each(states)("($state, $unavailableMessage) never offers to create a Gmail draft", (extra) => {
    const markup = html({ ...extra });
    expect(textOfMarkup(markup)).not.toContain("Create Gmail draft");
    expect(markup).not.toContain("bj-primary\">Create");
  });

  it.each(states)("($state, $unavailableMessage) opens the draft in the person's mail app and copies it", (extra) => {
    const markup = html({ ...extra });
    const [mail] = anchorsOf(markup).filter((a) => a.text === MAIL_APP);
    expect(mail).toBeDefined();
    expect(mail.attributes.href).toMatch(/^mailto:jane\.doe@acme\.example\?subject=/);
    expect(textOfMarkup(markup)).toContain(COPY);
  });

  it("fills the mail app with the subject and the email, and the contact's address only", () => {
    const [mail] = anchorsOf(html({ state: "not_connected" })).filter((a) => a.text === MAIL_APP);
    const link = mail.attributes.href.replace(/&amp;/g, "&");
    const query = new URLSearchParams(link.split("?")[1]);
    expect(link.startsWith("mailto:jane.doe@acme.example?")).toBe(true);
    expect(query.get("subject")).toBe(SUBJECT);
    expect(query.get("body")).toBe(BODY.replace(/\n/g, "\r\n"));
    expect([...query.keys()]).toEqual(["subject", "body"]);
  });

  it("leaves the recipient empty when the person has not looked up a work email", () => {
    const [mail] = anchorsOf(html({ state: "not_connected", recipient: null })).filter((a) => a.text === MAIL_APP);
    expect(mail.attributes.href.startsWith("mailto:?subject=")).toBe(true);
  });

  it("does not put a recipient in the link that is not a plain address", () => {
    const [mail] = anchorsOf(html({ state: "not_connected", recipient: "a@b.example,evil@x.example" })).filter(
      (a) => a.text === MAIL_APP,
    );
    expect(mail.attributes.href.startsWith("mailto:?subject=")).toBe(true);
    expect(mail.attributes.href).not.toContain("evil");
  });

  it("says when the draft is too long for a mail app, and does not cut it", () => {
    const markup = html({ state: "not_connected", body: "word ".repeat(1000) });
    expect(anchorsOf(markup).filter((a) => a.text === MAIL_APP)).toHaveLength(0);
    expect(markup).not.toContain("mailto:");
    const text = textOfMarkup(markup);
    expect(text).toContain("too long to open in a mail app");
    expect(text).toContain(COPY);
  });

  it("says when copying did not work, and where to find the text instead", () => {
    const failed = textOfMarkup(html({ state: "not_connected", copyFailed: true }));
    expect(failed).toContain("Couldn't copy automatically. Select the email above and copy it by hand.");
    expect(failed).toContain(COPY); // the button stays, so they can try again
    expect(textOfMarkup(html({ state: "not_connected" }))).not.toContain("Couldn't copy");
    expect(textOfMarkup(html({ state: "not_connected", copied: true }))).not.toContain("Couldn't copy");
  });

  it("says so too when the draft is too long for a mail app, where copying is the only way", () => {
    const text = textOfMarkup(html({ state: "not_connected", body: "word ".repeat(1000), copyFailed: true }));
    expect(text).toContain("too long to open in a mail app");
    expect(text).toContain("Couldn't copy automatically.");
  });

  it("copies the subject and the email when asked, and says it has", () => {
    const onCopyAll = vi.fn();
    const nodes = expand(GmailDraftActions(props({ state: "not_connected", onCopyAll })));
    const [copy] = findAll(nodes, (el) => el.type === "button");
    expect(textOf(copy)).toBe(COPY);
    press(copy);
    expect(onCopyAll).toHaveBeenCalledTimes(1);

    const done = expand(GmailDraftActions(props({ state: "not_connected", copied: true })));
    expect(textOf(findAll(done, (el) => el.type === "button")[0])).toBe("Copied!");
    expect(prop(findAll(done, (el) => el.type === "button")[0], "disabled")).toBeUndefined();
  });
});

describe("what it says, and where it sends people", () => {
  it("not connected: points at Settings to connect", () => {
    const markup = html({ state: "not_connected" });
    expect(textOfMarkup(markup)).toContain("Connect Gmail to have drafts created for you.");
    const link = anchorsOf(markup).find((a) => a.text === "Connect Gmail in Settings");
    expect(link?.attributes.href).toBe("/settings");
  });

  it("needs a reconnect: says so and points at Settings to reconnect", () => {
    const markup = html({ state: "reconnect_needed" });
    expect(textOfMarkup(markup)).toContain("Your Gmail connection needs to be reconnected before it can create drafts.");
    const link = anchorsOf(markup).find((a) => a.text === "Reconnect Gmail in Settings");
    expect(link?.attributes.href).toBe("/settings");
  });

  it("availability not known: says it cannot tell, and sends nobody to connect a Gmail that may not be theirs", () => {
    const markup = html({ state: "availability_unknown" });
    const text = textOfMarkup(markup);
    expect(text).toContain("We can't tell whether Gmail is available for your account right now.");
    expect(text).toContain("You can still copy this email or open it in your mail app.");
    expect(text).not.toContain("Create Gmail draft");
    expect(text).not.toMatch(/connect gmail|reconnect gmail/i);
    expect(anchorsOf(markup).some((a) => a.attributes.href === "/settings")).toBe(false);
    expect(markup).not.toContain("bj-primary");
    expect(anchorsOf(markup).some((a) => a.text === MAIL_APP)).toBe(true);
    expect(text).toContain(COPY);
  });

  it("not available: gives the reason and no way to connect a Gmail that cannot be had", () => {
    for (const message of ["Gmail isn't available for your account yet.", "Gmail isn't set up on this server."]) {
      const markup = html({ state: "unavailable", unavailableMessage: message });
      const text = textOfMarkup(markup);
      expect(text).toContain(message);
      expect(text).toContain("You can still copy this email or open it in your mail app.");
      expect(anchorsOf(markup).some((a) => a.attributes.href === "/settings")).toBe(false);
      expect(text).not.toMatch(/connect gmail|reconnect gmail/i);
    }
  });

  it("not available without a reason on hand: still says it plainly", () => {
    const text = textOfMarkup(html({ state: "unavailable", unavailableMessage: null }));
    expect(text).toContain("Gmail isn't available for you.");
  });
});
