import { describe, expect, it } from "vitest";
import {
  CAPABILITIES_PATH,
  type CapabilitiesFetcher,
  discordInstallUrl,
  isDiscordAvailable,
  isTelegramAvailable,
  isTesterProgramRequired,
  loadCapabilities,
  parseCapabilities,
  telegramBotUsername,
} from "./capabilities";

describe("parseCapabilities", () => {
  it("reads the telegram flag", () => {
    expect(parseCapabilities({ telegram: true })).toEqual({
      telegram: true,
      telegramBotUsername: null,
      testerProgramRequired: false,
    });
    expect(parseCapabilities({ telegram: false })).toEqual({
      telegram: false,
      telegramBotUsername: null,
      testerProgramRequired: false,
    });
  });

  it("keeps only the fields it knows", () => {
    expect(parseCapabilities({ telegram: true, somethingNew: 1 })).toEqual({
      telegram: true,
      telegramBotUsername: null,
      testerProgramRequired: false,
    });
  });

  it("reads the bot's name when the server gives a valid one", () => {
    expect(parseCapabilities({ telegram: true, telegram_bot_username: "between_jobs_tech_bot" })).toEqual({
      telegram: true,
      telegramBotUsername: "between_jobs_tech_bot",
      testerProgramRequired: false,
    });
  });

  it.each([
    ["null", null],
    ["a number", 7],
    ["too short", "abcd"],
    ["with a space", "my bot"],
    ["with markup", "<b>bot</b>"],
    ["with an @", "@between_jobs_tech_bot"],
    ["starting with a digit", "1between_bot"],
    ["too long", "a".repeat(33)],
  ])("treats a bot name that is %s as unknown, not as a reason to reject the answer", (_l, name) => {
    expect(parseCapabilities({ telegram: true, telegram_bot_username: name })).toEqual({
      telegram: true,
      telegramBotUsername: null,
      testerProgramRequired: false,
    });
  });

  it.each([
    ["null", null],
    ["undefined", undefined],
    ["a string", "true"],
    ["an array", [true]],
    ["an empty object", {}],
    ["a string flag", { telegram: "true" }],
    ["a numeric flag", { telegram: 1 }],
    ["a null flag", { telegram: null }],
  ])("does not guess from %s", (_label, body) => {
    expect(parseCapabilities(body)).toBeNull();
  });
});

describe("loadCapabilities", () => {
  it("asks the capabilities route and returns what it says", async () => {
    const asked: string[] = [];
    const fetcher: CapabilitiesFetcher = async <T>(path: string) => {
      asked.push(path);
      return { telegram: false } as T;
    };
    expect(await loadCapabilities(fetcher)).toEqual({
      kind: "ready",
      capabilities: { telegram: false, telegramBotUsername: null, testerProgramRequired: false },
    });
    expect(asked).toEqual([CAPABILITIES_PATH]);
  });

  it("is unavailable when the request fails", async () => {
    const fetcher: CapabilitiesFetcher = async () => {
      throw new Error("network down");
    };
    expect(await loadCapabilities(fetcher)).toEqual({ kind: "unavailable" });
  });

  it("is unavailable when the answer is not the expected shape", async () => {
    const fetcher: CapabilitiesFetcher = async <T>() => ({ telegram: "yes" }) as T;
    expect(await loadCapabilities(fetcher)).toEqual({ kind: "unavailable" });
  });
});

describe("isTelegramAvailable", () => {
  it("shows the card only for a definite yes", () => {
    expect(isTelegramAvailable({ kind: "ready", capabilities: { telegram: true, telegramBotUsername: null, testerProgramRequired: false } })).toBe(true);
  });

  it("hides it when the server has no bot", () => {
    expect(isTelegramAvailable({ kind: "ready", capabilities: { telegram: false, telegramBotUsername: null, testerProgramRequired: false } })).toBe(false);
  });

  it("hides it while the answer is unknown, and when asking failed", () => {
    expect(isTelegramAvailable({ kind: "checking" })).toBe(false);
    expect(isTelegramAvailable({ kind: "unavailable" })).toBe(false);
  });
});

