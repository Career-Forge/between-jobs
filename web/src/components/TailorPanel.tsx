import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { apiFetch } from "../lib/api";
import type { ResumeDocument } from "../lib/headerComposerTypes";
import { failureOf, type SetupNotice } from "../lib/setupRequired";
import { SKILL_STATE_LABELS, type ClusterCoverage, type CoverageResponse, type SkillState } from "../lib/tailorTypes";
import { tailorTrail } from "../lib/workspaceTrails";
import { BlockTrail } from "./paper/DocumentLayout";
import { FailureState, LoadingState } from "./paper/StateViews";
import { TRAIL_GLYPHS, type TrailGlyphName } from "../lib/trail";
import { RunButton } from "./research/RunParts";
import { SetupRequiredNotice } from "./SetupRequiredNotice";
import "../styles/workspace.css";

// Tailor: how this resume's profile covers the posting. Every requirement cluster shows a plain count
// and, opened, the facts from the profile that matched it -- never an unexplained percentage. Each skill
// carries one of four states and always says it in words as well as a mark.
//
//   Verified     ✓  the profile has it, with evidence
//   Supported    ◆  a profile fact backs it
//   Adjacent     ◇  something close is in the profile
//   Gap          ○  nothing in the profile speaks to it
//
// Must-have clusters are set in ivory bold and preferred ones in muted text; neither is red or cyan.
// The picks a person makes among a cluster's facts are saved (PATCH .../evidence), and the panel says
// plainly that the draft does not read them yet: generation is not given them today.
//
// The clusters themselves are a model's reading of the posting (the engine's first step); code then
// checks them against the profile's facts. The trail under the panel says so.
//
// WHAT RUNS WHEN. Opening the page runs nothing here. The coverage match is a model call on the person's
// own key, so it is made only when they press "Check coverage": never on mount (and so not twice a visit
// under StrictMode in development), and never again on its own. A press reads the resume document, then
// the profile's facts, and asks for the coverage last, so a read that fails costs no model call, and
// neither does a press the person has left the page before its reads finished. A call already out when
// they leave is not cancelled: it is paid for and its answer dropped. Nothing is stored: the answer
// lives in this panel's state, so a person who leaves the application and comes back sees the button
// again, and the sentence above it says so. The page does not mount this panel while it knows no model
// key is saved (it shows the missing key instead, and fires nothing).
//
// States: idle is one sentence and the button; loading keeps both (the button busy) with a placeholder
// in the panel's own shape; a missing profile or key (the server's 409) shows its notice with the link
// that fixes it and no button and no Retry; any other failure of the three reads keeps the sentence and
// the button and adds a callout with Retry, which makes the same press again (the button above does the
// same). When the result arrives the button has gone, so focus moves to the result unless it was already
// somewhere else. A save that fails keeps the panel as it was and says the picks were not saved, with a
// Retry that sends them again -- it does not run the coverage model call a second time.

interface CareerFact {
  id: string;
  fact_type: string;
  value_json: Record<string, unknown>;
}

function factLabel(fact: CareerFact): string {
  const v = fact.value_json;
  const title = (v.title as string) || (v.name as string) || (v.degree as string) || "";
  const org = (v.company as string) || (v.institution as string) || "";
  return org ? `${title} -- ${org}` : title || fact.fact_type;
}

const SKILL_MARKS: Record<SkillState, { glyph: TrailGlyphName | null; char: string }> = {
  verified: { glyph: "passed", char: "✓" },
  supported: { glyph: "evidence", char: "◆" },
  adjacent: { glyph: "evidence", char: "◇" },
  unsupported: { glyph: "code", char: "○" },
};

type State =
  // Nothing has been asked yet: the sentence and the button.
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "error"; message: string }
  // No active profile, or no model key for the coverage match: the notice links to the page that fixes
  // it (no button, no Retry).
  | { kind: "setup"; notice: SetupNotice }
  | {
      kind: "ready";
      documentId: string;
      profileVersionId: string | null;
      coverage: ClusterCoverage[];
      skills: CoverageResponse["skills"];
      facts: CareerFact[];
      selected: Set<string>;
      expanded: string | null;
      saving: boolean;
      saveError: string | null;
    };

