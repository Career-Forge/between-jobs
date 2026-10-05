import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { setupRequiredNotice } from "../lib/setupRequired";
import { ProblemView, SetupRequiredNotice } from "./SetupRequiredNotice";

// The setup-needed notice as it renders for each kind of error the server can send, going
// all the way from an error to markup (lib/setupRequired.ts decides, this draws).

class FakeApiError extends Error {
  constructor(
    message: string,
    public readonly settingsPath?: string,
    public readonly capability?: string,
    public readonly missing?: readonly string[],
    public readonly code: string = "SETUP_REQUIRED",
  ) {
    super(message);
  }
}

function render(node: React.ReactNode): string {
  return renderToStaticMarkup(<MemoryRouter>{node}</MemoryRouter>);
}

function noticeFor(error: unknown) {
  const notice = setupRequiredNotice(error);
  if (notice === null) throw new Error("expected a setup notice");
  return notice;
}

describe("SetupRequiredNotice", () => {
  it("shows the message and a link to the model-key page for a missing model key", () => {
    const html = render(
      <SetupRequiredNotice
        notice={noticeFor(
          new FakeApiError(
            "No openrouter key configured for 'job_scoring'.",
            "/profile/integrations?capability=job_scoring",
            "job_scoring",
            ["credential"],
          ),
        )}
      />,
    );
    expect(html).toContain("No openrouter key configured for &#x27;job_scoring&#x27;.");
    expect(html).toContain('href="/profile/integrations?capability=job_scoring"');
    expect(html).toContain(">Add a model key in Integrations</a>");
    expect(html).toContain('role="alert"');
  });

  it("links a missing profile to the profile page", () => {
    const html = render(
      <SetupRequiredNotice
        notice={noticeFor(new FakeApiError("Import a profile first.", "/profile", "profile", ["profile_version"]))}
      />,
    );
    expect(html).toContain('href="/profile"');
    expect(html).toContain(">Finish your profile</a>");
  });

  it("is the message alone, with no link at all, when the server gave no usable path", () => {
    for (const path of [undefined, "https://evil.example", "//evil.example", "javascript:alert(1)", "/admin"]) {
      const html = render(<SetupRequiredNotice notice={noticeFor(new FakeApiError("Add a key.", path))} />);
      expect(html).toContain("Add a key.");
      expect(html).not.toContain("<a ");
      expect(html).not.toContain("href=");
    }
  });

  it("never writes an unsafe path into an attribute", () => {
    const html = render(
      <SetupRequiredNotice notice={noticeFor(new FakeApiError("x", '/profile"><script>alert(1)</script>'))} />,
    );
    expect(html).not.toContain("<script");
    expect(html).not.toContain("href=");
  });

  it("renders a message with markup in it as text, not as HTML", () => {
    const html = render(
      <SetupRequiredNotice notice={noticeFor(new FakeApiError("<img src=x onerror=alert(1)>", "/profile"))} />,
    );
    expect(html).not.toContain("<img");
    expect(html).toContain("&lt;img");
  });
});

describe("ProblemView", () => {
  it("draws a plain message as the red error line", () => {
    const html = render(<ProblemView problem="Enrichment failed" />);
    expect(html).toContain('class="bj-error"');
    expect(html).toContain("Enrichment failed");
  });

  it("draws a setup notice as the callout, with its link", () => {
    const html = render(
      <ProblemView problem={noticeFor(new FakeApiError("Add a key.", "/profile/integrations", "contact_enrichment", ["apollo_credential"]))} />,
    );
    expect(html).toContain("bj-setup-notice");
    expect(html).not.toContain("bj-error");
    expect(html).toContain('href="/profile/integrations"');
    expect(html).toContain("Add an Apollo or Hunter key in Integrations");
  });

  it("draws nothing for no problem", () => {
    expect(render(<ProblemView problem={null} />)).toBe("");
    expect(render(<ProblemView problem="" />)).toBe("");
  });
});
