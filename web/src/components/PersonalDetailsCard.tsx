import { useEffect, useState } from "react";
import { ApiError } from "../lib/api";
import type { CanonicalProfile, Personal } from "../lib/profileTypes";
import { applyWorkAuthorization } from "../lib/workAuthorization";
import { PersonalDetailsCardView } from "./PersonalDetailsCardView";

// Connected wrapper for the "Personal details" card (work-authorization-
// status.md) -- wiring only, matching HiringSignalsPanel.tsx's own split
// from its view. Owns the draft text, whether Examples is open, AND its
// own save-error message -- deliberately NOT the shared `useProfileEditor`
// error (see below). `onSave` still threads through to the exact same
// editor.save mechanic every other profile edit already uses (clone,
// mutate, re-import, activate -- see useProfileEditor.ts): this is not a
// parallel save path, `onSave` here IS `useProfileEditor`'s `save`, passed
// straight through by ActiveProfile (Profile.tsx).
//
// A single `useProfileEditor` instance is shared by this card AND
// SectionedProfile (both mutate the same canonical profile), so its
// `error` state is genuinely shared, not per-card. Rendering that shared
// value directly here would mean a SectionedProfile modal's save failure
// -- entirely unrelated to work authorization -- surfaces under this
// always-visible card too (and vice versa, this card's own failure would
// silently vanish the moment any SectionedProfile edit modal opens, since
// that modal calls `clearError()` on open with no relation to whether
// THIS card's problem was resolved). Catching the rejection here and
// keeping our own message sidesteps both directions of that bleed --
// `saving` stays shared (both cards genuinely do save through the same
// versioned-import pipeline, so "something is saving" is accurate for
// either), only the error TEXT is scoped to this card.
//
// Personal/header editing is otherwise out of scope for SectionedProfile
// (its own file comment says so) and this isn't a resume-header concern
// either (that's HeaderComposer's job) -- so this card lives directly in
// ActiveProfile, its own section, not folded into either.

export function PersonalDetailsCard({
  personal,
  onSave,
  saving,
}: {
  personal: Personal;
  onSave: (mutate: (draft: CanonicalProfile) => CanonicalProfile) => Promise<void>;
  saving: boolean;
}) {
  const original = personal.work_authorization ?? "";
  const [draft, setDraft] = useState(original);
  const [examplesOpen, setExamplesOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Re-sync if the canonical value changes underneath this card -- the
  // only path that can do that today is a fresh profile import (this card
  // is work_authorization's sole editor), and the reimported profile IS
  // the new truth.
  useEffect(() => {
    setDraft(original);
  }, [original]);

  async function save() {
    setError(null);
    try {
      await onSave((profile) => applyWorkAuthorization(profile, draft));
    } catch (e) {
      // Same message-shaping useProfileEditor.save itself uses -- this
      // card just keeps its own copy instead of reading the shared one
      // back off the editor (see the file comment above for why).
      setError(e instanceof ApiError ? e.message : "Save failed -- please try again.");
    }
  }

  return (
    <PersonalDetailsCardView
      draft={draft}
      dirty={draft !== original}
      examplesOpen={examplesOpen}
      saving={saving}
      error={error}
      actions={{
        setDraft,
        toggleExamples: () => setExamplesOpen((open) => !open),
        save: () => void save(),
      }}
    />
  );
}
