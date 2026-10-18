import { useCallback, useEffect, useState } from "react";
import { apiFetch } from "../api";
import { parseProviderCatalog, type LlmProvider } from "./llmProviders";

// The provider catalogue (GET /credentials/llm/providers): static data from the API, read once when
// the Settings page opens. While it loads, or if it fails, the page still shows the keys the person
// has (it knows their provider ids) and says plainly that the list of providers to add is missing.

export type CatalogState = { kind: "loading" } | { kind: "failed" } | { kind: "ready"; providers: readonly LlmProvider[] };

export function useProviderCatalog(): { catalog: CatalogState; retry: () => void } {
  const [catalog, setCatalog] = useState<CatalogState>({ kind: "loading" });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let current = true;
    setCatalog({ kind: "loading" });
    apiFetch<unknown>("/credentials/llm/providers").then(
      (body) => {
        if (!current) return;
        const providers = parseProviderCatalog(body);
        setCatalog(providers === null ? { kind: "failed" } : { kind: "ready", providers });
      },
      () => {
        if (current) setCatalog({ kind: "failed" });
      },
    );
    return () => {
      current = false;
    };
  }, [attempt]);

  const retry = useCallback(() => setAttempt((n) => n + 1), []);
  return { catalog, retry };
}
