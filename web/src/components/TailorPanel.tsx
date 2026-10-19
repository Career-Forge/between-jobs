import { useCallback, useEffect, useState } from "react";
import { apiFetch } from "../lib/api";
import type { ResumeDocument } from "../lib/headerComposerTypes";
import { failureOf, type SetupNotice } from "../lib/setupRequired";
import { SKILL_STATE_LABELS, type ClusterCoverage, type CoverageResponse, type SkillState } from "../lib/tailorTypes";
import { tailorTrail } from "../lib/workspaceTrails";
import { BlockTrail } from "./paper/DocumentLayout";
import { FailureState, LoadingState } from "./paper/StateViews";
import { TRAIL_GLYPHS, type TrailGlyphName } from "../lib/trail";
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
// checks them against the profile's facts. The trail under the panel says so. The coverage match is a
// model call and runs on every mount, so the page does not mount this panel while it knows no model key
// is saved (it shows the missing key instead, and fires nothing).
//
// States: loading is a placeholder in the panel's own shape; a missing profile or key (the server's
// 409) shows its notice with the link that fixes it and no Retry; any other failure of the three reads
// is a callout with Retry. A save that fails keeps the panel as it was and says the picks were not
// saved, with a Retry that sends them again -- it does not run the coverage model call a second time.

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
  | { kind: "loading" }
  | { kind: "error"; message: string }
  // No active profile, or no model key for the coverage match: the notice links to the page that fixes
  // it (no Retry).
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

export function TailorPanel({ applicationId }: { applicationId: string }) {
  const [state, setState] = useState<State>({ kind: "loading" });

  const load = useCallback(async () => {
    setState({ kind: "loading" });
    try {
      const document = await apiFetch<ResumeDocument & { selected_evidence_fact_ids?: string[] }>(`/resume-documents/mine?application_id=${applicationId}`);
      const [result, facts] = await Promise.all([
        apiFetch<CoverageResponse>(`/resume-documents/${document.id}/coverage`, { method: "POST" }),
        apiFetch<CareerFact[]>(`/profile/versions/${document.profile_version_id}/career-facts`),
      ]);
      const stored = document.selected_evidence_fact_ids ?? [];
      const initial = stored.length > 0 ? stored : result.coverage.flatMap((c) => c.matched_fact_ids);
      setState({
        kind: "ready",
        documentId: document.id,
        profileVersionId: typeof document.profile_version_id === "string" ? document.profile_version_id : null,
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
    }
  }, [applicationId]);

  useEffect(() => {
    void load();
  }, [load]);

  if (state.kind === "loading") {
    return <LoadingState rows={3} rowHeight={44} label="Loading tailor panel..." />;
  }
  if (state.kind === "setup") {
    return <SetupRequiredNotice notice={state.notice} />;
  }
  if (state.kind === "error") {
    return <FailureState message={state.message} onRetry={() => void load()} />;
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
    <div className="bj-ws-tailor">
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
              {s.skill}
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
