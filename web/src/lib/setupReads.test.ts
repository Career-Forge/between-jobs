import { describe, expect, it } from "vitest";
import { SETUP_LOADING, readSetup, searchKeyCount, setupChecks, type Fetcher } from "./setupReads";

// The profile and credentials reads, with the fetcher injected: what each answer means to a page
// that needs setup, and that a failed read is never read as "missing".

class StatusError extends Error {
  constructor(
    message: string,
    public readonly status: number,
  ) {
    super(message);
  }
}

function fetcher(answers: Record<string, unknown>): Fetcher {
  return (async (path: string) => {
    const answer = answers[path];
    if (answer instanceof Error) throw answer;
    return answer;
  }) as Fetcher;
}

describe("readSetup", () => {
  it("reads an active profile and the credential rows", async () => {
    const reads = await readSetup(
      fetcher({ "/profile/current": { id: "pv" }, "/credentials": [{ service: "llm", provider: "openrouter" }, "junk", null] }),
    );
    expect(reads.profile).toEqual({ kind: "ok", value: { active: true } });
    expect(reads.credentials).toEqual({ kind: "ok", value: [{ service: "llm", provider: "openrouter" }] });
  });

  it("reads a 404 on the profile as 'no active profile yet': an answer, not a failure", async () => {
    const reads = await readSetup(fetcher({ "/profile/current": new StatusError("none", 404), "/credentials": [] }));
    expect(reads.profile).toEqual({ kind: "ok", value: { active: false } });
  });

  it("reads any other error on the profile as a failed read", async () => {
    for (const error of [new StatusError("boom", 500), new Error("network"), new StatusError("auth", 401)]) {
      const reads = await readSetup(fetcher({ "/profile/current": error, "/credentials": [] }));
      expect(reads.profile).toEqual({ kind: "failed" });
    }
  });

  it("reads a reply that is not an object as a failed profile read, and one that is not a list as failed credentials", async () => {
    const reads = await readSetup(fetcher({ "/profile/current": "nope", "/credentials": { rows: [] } }));
    expect(reads.profile).toEqual({ kind: "failed" });
    expect(reads.credentials).toEqual({ kind: "failed" });
  });

  it("keeps each read on its own: one failing leaves the other", async () => {
    const reads = await readSetup(fetcher({ "/profile/current": { id: "pv" }, "/credentials": new StatusError("down", 503) }));
    expect(reads.profile.kind).toBe("ok");
    expect(reads.credentials).toEqual({ kind: "failed" });
  });
});

describe("searchKeyCount", () => {
  it("counts the four providers a search can use, once each", () => {
    expect(
      searchKeyCount([
        { service: "search", provider: "brave" },
        { service: "search", provider: "serper" },
        { service: "search", provider: "brave" },
        { service: "search", provider: "firecrawl" },
        { service: "search", provider: "you_com" },
      ]),
    ).toBe(4);
  });

  it("does not count keys that look people up, model keys or an OAuth connection", () => {
    expect(
      searchKeyCount([
        { service: "search", provider: "apollo" },
        { service: "search", provider: "hunter" },
        { service: "search", provider: "exa" },
        { service: "llm", provider: "brave" },
        { service: "oauth", provider: "gmail" },
        { service: "search" },
      ]),
    ).toBe(0);
  });

  it("is zero for no rows", () => {
    expect(searchKeyCount([])).toBe(0);
  });
});

describe("setupChecks", () => {
  const AT = "09:14";

  it("is loading while either read is out", () => {
    const checks = setupChecks(SETUP_LOADING, AT);
    expect(checks.profile).toEqual({ state: "loading" });
    expect(checks.credentials).toEqual({ state: "loading" });
  });

  it("names the call and the time of a failed read", () => {
    const checks = setupChecks({ profile: { kind: "failed" }, credentials: { kind: "failed" } }, AT);
    expect(checks.profile).toEqual({ state: "failed", call: "GET /profile/current", at: AT });
    expect(checks.credentials).toEqual({ state: "failed", call: "GET /credentials", at: AT });
  });

  it("never claims a waiting import: that is known only in the session that made it", () => {
    const checks = setupChecks({ profile: { kind: "ok", value: { active: false } }, credentials: { kind: "ok", value: [] } }, AT);
    expect(checks.profile).toEqual({ state: "ready", value: { active: false, importWaiting: false } });
  });

  it("leaves the model-key reading out unless asked for, so the global key row never speaks for the page", () => {
    const reads = { profile: { kind: "ok", value: { active: true } }, credentials: { kind: "ok", value: [] } } as const;
    expect(setupChecks(reads, AT).credentials).toEqual({ state: "ready", value: {} });
    expect(setupChecks(reads, AT, { modelKey: true }).credentials).toEqual({ state: "ready", value: { modelKey: "none" } });
  });

  it("reads a checked key with a model as ready when asked", () => {
    const reads = {
      profile: { kind: "ok", value: { active: true } },
      credentials: { kind: "ok", value: [{ service: "llm", provider: "openrouter", is_validated: true, model: "vendor/model" }] },
    } as const;
    expect(setupChecks(reads, AT, { modelKey: true }).credentials).toEqual({ state: "ready", value: { modelKey: "ready" } });
  });
});
