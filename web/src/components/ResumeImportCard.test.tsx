import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { textOfMarkup } from "../testing/markup";

// The real API client pulls in the Supabase client, which throws at import time without env vars;
// the card must also not reach any network on its own, which the mocks below let this check.
const api = vi.hoisted(() => ({ apiFetch: vi.fn(), apiFetchBytes: vi.fn() }));
vi.mock("../lib/api", () => api);

import { ResumeImportCard } from "./ResumeImportCard";

describe("the connected resume-file import card", () => {
  it("is drawn first as the upload card, with nothing requested and nothing used", () => {
    const onActivated = vi.fn();
    const html = renderToStaticMarkup(
      <MemoryRouter>
        <ResumeImportCard replacesCurrent onActivated={onActivated} />
      </MemoryRouter>,
    );
    expect(textOfMarkup(html)).toContain("Import from a resume file (PDF or DOCX)");
    expect(html).toContain("Choose a file");
    expect(html).not.toContain("Use this profile");
    expect(html).not.toContain("Discard this draft");
    expect(api.apiFetch).not.toHaveBeenCalled();
    expect(api.apiFetchBytes).not.toHaveBeenCalled();
    expect(onActivated).not.toHaveBeenCalled();
  });

  it("is the same card for a person with no profile yet", () => {
    const html = renderToStaticMarkup(
      <MemoryRouter>
        <ResumeImportCard replacesCurrent={false} onActivated={() => {}} />
      </MemoryRouter>,
    );
    expect(html).toContain("Choose a file");
  });
});
