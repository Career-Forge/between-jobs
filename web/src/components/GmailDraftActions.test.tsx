import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { anchorsOf, tagsOf, textOfMarkup } from "../testing/markup";
import { expand, findAll, press, prop, textOf } from "../testing/reactTree";
import { GmailDraftActions, type GmailDraftActionsProps } from "./GmailDraftActions";

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
    expect(textOfMarkup(markup)).toBe("Create Gmail draft");
    expect(markup).toContain("bj-primary");
    expect(markup).not.toContain("mailto:");
    expect(textOfMarkup(markup)).not.toContain(COPY);
  });

  it.each(["connected", "unknown"] as const)("(%s) asks for a work email first when there is none", (state) => {
    const markup = html({ state, recipient: null });
    expect(textOfMarkup(markup)).toBe("Find this contact's work email to create a Gmail draft.");
    expect(markup).not.toContain("<button");
  });

  it("says Creating draft... and disables the button while the request is out", () => {
    const markup = html({ pushing: true });
    expect(textOfMarkup(markup)).toBe("Creating draft...");
    expect(tagsOf(markup, "button")[0]).toHaveProperty("disabled");
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
  it("not connected: points at Integrations to connect", () => {
    const markup = html({ state: "not_connected" });
    expect(textOfMarkup(markup)).toContain("Connect Gmail to have drafts created for you.");
    const link = anchorsOf(markup).find((a) => a.text === "Connect Gmail in Integrations");
    expect(link?.attributes.href).toBe("/profile/integrations");
  });

  it("needs a reconnect: says so and points at Integrations to reconnect", () => {
    const markup = html({ state: "reconnect_needed" });
    expect(textOfMarkup(markup)).toContain("Your Gmail connection needs to be reconnected before it can create drafts.");
    const link = anchorsOf(markup).find((a) => a.text === "Reconnect Gmail in Integrations");
    expect(link?.attributes.href).toBe("/profile/integrations");
  });

  it("availability not known: says it cannot tell, and sends nobody to connect a Gmail that may not be theirs", () => {
    const markup = html({ state: "availability_unknown" });
    const text = textOfMarkup(markup);
    expect(text).toContain("We can't tell whether Gmail is available for your account right now.");
    expect(text).toContain("You can still copy this email or open it in your mail app.");
    expect(text).not.toContain("Create Gmail draft");
    expect(text).not.toMatch(/connect gmail|reconnect gmail/i);
    expect(anchorsOf(markup).some((a) => a.attributes.href === "/profile/integrations")).toBe(false);
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
      expect(anchorsOf(markup).some((a) => a.attributes.href === "/profile/integrations")).toBe(false);
      expect(text).not.toMatch(/connect gmail|reconnect gmail/i);
    }
  });

  it("not available without a reason on hand: still says it plainly", () => {
    const text = textOfMarkup(html({ state: "unavailable", unavailableMessage: null }));
    expect(text).toContain("Gmail isn't available for you.");
  });
});
