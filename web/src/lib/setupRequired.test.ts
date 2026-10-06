import { describe, expect, it } from "vitest";
import {
  GENERIC_SETUP_MESSAGE,
  ROUTED_SETTINGS_PATHS,
  failureOf,
  parseSetupFields,
  problemOf,
  safeSettingsLink,
  setupRequiredNotice,
} from "./setupRequired";
import appSource from "../App.tsx?raw";

// What the API's SETUP_REQUIRED reply becomes on screen: the server's own message plus ONE
// link to where the fix is. The link is the part that must not be trusted blindly -- it is
// a string out of a response body that ends up in a router <Link> -- so most of this file
// is about what is refused.

// An ApiError (api.ts) as this module reads it: structurally, so the test needs no client.
class FakeApiError extends Error {
  constructor(
    public readonly status: number,
    message: string,
    public readonly code?: string,
    public readonly settingsPath?: unknown,
    public readonly capability?: unknown,
    public readonly missing?: unknown,
  ) {
    super(message);
  }
}

function setupError(fields: { path?: unknown; capability?: unknown; missing?: unknown }, message = "Add a key.") {
  return new FakeApiError(409, message, "SETUP_REQUIRED", fields.path, fields.capability, fields.missing);
}

describe("safeSettingsLink", () => {
  it("accepts the routes the server sends, unchanged", () => {
    expect(safeSettingsLink("/profile")).toBe("/profile");
    expect(safeSettingsLink("/profile/integrations")).toBe("/profile/integrations");
  });

  it("accepts the one query the resolver adds (the capability that needs the key)", () => {
    expect(safeSettingsLink("/profile/integrations?capability=job_scoring")).toBe(
      "/profile/integrations?capability=job_scoring",
    );
  });

  it("accepts a capability as long as the server writes one (a letter, then 63 more)", () => {
    const longest = `/profile/integrations?capability=a${"b".repeat(63)}`;
    expect(safeSettingsLink(longest)).toBe(longest);
  });

  it("accepts every routed page, and every one of those really is a route in App.tsx", () => {
    for (const path of ROUTED_SETTINGS_PATHS) {
      expect(safeSettingsLink(path)).toBe(path);
      expect(appSource).toContain(`path="${path}"`);
    }
  });

  it("does not list the auth-flow route as a place a missing setting could be fixed", () => {
    expect(safeSettingsLink("/update-password")).toBeNull();
  });

  it.each([
    ["an absolute URL", "https://evil.example/profile"],
    ["a protocol-relative URL", "//evil.example"],
    ["a protocol-relative URL that looks like a route", "//profile/integrations"],
    ["a javascript: URL", "javascript:alert(1)"],
    ["a data: URL", "data:text/html,<script>1</script>"],
    ["a scheme tucked after a route", "/profile/integrations://evil.example"],
    ["a backslash (browsers read it as a slash)", "/\\evil.example"],
    ["a backslash after the first slash", "/profile\\integrations"],
    ["a newline", "/profile\n"],
    ["a tab", "/pro\tfile"],
    ["a NUL", "/profile\u0000"],
    ["a DEL", "/profile\u007f"],
    ["a space", "/profile "],
    ["a relative path", "profile/integrations"],
    ["a path the app does not route", "/admin"],
    ["a deeper path than any route", "/profile/integrations/extra"],
    ["a route with a parameter's worth of path", "/applications/123"],
    ["a trailing slash (not the routed spelling)", "/profile/"],
    ["another case", "/Profile"],
    ["a percent-encoded slash", "/profile%2fintegrations"],
    ["a fragment", "/profile#settings"],
    ["a query this app never sends", "/profile/integrations?next=https://evil.example"],
    ["the capability query with an unsafe value", "/profile/integrations?capability=a/b"],
    ["the capability query with an uppercase value", "/profile/integrations?capability=Job"],
    ["two queries", "/profile/integrations?capability=job&x=1"],
    ["an empty query", "/profile?"],
    ["an over-long value", `/profile/integrations?capability=${"a".repeat(80)}`],
    ["a prefix before the capability query", "/profile/integrations?next=x&capability=job"],
    ["a capability that starts with a digit", "/profile/integrations?capability=1job"],
    ["a capability that starts with an underscore", "/profile/integrations?capability=_job"],
    ["an uppercase letter after the first character", "/profile/integrations?capability=jOb"],
    ["a capability one character past the longest", `/profile/integrations?capability=a${"b".repeat(64)}`],
    ["an empty string", ""],
    ["just a slash-less scheme", "http:"],
  ])("refuses %s", (_name, input) => {
    expect(safeSettingsLink(input)).toBeNull();
  });

  it.each([[undefined], [null], [42], [{}], [["/profile"]], [true]])(
    "refuses a value that is not a string (%j)",
    (input) => {
      expect(safeSettingsLink(input)).toBeNull();
    },
  );
});

