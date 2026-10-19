// @vitest-environment jsdom
import { useState } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { FakeApiError, on, reset } from "../../testing/fakeApi";
import { settle } from "../../testing/forms";
import { buttonNamed, click, query, render } from "../../testing/render";
import { DiscordLinkCard, GmailConnectCard, TelegramLinkCard } from "./ConnectionCards";
import type { CredentialSummary } from "../../lib/settings/credentials";

vi.mock("../../lib/api", async () => (await import("../../testing/fakeApi")).apiModule);
vi.mock("../../lib/useCapabilities", () => ({ refreshCapabilities: () => undefined }));

// A button that is busy keeps keyboard focus (it is aria-disabled, never disabled: a disabled button drops
// focus to the top of the page), and when a disconnect removes the button the keyboard was on, focus goes to
// what the card offers next.

const GMAIL: CredentialSummary = { id: "g", service: "oauth", provider: "gmail", model: null, is_validated: true, updated_at: "2026-10-12T10:00:00Z", scope: "https://www.googleapis.com/auth/gmail.readonly" };

function GmailCard({ onDisconnected }: { onDisconnected: () => void }) {
  const [credential, setCredential] = useState<CredentialSummary | null>(GMAIL);
  return (
    <GmailConnectCard
      credential={credential}
      access="available"
      testingMode={false}
      onChanged={async () => {
        setCredential(null);
        onDisconnected();
      }}
    />
  );
}

beforeEach(() => reset());

describe.each(["Telegram", "Discord"] as const)("the %s link-code button", (name) => {
  const card = () => render(name === "Telegram" ? <TelegramLinkCard botUsername={null} /> : <DiscordLinkCard installUrl={null} />);

  it("stays focused and aria-disabled while the code is asked for, and ignores a second press", async () => {
    let calls = 0;
    on("POST /link/code", () => {
      calls += 1;
      return new Promise(() => undefined);
    });
    const { container } = card();
    const button = buttonNamed(container, "Generate a code");
    button.focus();
    click(button);
    await settle(1);
    expect(button.disabled).toBe(false);
    expect(button.getAttribute("aria-disabled")).toBe("true");
    expect(button.textContent).toBe("Generating...");
    expect(document.activeElement).toBe(button);
    click(button);
    expect(calls).toBe(1);
  });

  it("keeps focus on the button once the code has arrived", async () => {
    on("POST /link/code", { code: "ABC123", expires_at: "2026-10-19T13:00:00Z" });
    const { container } = card();
    const button = buttonNamed(container, "Generate a code");
    button.focus();
    click(button);
    await settle();
    expect(container.textContent).toContain("ABC123");
    expect(document.activeElement).toBe(button);
    expect(button.getAttribute("aria-disabled")).toBeNull();
  });
});

describe("the Gmail card", () => {
  it("keeps focus on Disconnect Gmail while it works, and says why when it failed", async () => {
    on("DELETE /credentials/oauth/gmail", new FakeApiError("down", "INTERNAL", undefined, 500));
    const { container } = render(<GmailCard onDisconnected={() => undefined} />);
    const button = buttonNamed(container, "Disconnect Gmail");
    button.focus();
    click(button);
    await settle();
    expect(document.activeElement).toBe(button);
    expect(button.getAttribute("aria-disabled")).toBeNull();
    expect(container.textContent).toContain("down");
  });

  it("keeps Disconnect Gmail focused and aria-disabled (never disabled) while the request is out, and ignores a second press", async () => {
    let calls = 0;
    on("DELETE /credentials/oauth/gmail", () => {
      calls += 1;
      return new Promise(() => undefined);
    });
    const { container } = render(<GmailCard onDisconnected={() => undefined} />);
    const button = buttonNamed(container, "Disconnect Gmail");
    button.focus();
    click(button);
    await settle(1);
    expect(button.textContent).toBe("Disconnecting...");
    expect(button.disabled).toBe(false);
    expect(button.getAttribute("aria-disabled")).toBe("true");
    expect(document.activeElement).toBe(button);
    click(button);
    expect(calls).toBe(1);
  });

  it("moves focus to Connect Gmail when the disconnect removes the button the keyboard was on", async () => {
    on("DELETE /credentials/oauth/gmail", undefined);
    const { container } = render(<GmailCard onDisconnected={() => undefined} />);
    const button = buttonNamed(container, "Disconnect Gmail");
    button.focus();
    click(button);
    await settle();
    expect(container.contains(button)).toBe(false);
    expect(document.activeElement).toBe(buttonNamed(container, "Connect Gmail"));
  });

  it("leaves focus alone when the person has moved on while the request was out", async () => {
    let finish: () => void = () => undefined;
    on("DELETE /credentials/oauth/gmail", () => new Promise<void>((resolve) => (finish = resolve)));
    const { container } = render(
      <div>
        <GmailCard onDisconnected={() => undefined} />
        <input id="elsewhere" aria-label="Elsewhere" />
      </div>,
    );
    click(buttonNamed(container, "Disconnect Gmail"));
    const elsewhere = query<HTMLInputElement>(container, "#elsewhere");
    elsewhere.focus();
    finish();
    await settle();
    expect(document.activeElement).toBe(elsewhere);
  });

  it("keeps Connect Gmail focused and aria-disabled while it asks for the address", async () => {
    on("GET /profile/integrations/gmail/connect", () => new Promise(() => undefined));
    const { container } = render(<GmailConnectCard credential={null} access="available" testingMode={false} onChanged={async () => undefined} />);
    const button = buttonNamed(container, "Connect Gmail");
    button.focus();
    click(button);
    await settle(1);
    expect(button.disabled).toBe(false);
    expect(button.getAttribute("aria-disabled")).toBe("true");
    expect(document.activeElement).toBe(button);
  });
});
