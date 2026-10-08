import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { CapabilitiesState } from "../lib/capabilities";
import Integrations from "./Integrations";

// Which chat-channel cards the Integrations page draws, from what /capabilities says. A static
// render does not run effects, so nothing is fetched: this is only the gating of the two cards
// that link a chat account (Telegram, Discord) and what the Discord card tells people.

const recorded = vi.hoisted(() => ({ caps: { kind: "checking" } as { kind: string } }));

vi.mock("../lib/useCapabilities", () => ({ useCapabilities: () => recorded.caps }));
vi.mock("../lib/useHiringSignalsStatus", () => ({ useHiringSignalsEnabled: () => false }));
vi.mock("../components/SavedSearchesCard", () => ({ SavedSearchesCard: () => null }));

function page(caps: CapabilitiesState): string {
  recorded.caps = caps;
  return renderToStaticMarkup(
    <MemoryRouter>
      <Integrations />
    </MemoryRouter>,
  );
}

const ready = (extra: Record<string, unknown>): CapabilitiesState =>
  ({
    kind: "ready",
    capabilities: { telegram: false, telegramBotUsername: null, testerProgramRequired: false, ...extra },
  }) as CapabilitiesState;

beforeEach(() => {
  recorded.caps = { kind: "checking" };
});

describe("the Discord card", () => {
  it("is out while the answer is awaited, when asking failed, and on a server without Discord", () => {
    expect(page({ kind: "checking" })).not.toContain("Link your Discord account");
    expect(page({ kind: "unavailable" })).not.toContain("Link your Discord account");
    expect(page(ready({}))).not.toContain("Link your Discord account");
    expect(page(ready({ discord: false }))).not.toContain("Link your Discord account");
  });

  it("is drawn when the server has Discord, and says that only slash commands reach the app", () => {
    const html = page(ready({ discord: true, discordInstallUrl: null }));
    expect(html).toContain("<h2>Discord</h2>");
    expect(html).toContain("Link your Discord account");
    expect(html).toContain("slash commands in a direct message");
    expect(html).toContain("text you type in");
    expect(html).toContain("Generate a code");
    expect(html).not.toContain("Add the app to Discord");
  });

  it("links the install address only when the server gave one", () => {
    const html = page(ready({ discord: true, discordInstallUrl: "https://example.com/add-the-app" }));
    expect(html).toContain('href="https://example.com/add-the-app"');
    expect(html).toContain('rel="noopener noreferrer"');
    expect(html).toContain("Add the app to Discord");
  });

  it("does not turn Telegram on, and Telegram does not turn Discord on", () => {
    const withBoth = page(ready({ telegram: true, discord: true }));
    expect(withBoth).toContain("<h2>Telegram</h2>");
    expect(withBoth).toContain("<h2>Discord</h2>");
    const telegramOnly = page(ready({ telegram: true }));
    expect(telegramOnly).toContain("<h2>Telegram</h2>");
    expect(telegramOnly).not.toContain("<h2>Discord</h2>");
    const discordOnly = page(ready({ discord: true }));
    expect(discordOnly).not.toContain("<h2>Telegram</h2>");
    expect(discordOnly).toContain("<h2>Discord</h2>");
  });
});

describe("what the page asks the server for", () => {
  it("mints a code for the channel the card is about: the Discord card asks for discord", async () => {
    const source = (await import("./Integrations.tsx?raw")).default as string;
    const discordCard = source.slice(source.indexOf("function DiscordLinkCard"), source.indexOf("function GmailConnectCard"));
    expect(discordCard).toContain('channel: "discord"');
    expect(discordCard).not.toContain('"telegram"');
  });
});