describe("parseSetupFields", () => {
  it("reads the three setup fields from an error envelope", () => {
    expect(
      parseSetupFields({
        code: "SETUP_REQUIRED",
        settings_path: "/profile/integrations?capability=job_scoring",
        capability: "job_scoring",
        missing: ["credential"],
      }),
    ).toEqual({
      settingsPath: "/profile/integrations?capability=job_scoring",
      capability: "job_scoring",
      missing: ["credential"],
    });
  });

  it("treats the nulls an ordinary error carries as absent", () => {
    expect(parseSetupFields({ code: "NOT_FOUND", settings_path: null, capability: null, missing: null })).toEqual({});
  });

  it("drops a field of the wrong type instead of passing it on", () => {
    expect(parseSetupFields({ settings_path: 7, capability: { a: 1 }, missing: "credential" })).toEqual({});
  });

  it("keeps only the strings of a missing list", () => {
    expect(parseSetupFields({ missing: ["profile_version", 3, null, "credential"] })).toEqual({
      missing: ["profile_version", "credential"],
    });
  });

  it("keeps the path exactly as sent -- padding is for safeSettingsLink to refuse, not for this to trim away", () => {
    expect(parseSetupFields({ settings_path: " /profile " }).settingsPath).toBe(" /profile ");
  });

  it("copes with a body that is not an object at all", () => {
    expect(parseSetupFields(undefined)).toEqual({});
    expect(parseSetupFields(null)).toEqual({});
    expect(parseSetupFields("nope")).toEqual({});
    expect(parseSetupFields([1, 2])).toEqual({});
  });
});

