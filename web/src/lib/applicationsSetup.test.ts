import { describe, expect, it } from "vitest";
import { readSetupChecks } from "./applicationsSetup";

// The two reads the band's global rows need. Each fails on its own, and a 404 for the profile is the
// answer "no active profile yet", not a failure.

class StatusError extends Error {
  constructor(
    message: string,
    public readonly status: number,
  ) {
    super(message);
  }
}

const AT = () => new Date("2026-10-05T09:14:00Z");

function fetcher(answers: Record<string, unknown>) {
  const asked: string[] = [];
  const fn = async <T,>(path: string): Promise<T> => {
    asked.push(path);
    const answer = answers[path];
    if (answer instanceof Error) throw answer;
    return answer as T;
  };
  return { fn, asked };
}

describe("readSetupChecks", () => {
  it("asks for the profile and the credentials, and nothing else", async () => {
    const { fn, asked } = fetcher({ "/profile/current": { id: "pv-1" }, "/credentials": [] });
    await readSetupChecks(fn, AT);
    expect(asked.sort()).toEqual(["/credentials", "/profile/current"]);
  });

  it("reads an active profile and a usable key", async () => {
    const { fn } = fetcher({
      "/profile/current": { id: "pv-1" },
      "/credentials": [{ service: "llm", provider: "openrouter", is_validated: true, model: "vendor/model" }],
    });
    expect(await readSetupChecks(fn, AT)).toEqual({
      profile: { state: "ready", value: { active: true, importWaiting: false } },
      credentials: { state: "ready", value: { modelKey: "ready" } },
    });
  });

  it("reads a 404 for the profile as no active profile, not a failure", async () => {
    const { fn } = fetcher({ "/profile/current": new StatusError("none", 404), "/credentials": [] });
    const checks = await readSetupChecks(fn, AT);
    expect(checks.profile).toEqual({ state: "ready", value: { active: false, importWaiting: false } });
    expect(checks.credentials).toEqual({ state: "ready", value: { modelKey: "none" } });
  });

  it("names the call and the client time when a read fails, and leaves the other read alone", async () => {
    const { fn } = fetcher({ "/profile/current": new StatusError("boom", 500), "/credentials": [] });
    const checks = await readSetupChecks(fn, () => new Date("2026-10-05T09:14:00Z"));
    expect(checks.profile.state).toBe("failed");
    if (checks.profile.state === "failed") {
      expect(checks.profile.call).toBe("GET /profile/current");
      expect(checks.profile.at).toMatch(/^\d\d:\d\d$/);
    }
    expect(checks.credentials.state).toBe("ready");
  });

  it("treats a reply that is not the expected shape as a failed read", async () => {
    const { fn } = fetcher({ "/profile/current": "nope", "/credentials": { rows: [] } });
    const checks = await readSetupChecks(fn, AT);
    expect(checks.profile.state).toBe("failed");
    expect(checks.credentials.state).toBe("failed");
  });
});
