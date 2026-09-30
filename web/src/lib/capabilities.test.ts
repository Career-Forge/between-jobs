import { describe, expect, it } from "vitest";
import {
  CAPABILITIES_PATH,
  type CapabilitiesFetcher,
  isTelegramAvailable,
  loadCapabilities,
  parseCapabilities,
  telegramBotUsername,
} from "./capabilities";

describe("parseCapabilities", () => {
  it("reads the telegram flag", () => {
    expect(parseCapabilities({ telegram: true })).toEqual({
      telegram: true,
      telegramBotUsername: null,
    });
    expect(parseCapabilities({ telegram: false })).toEqual({
      telegram: false,
      telegramBotUsername: null,
    });
  });

  it("keeps only the fields it knows", () => {
    expect(parseCapabilities({ telegram: true, somethingNew: 1 })).toEqual({
      telegram: true,
      telegramBotUsername: null,
    });
  });

  it("reads the bot's name when the server gives a valid one", () => {
    expect(parseCapabilities({ telegram: true, telegram_bot_username: "between_jobs_tech_bot" })).toEqual({
      telegram: true,
      telegramBotUsername: "between_jobs_tech_bot",
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
      capabilities: { telegram: false, telegramBotUsername: null },
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
    expect(isTelegramAvailable({ kind: "ready", capabilities: { telegram: true, telegramBotUsername: null } })).toBe(true);
  });

  it("hides it when the server has no bot", () => {
    expect(isTelegramAvailable({ kind: "ready", capabilities: { telegram: false, telegramBotUsername: null } })).toBe(false);
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
        capabilities: { telegram: true, telegramBotUsername: "acme_jobs_bot" },
      }),
    ).toBe("acme_jobs_bot");
  });

  it("is null when the bot is not named, when there is no bot, and while unknown", () => {
    expect(
      telegramBotUsername({ kind: "ready", capabilities: { telegram: true, telegramBotUsername: null } }),
    ).toBeNull();
    expect(
      telegramBotUsername({ kind: "ready", capabilities: { telegram: false, telegramBotUsername: "acme_jobs_bot" } }),
    ).toBeNull();
    expect(telegramBotUsername({ kind: "checking" })).toBeNull();
    expect(telegramBotUsername({ kind: "unavailable" })).toBeNull();
  });
});
