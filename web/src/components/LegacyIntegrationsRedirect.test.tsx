// @vitest-environment jsdom
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { render } from "../testing/render";
import { LegacyIntegrationsRedirect } from "./LegacyIntegrationsRedirect";
import appSource from "../App.tsx?raw";

function Where() {
  const { pathname, search, hash } = useLocation();
  return <p data-testid="where">{`${pathname}${search}${hash}`}</p>;
}

function landing(start: string): string {
  const { container } = render(
    <MemoryRouter initialEntries={[start]}>
      <Routes>
        <Route path="/profile/integrations" element={<LegacyIntegrationsRedirect />} />
        <Route path="/settings" element={<Where />} />
      </Routes>
    </MemoryRouter>,
  );
  return container.querySelector("[data-testid=where]")?.textContent ?? "";
}

describe("the old Integrations address", () => {
  it("lands on Settings", () => {
    expect(landing("/profile/integrations")).toBe("/settings");
  });

  it("keeps the query the API adds and the fragment a link names", () => {
    expect(landing("/profile/integrations?capability=job_scoring#ai-provider")).toBe(
      "/settings?capability=job_scoring#ai-provider",
    );
  });

  it("is a route of the app next to the new one", () => {
    expect(appSource).toContain('<Route path="/settings" element={<Settings />} />');
    expect(appSource).toContain('<Route path="/profile/integrations" element={<LegacyIntegrationsRedirect />} />');
  });
});