describe("setupRequiredNotice", () => {
  it("is null for an error that is not SETUP_REQUIRED, and for a thing that is not an error", () => {
    expect(setupRequiredNotice(new FakeApiError(404, "gone", "NOT_FOUND", "/profile"))).toBeNull();
    expect(setupRequiredNotice(new FakeApiError(500, "boom"))).toBeNull();
    expect(setupRequiredNotice(new Error("Failed to fetch"))).toBeNull();
    expect(setupRequiredNotice("SETUP_REQUIRED")).toBeNull();
    expect(setupRequiredNotice({ code: "SETUP_REQUIRED", message: "x", settingsPath: "/profile" })).toBeNull();
    expect(setupRequiredNotice(undefined)).toBeNull();
  });

  it("links to a routed internal path, with a label for the capability that needs it (the model key)", () => {
    const notice = setupRequiredNotice(
      setupError({
        path: "/profile/integrations?capability=job_scoring",
        capability: "job_scoring",
        missing: ["credential"],
      }),
    );
    expect(notice).toEqual({
      message: "Add a key.",
      linkTo: "/profile/integrations?capability=job_scoring",
      linkLabel: "Add a model key in Integrations",
    });
  });

  // The resume file import has no model setting of its own: it falls back to the default model, so
  // what a person with no key needs is the same model key, whether the server names what is missing
  // or only the capability.
  it("labels the resume file import's missing model as adding a model key", () => {
    const path = "/profile/integrations?capability=profile_import";
    expect(
      setupRequiredNotice(setupError({ path, capability: "profile_import", missing: ["execution_mode"] })),
    ).toMatchObject({ linkTo: path, linkLabel: "Add a model key in Integrations" });
    expect(setupRequiredNotice(setupError({ path, capability: "profile_import" }))).toMatchObject({
      linkTo: path,
      linkLabel: "Add a model key in Integrations",
    });
  });

  it("labels a missing profile as finishing the profile", () => {
    expect(
      setupRequiredNotice(setupError({ path: "/profile", capability: "profile", missing: ["profile_version"] })),
    ).toMatchObject({ linkTo: "/profile", linkLabel: "Finish your profile" });
  });

  it.each([
    ["apollo_credential", "Add an Apollo or Hunter key in Integrations"],
    ["exa_credential", "Add an Exa key in Integrations"],
    ["gmail_oauth", "Connect Gmail in Integrations"],
    ["firecrawl_credential", "Add a Firecrawl key in Integrations"],
    ["you_com_or_firecrawl_credential", "Add a You.com or Firecrawl key in Integrations"],
    ["search_credential", "Add a search key in Integrations"],
  ])("labels what is missing (%s) by name", (token, label) => {
    expect(
      setupRequiredNotice(setupError({ path: "/profile/integrations", capability: "anything", missing: [token] })),
    ).toMatchObject({ linkTo: "/profile/integrations", linkLabel: label });
  });

  it("tells a search-key failure from a model-key failure of the same capability by what is missing", () => {
    const search = setupRequiredNotice(
      setupError({
        path: "/profile/integrations",
        capability: "company_intel",
        missing: ["you_com_or_firecrawl_credential"],
      }),
    );
    const model = setupRequiredNotice(
      setupError({
        path: "/profile/integrations?capability=company_intel",
        capability: "company_intel",
        missing: ["credential"],
      }),
    );
    expect(search?.linkLabel).toBe("Add a You.com or Firecrawl key in Integrations");
    expect(model?.linkLabel).toBe("Add a model key in Integrations");
  });

  it("falls back to the capability when the server named nothing missing", () => {
    expect(
      setupRequiredNotice(
        setupError({ path: "/profile/integrations?capability=prepare_application", capability: "prepare_application" }),
      ),
    ).toMatchObject({ linkLabel: "Add a model key in Integrations" });
    expect(setupRequiredNotice(setupError({ path: "/profile", capability: "profile", missing: [] }))).toMatchObject({
      linkLabel: "Finish your profile",
    });
  });

  it("uses a generic label, by destination, for a capability it does not know", () => {
    expect(
      setupRequiredNotice(setupError({ path: "/profile/integrations", capability: "something_new" })),
    ).toEqual({ message: "Add a key.", linkTo: "/profile/integrations", linkLabel: "Open Integrations" });
    expect(setupRequiredNotice(setupError({ path: "/profile", capability: "something_new" }))).toMatchObject({
      linkTo: "/profile",
      linkLabel: "Open your profile",
    });
    expect(setupRequiredNotice(setupError({ path: "/discover" }))).toMatchObject({
      linkTo: "/discover",
      linkLabel: "Continue",
    });
  });

  it("does not put a label on a link it does not describe (a profile label on the Integrations page)", () => {
    expect(
      setupRequiredNotice(setupError({ path: "/profile/integrations", capability: "profile", missing: ["profile_version"] })),
    ).toMatchObject({ linkTo: "/profile/integrations", linkLabel: "Open Integrations" });
    expect(
      setupRequiredNotice(setupError({ path: "/profile", capability: "job_scoring", missing: ["credential"] })),
    ).toMatchObject({ linkTo: "/profile", linkLabel: "Open your profile" });
  });

  it("is the message alone when the server gave no path -- never a guessed destination", () => {
    expect(
      setupRequiredNotice(setupError({ capability: "profile", missing: ["profile_version"] }, "Import a profile first.")),
    ).toEqual({ message: "Import a profile first.", linkTo: null, linkLabel: null });
    expect(setupRequiredNotice(setupError({ path: null }))).toMatchObject({ linkTo: null, linkLabel: null });
  });

  it("is the message alone when the path is an external or unrouted one", () => {
    for (const path of ["https://evil.example/x", "//evil.example", "javascript:alert(1)", "/admin", "/profile\\x"]) {
      expect(setupRequiredNotice(setupError({ path, capability: "profile" }))).toEqual({
        message: "Add a key.",
        linkTo: null,
        linkLabel: null,
      });
    }
  });

  it("never lets a hostile capability or missing value reach the label or the link", () => {
    const notice = setupRequiredNotice(
      setupError({
        path: "/profile/integrations",
        capability: "__proto__",
        missing: ["constructor", "toString", { evil: true }],
      }),
    );
    expect(notice).toEqual({ message: "Add a key.", linkTo: "/profile/integrations", linkLabel: "Open Integrations" });
  });

  it("gives a plain sentence when the server sent no message at all", () => {
    expect(setupRequiredNotice(setupError({ path: "/profile" }, ""))?.message).toBe(GENERIC_SETUP_MESSAGE);
    expect(setupRequiredNotice(setupError({ path: "/profile" }, "   "))?.message).toBe(GENERIC_SETUP_MESSAGE);
  });
});

