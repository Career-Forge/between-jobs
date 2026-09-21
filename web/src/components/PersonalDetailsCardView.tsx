import {
  WORK_AUTHORIZATION_INDIA_NOTE,
  WORK_AUTHORIZATION_REGION_EXAMPLES,
} from "../lib/workAuthorization";

// Personal details -- work authorization (work-authorization-status.md).
// A pure function of its props, same shape as HiringSignalsPanelView: no
// hooks, so a test can call it directly and walk what it returns (see
// src/testing/reactTree.ts) instead of needing a browser. All local UI
// state (the draft text, whether Examples is open) lives one level up, in
// PersonalDetailsCard.tsx.
//
// D1 (the plan doc): ONE free-text field, region-agnostic -- the "Examples"
// disclosure lists one real sentence per region so a person can find the
// shape closest to their own situation, but the field itself, and this
// component, never ask "which country are you in." No dropdown, no
// per-country branching.

export interface PersonalDetailsActions {
  setDraft: (value: string) => void;
  toggleExamples: () => void;
  save: () => void;
}

export function PersonalDetailsCardView({
  draft,
  dirty,
  examplesOpen,
  saving,
  error,
  actions,
}: {
  draft: string;
  dirty: boolean;
  examplesOpen: boolean;
  saving: boolean;
  error: string | null;
  actions: PersonalDetailsActions;
}) {
  return (
    <div className="bj-card">
      <h2>Personal details</h2>
      <label className="bj-field">
        <span>Work authorization</span>
        <textarea
          rows={3}
          value={draft}
          placeholder="Free text -- see the examples below for the shape that fits your situation."
          onChange={(e) => actions.setDraft(e.target.value)}
        />
      </label>
      <p className="bj-muted bj-small">
        Describe your work authorization in your own words. AI-drafted answers to screening
        questions read only this field -- a vague answer here (just a nationality, say) can turn
        into a confident, wrong guess about your legal status.
      </p>
      {error && <div className="bj-error">{error}</div>}
      <div className="bj-actions">
        <button className="bj-primary" onClick={() => actions.save()} disabled={saving || !dirty}>
          {saving ? "Saving..." : "Save"}
        </button>
      </div>
      <div className="bj-workauth-disclosure">
        <button
          type="button"
          className="bj-workauth-disclosure-toggle"
          onClick={() => actions.toggleExamples()}
          aria-expanded={examplesOpen}
        >
          {examplesOpen ? "Hide" : "Show"} examples by region
        </button>
        {examplesOpen && (
          <div className="bj-workauth-examples">
            {WORK_AUTHORIZATION_REGION_EXAMPLES.map((region) => (
              <div className="bj-workauth-example" key={region.region}>
                <span className="bj-workauth-example-region">{region.region}</span>
                {region.sentences.map((sentence) => (
                  <p className="bj-workauth-example-text" key={sentence}>
                    &ldquo;{sentence}&rdquo;
                  </p>
                ))}
                {region.region === "India" && (
                  <p className="bj-muted bj-small">{WORK_AUTHORIZATION_INDIA_NOTE}</p>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
