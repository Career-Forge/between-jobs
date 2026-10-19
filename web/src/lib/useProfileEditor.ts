import { useState } from "react";
import { ApiError, apiFetch } from "./api";
import type { CanonicalProfile } from "./profileTypes";

// The versioned-edit mechanic (Sprint 3.1e): editing never mutates in
// place. It clones the current profile, applies the caller's mutation,
// and pushes the result through the SAME deterministic import+activate
// pipeline every fresh upload goes through -- the identical validation,
// the identical business rules (delete your last piece of evidence and
// you get the same honest rejection a from-scratch import would), and a
// brand-new immutable version. "Editing" is a convenience layer over
// "import a new version," never a special path with its own rules.
//
// Known, accepted cost: every save re-derives ALL career_facts with fresh
// entity_keys, including for sections nothing about this edit touched
// (Sprint 2.5b deferred stable-id preservation across re-imports until
// something downstream consumes it -- editing is that consumer's first
// real candidate, not yet its implementation).

// What a failed save says. The API's own sentence for an error it answered (a deterministic
// rejection reads exactly as it would for a fresh import), a plain one for anything else. Shared by
// the editor's own `error` and by the cards that keep a message of their own.
export function saveFailureMessage(e: unknown): string {
  return e instanceof ApiError ? e.message : "Save failed -- please try again.";
}

interface UseProfileEditorResult {
  save: (mutate: (draft: CanonicalProfile) => CanonicalProfile) => Promise<void>;
  saving: boolean;
  error: string | null;
  clearError: () => void;
}

export function useProfileEditor(
  currentProfile: CanonicalProfile,
  onSaved: () => Promise<void>,
): UseProfileEditorResult {
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function save(mutate: (draft: CanonicalProfile) => CanonicalProfile): Promise<void> {
    setSaving(true);
    setError(null);
    const draft = mutate(structuredClone(currentProfile));
    try {
      const version = await apiFetch<{ id: string }>("/profile/versions", {
        method: "POST",
        body: JSON.stringify({ raw_text: JSON.stringify(draft) }),
      });
      await apiFetch(`/profile/versions/${version.id}/activate`, { method: "POST" });
      await onSaved();
    } catch (e) {
      setError(saveFailureMessage(e));
      throw e;
    } finally {
      setSaving(false);
    }
  }

  return { save, saving, error, clearError: () => setError(null) };
}