describe("setupRequiredNotice: what labels the link", () => {
  // The resolver raises SETUP_REQUIRED with `missing: []` when a capability is turned off, so
  // there the label can only come from the capability. Every capability that runs on the
  // person's own model has to be named, or its notice degrades to the generic label.
  it.each([
    "job_scoring",
    "prepare_application",
    "positioning_brief",
    "interview_practice",
    "outreach_writer",
    "contact_research",
    "company_intel",
    "warm_path_events",
    "application_answer_generation",
  ])("labels %s, switched off (nothing named as missing), as the model key", (capability) => {
    expect(
      setupRequiredNotice(
        setupError({ path: `/profile/integrations?capability=${capability}`, capability, missing: [] }),
      ),
    ).toMatchObject({ linkLabel: "Add a model key in Integrations" });
  });

  // `hiring_signals` and `gmail_reply_check` are not in the capability table at all: they
  // rely on what the resolver says is missing.
  it.each(["execution_mode", "provider", "model", "credential"])(
    "labels a missing %s as the model key, for a capability the table does not know",
    (token) => {
      expect(
        setupRequiredNotice(
          setupError({ path: "/profile/integrations", capability: "gmail_reply_check", missing: [token] }),
        ),
      ).toMatchObject({ linkLabel: "Add a model key in Integrations" });
    },
  );

  it("labels a missing profile version as the profile, for a capability the table does not know", () => {
    expect(
      setupRequiredNotice(
        setupError({ path: "/profile", capability: "gmail_reply_check", missing: ["profile_version"] }),
      ),
    ).toMatchObject({ linkLabel: "Finish your profile" });
  });

  describe("when several things are missing", () => {
    const labelOf = (missing: string[]) =>
      setupRequiredNotice(setupError({ path: "/profile/integrations", capability: "something_new", missing }))
        ?.linkLabel;

    it("goes by the first one that is known", () => {
      expect(labelOf(["model", "apollo_credential"])).toBe("Add a model key in Integrations");
      expect(labelOf(["apollo_credential", "model"])).toBe("Add an Apollo or Hunter key in Integrations");
    });

    it("skips one that is not known rather than stopping at it", () => {
      expect(labelOf(["something_new", "credential"])).toBe("Add a model key in Integrations");
    });
  });

  it("is the message alone when the path is padded with spaces -- refused, not trimmed into a link", () => {
    expect(setupRequiredNotice(setupError({ path: " /profile " }))).toEqual({
      message: "Add a key.",
      linkTo: null,
      linkLabel: null,
    });
  });
});

describe("failureOf", () => {
  it("is the notice, link and all, for a setup failure", () => {
    const failure = failureOf(
      setupError({ path: "/profile", capability: "profile", missing: ["profile_version"] }, "Import a profile first."),
      "Failed to load",
    );
    expect(failure).toEqual({
      kind: "setup",
      notice: { message: "Import a profile first.", linkTo: "/profile", linkLabel: "Finish your profile" },
    });
  });

  it("is a plain error for any other failure, with the server's own message", () => {
    expect(failureOf(new FakeApiError(500, "boom", "INTERNAL"), "Failed to load")).toEqual({
      kind: "error",
      message: "boom",
    });
    expect(failureOf(new Error("Failed to fetch"), "Failed to load")).toEqual({
      kind: "error",
      message: "Failed to fetch",
    });
  });

  it("uses the fallback for a thing that is not an error at all", () => {
    expect(failureOf("nope", "Failed to load")).toEqual({ kind: "error", message: "Failed to load" });
    expect(failureOf(undefined, "Failed to save")).toEqual({ kind: "error", message: "Failed to save" });
  });

  it("words a rate limit the way the rest of the app does", () => {
    const failure = failureOf(new FakeApiError(429, "slow down", "RATE_LIMITED"), "Failed to load");
    expect(failure.kind).toBe("error");
    expect(failure.kind === "error" && failure.message).toMatch(/too often/);
  });

  it("keeps a setup failure with no path as a notice with no link, never as bare text", () => {
    expect(failureOf(setupError({}, "Needs setup."), "Failed to load")).toEqual({
      kind: "setup",
      notice: { message: "Needs setup.", linkTo: null, linkLabel: null },
    });
  });
});

describe("problemOf", () => {
  it("is the notice object for a setup failure, link intact", () => {
    const problem = problemOf(
      setupError({ path: "/profile/integrations", capability: "gmail_oauth", missing: ["gmail_oauth"] }),
      "Failed",
    );
    expect(problem).toEqual({
      message: "Add a key.",
      linkTo: "/profile/integrations",
      linkLabel: "Connect Gmail in Integrations",
    });
  });

  it("is a string for any other failure", () => {
    expect(problemOf(new FakeApiError(404, "gone", "NOT_FOUND"), "Failed")).toBe("gone");
    expect(problemOf(new Error("Failed to fetch"), "Failed")).toBe("Failed to fetch");
    expect(problemOf(42, "Failed")).toBe("Failed");
  });
});