const IDLE_SENTENCE =
  "Reads this posting's requirements with your model, then matches them to your profile by code. Pressing Check coverage makes a real model call on your own key. The match isn't saved, so each visit needs its own press.";

export function TailorPanel({ applicationId }: { applicationId: string }) {
  const [state, setState] = useState<State>({ kind: "idle" });
  // A press while one is out is ignored here, not by the button: the button is aria-disabled (so a focused one keeps focus),
  // which does not stop a click. Reset in `finally`, so a press after a failure, from the button or from the callout's
  // Retry, is a new one.
  const working = useRef(false);
  // False once the panel has gone, so a press whose free reads outlast the person's stay does not go on to the model call.
  // Set to true again on every mount: StrictMode runs a mount's cleanup and its setup once before the page is shown.
  const present = useRef(true);
  useEffect(() => {
    present.current = true;
    return () => {
      present.current = false;
    };
  }, []);
  const checkButton = useRef<HTMLButtonElement>(null);
  const resultRoot = useRef<HTMLDivElement>(null);
  // Retry removes the callout it is pressed in; focus goes on to the button, which stays.
  const focusCheckButton = useCallback(() => checkButton.current, []);

  // The one press. The two reads that cost nothing come first and the model call last, so a failed read never spends it.
  const check = useCallback(async () => {
    if (working.current) return;
    working.current = true;
    setState({ kind: "loading" });
    try {
      const resumeDocument = await apiFetch<ResumeDocument & { selected_evidence_fact_ids?: string[] }>(`/resume-documents/mine?application_id=${applicationId}`);
      const facts = await apiFetch<CareerFact[]>(`/profile/versions/${resumeDocument.profile_version_id}/career-facts`);
      if (!present.current) return;
      const result = await apiFetch<CoverageResponse>(`/resume-documents/${resumeDocument.id}/coverage`, { method: "POST" });
      const stored = resumeDocument.selected_evidence_fact_ids ?? [];
      const initial = stored.length > 0 ? stored : result.coverage.flatMap((c) => c.matched_fact_ids);
      setState({
        kind: "ready",
        documentId: resumeDocument.id,
        profileVersionId: typeof resumeDocument.profile_version_id === "string" ? resumeDocument.profile_version_id : null,
        coverage: result.coverage,
        skills: result.skills,
        facts,
        selected: new Set(initial),
        expanded: null,
        saving: false,
        saveError: null,
      });
    } catch (e) {
      setState(failureOf(e, "Failed to load"));
    } finally {
      working.current = false;
    }
  }, [applicationId]);

  // The pressed button goes when the result arrives, which would drop focus to the page body: the result takes it, unless
  // the person has already moved on to something else. It takes it without scrolling: someone who pressed, then scrolled
  // to another part of the page while the model worked, is not pulled back (a screen reader still lands on the result).
  useLayoutEffect(() => {
    if (state.kind !== "ready") return;
    const active = document.activeElement;
    if (active === null || active === document.body) resultRoot.current?.focus({ preventScroll: true });
  }, [state.kind]);

  if (state.kind === "setup") {
    return <SetupRequiredNotice notice={state.notice} />;
  }
  if (state.kind !== "ready") {
    return (
      <div className="bj-ws-tailor">
        <p className="bj-ws-intro">{IDLE_SENTENCE}</p>
        <RunButton label="Check coverage" busyLabel="Checking..." busy={state.kind === "loading"} buttonRef={checkButton} onRun={() => void check()} />
        {state.kind === "loading" && <LoadingState rows={3} rowHeight={44} label="Checking coverage..." />}
        {state.kind === "error" && <FailureState message={state.message} onRetry={() => void check()} restoreFocusTo={focusCheckButton} />}
      </div>
    );
  }

  const { documentId, coverage, skills, facts, selected, expanded, saving, saveError } = state;
  const factsById = new Map(facts.map((f) => [f.id, f]));

  async function persistSelection(next: Set<string>) {
    setState((prev) => (prev.kind === "ready" ? { ...prev, selected: next, saving: true, saveError: null } : prev));
    try {
      await apiFetch(`/resume-documents/${documentId}/evidence`, {
        method: "PATCH",
        body: JSON.stringify({ evidence_fact_ids: Array.from(next) }),
      });
      setState((prev) => (prev.kind === "ready" ? { ...prev, saving: false } : prev));
    } catch (e) {
      // A save the server refuses for setup (the profile or key went away) replaces the panel with the
      // notice and its link; any other failure keeps the panel and says the picks were not saved.
      const failure = failureOf(e, "Failed to save");
      setState((prev) => {
        if (failure.kind === "setup") return failure;
        return prev.kind === "ready" ? { ...prev, saving: false, saveError: failure.message } : prev;
      });
    }
  }

  function toggleFact(factId: string) {
    const next = new Set(selected);
    if (next.has(factId)) {
      next.delete(factId);
    } else {
      next.add(factId);
    }
    void persistSelection(next);
  }

  function toggleExpanded(name: string) {
    setState((prev) => (prev.kind === "ready" ? { ...prev, expanded: prev.expanded === name ? null : name } : prev));
  }

  const trail = tailorTrail({ clusters: coverage.length, skills: skills.length, profileVersionId: state.profileVersionId });

  return (
    <div className="bj-ws-tailor" ref={resultRoot} role="group" aria-label="Coverage result" tabIndex={-1}>
      <h4 className="bj-ws-label">Requirement clusters</h4>
      <ul className="bj-ws-clusters" aria-label="Requirement clusters">
        {coverage.map((cluster) => {
          const open = expanded === cluster.name;
          const panelId = `cluster-${cluster.name.replace(/[^a-z0-9]+/gi, "-")}`;
          return (
            <li key={cluster.name} className="bj-ws-cluster">
              <button type="button" className="bj-ws-cluster-row" aria-expanded={open} aria-controls={open ? panelId : undefined} onClick={() => toggleExpanded(cluster.name)}>
                <span className={cluster.priority === "must_have" ? "bj-ws-priority bj-ws-priority--must" : "bj-ws-priority"}>{cluster.priority === "must_have" ? "Must have" : "Preferred"}</span>
                <span className="bj-ws-cluster-name">{cluster.name}</span>
                <span className="bj-ws-cluster-count">
                  {cluster.coverage_count} {cluster.coverage_count === 1 ? "match" : "matches"}
                </span>
              </button>
              {open && (
                <div id={panelId} className="bj-ws-evidence">
                  {cluster.matched_fact_ids.length === 0 && <p className="bj-muted bj-small">No matching evidence in your profile.</p>}
                  {cluster.matched_fact_ids.map((factId) => {
                    const fact = factsById.get(factId);
                    return (
                      <label key={factId} className="bj-ws-evidence-item">
                        <input type="checkbox" checked={selected.has(factId)} onChange={() => toggleFact(factId)} />
                        {fact ? factLabel(fact) : factId}
                      </label>
                    );
                  })}
                  {cluster.matched_fact_ids.length > 0 && <p className="bj-ws-caption">Picks save here. The draft doesn't read them yet.</p>}
                </div>
              )}
            </li>
          );
        })}
      </ul>

      <h4 className="bj-ws-label">Skills</h4>
      <ul className="bj-ws-skills" aria-label="Skills and where your profile stands on each">
        {skills.map((s) => {
          const mark = SKILL_MARKS[s.state];
          const tone = mark.glyph === null ? "muted" : TRAIL_GLYPHS[mark.glyph].tone;
          return (
            <li key={s.requested_as} className={`bj-ws-skill bj-ws-skill--${s.state}`}>
              <span className={`bj-trail-glyph bj-trail-glyph--${tone}`} aria-hidden="true">
                {mark.char}
              </span>{" "}
              {s.skill}{" "}
              <span className="bj-ws-skill-state">{SKILL_STATE_LABELS[s.state]}</span>
            </li>
          );
        })}
      </ul>

      <p className="bj-ws-status-line" role="status" aria-live="polite">
        {saving ? "Saving..." : ""}
      </p>
      {saveError !== null && <FailureState message={`Couldn't save your picks. ${saveError}`} onRetry={() => void persistSelection(selected)} />}
      <BlockTrail trail={trail.trail} receipt={trail.receipt} receiptTitle={trail.receiptTitle} />
    </div>
  );
}