describe("telegramBotUsername", () => {
  it("is the server's bot when there is one and it is named", () => {
    expect(
      telegramBotUsername({
        kind: "ready",
        capabilities: { telegram: true, telegramBotUsername: "acme_jobs_bot", testerProgramRequired: false },
      }),
    ).toBe("acme_jobs_bot");
  });

  it("is null when the bot is not named, when there is no bot, and while unknown", () => {
    expect(
      telegramBotUsername({ kind: "ready", capabilities: { telegram: true, telegramBotUsername: null, testerProgramRequired: false } }),
    ).toBeNull();
    expect(
      telegramBotUsername({ kind: "ready", capabilities: { telegram: false, telegramBotUsername: "acme_jobs_bot", testerProgramRequired: false } }),
    ).toBeNull();
    expect(telegramBotUsername({ kind: "checking" })).toBeNull();
    expect(telegramBotUsername({ kind: "unavailable" })).toBeNull();
  });
});

describe("the tester programme flag", () => {
  it("is read when the server says it is required", () => {
    expect(parseCapabilities({ telegram: false, tester_program_required: true })).toEqual({
      telegram: false,
      telegramBotUsername: null,
      testerProgramRequired: true,
    });
  });

  it("is not required when the server says so, says nothing (an older server), or sends something that is not a boolean", () => {
    for (const body of [
      { telegram: true, tester_program_required: false },
      { telegram: true },
      { telegram: true, tester_program_required: "true" },
      { telegram: true, tester_program_required: 1 },
      { telegram: true, tester_program_required: null },
    ]) {
      expect(parseCapabilities(body)?.testerProgramRequired, JSON.stringify(body)).toBe(false);
    }
  });

  it("is required only for a definite yes: not while checking, and not when asking failed", () => {
    const ready = (testerProgramRequired: boolean) =>
      ({ kind: "ready", capabilities: { telegram: false, telegramBotUsername: null, testerProgramRequired } }) as const;
    expect(isTesterProgramRequired(ready(true))).toBe(true);
    expect(isTesterProgramRequired(ready(false))).toBe(false);
    expect(isTesterProgramRequired({ kind: "checking" })).toBe(false);
    expect(isTesterProgramRequired({ kind: "unavailable" })).toBe(false);
  });
});


describe("Discord", () => {
  it("is not there unless the server says so: the answer a server without it gives is unchanged", () => {
    const parsed = parseCapabilities({ telegram: true });
    expect(parsed).toEqual({ telegram: true, telegramBotUsername: null, testerProgramRequired: false });
    expect(parsed).not.toHaveProperty("discord");
    for (const discord of [false, "true", 1, null, undefined]) {
      expect(parseCapabilities({ telegram: false, discord })).not.toHaveProperty("discord");
    }
  });

  it("reads the flag and the install address when the server sends them", () => {
    expect(
      parseCapabilities({ telegram: false, discord: true, discord_install_url: "https://example.com/add-the-app" }),
    ).toEqual({
      telegram: false,
      telegramBotUsername: null,
      testerProgramRequired: false,
      discord: true,
      discordInstallUrl: "https://example.com/add-the-app",
    });
    expect(parseCapabilities({ telegram: false, discord: true })?.discordInstallUrl).toBeNull();
  });

  it("keeps the query string of Discord's own install link, which is the whole of it", () => {
    for (const link of [
      "https://discord.com/oauth2/authorize?client_id=1234567890123456789",
      "https://discord.com/oauth2/authorize?client_id=1234567890123456789&scope=bot+applications.commands&integration_type=0",
    ]) {
      expect(parseCapabilities({ telegram: false, discord: true, discord_install_url: link })?.discordInstallUrl).toBe(link);
    }
  });

  it.each([
    "http://example.com/add",
    "javascript:alert(1)",
    "https://user:secret@example.com/add",
    "not a url",
    "",
    42,
    null,
  ])("shows no install address for %j", (value) => {
    expect(parseCapabilities({ telegram: false, discord: true, discord_install_url: value })?.discordInstallUrl).toBeNull();
  });

  it("offers the card only for a definite yes", () => {
    const on = { kind: "ready", capabilities: { telegram: false, telegramBotUsername: null, testerProgramRequired: false, discord: true, discordInstallUrl: "https://example.com/a" } } as const;
    const off = { kind: "ready", capabilities: { telegram: false, telegramBotUsername: null, testerProgramRequired: false } } as const;
    expect(isDiscordAvailable(on)).toBe(true);
    expect(isDiscordAvailable(off)).toBe(false);
    expect(isDiscordAvailable({ kind: "checking" })).toBe(false);
    expect(isDiscordAvailable({ kind: "unavailable" })).toBe(false);
    expect(discordInstallUrl(on)).toBe("https://example.com/a");
    expect(discordInstallUrl(off)).toBeNull();
    expect(discordInstallUrl({ kind: "checking" })).toBeNull();
  });
});
