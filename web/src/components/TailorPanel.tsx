import { useCallback, useEffect, useState } from "react";
import { apiFetch } from "../lib/api";
import type { ResumeDocument } from "../lib/headerComposerTypes";
import {
  SKILL_STATE_LABELS,
  type ClusterCoverage,
  type CoverageResponse,
  type SkillState,
} from "../lib/tailorTypes";

// Tailor panel (Sprint 3.3e) -- Proposal §24.5.4. "Match display is
// coverage counts, never an unexplained percentage ring" -- every cluster
// shows a plain count and, on expand, the actual matched facts (real
// evidence, not a black-box score). Skills carry one of four states,
// always paired with a text label alongside color (tokens.css's own
// contract: "color never carries meaning alone").

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

function skillBadgeClass(state: SkillState): string {
  switch (state) {
    case "verified":
      return "bj-badge-emerald";
    case "supported":
      return "bj-badge-cyan";
    case "adjacent":
      return "bj-badge-gold";
    case "unsupported":
      return "bj-badge-muted";
  }
}

type State =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | {
      kind: "ready";
      documentId: string;
      coverage: ClusterCoverage[];
      skills: CoverageResponse["skills"];
      facts: CareerFact[];
      selected: Set<string>;
      expanded: string | null;
      saving: boolean;
    };

export function TailorPanel({ applicationId }: { applicationId: string }) {
  const [state, setState] = useState<State>({ kind: "loading" });

  const load = useCallback(async () => {
    setState({ kind: "loading" });
    try {
      const document = await apiFetch<ResumeDocument & { selected_evidence_fact_ids?: string[] }>(
        `/resume-documents/mine?application_id=${applicationId}`,
      );
      const [result, facts] = await Promise.all([
        apiFetch<CoverageResponse>(`/resume-documents/${document.id}/coverage`, {
          method: "POST",
        }),
        apiFetch<CareerFact[]>(`/profile/versions/${document.profile_version_id}/career-facts`),
      ]);
      const stored = document.selected_evidence_fact_ids ?? [];
      const initial =
        stored.length > 0 ? stored : result.coverage.flatMap((c) => c.matched_fact_ids);
      setState({
        kind: "ready",
        documentId: document.id,
        coverage: result.coverage,
        skills: result.skills,
        facts,
        selected: new Set(initial),
        expanded: null,
        saving: false,
      });
    } catch (e) {
      setState({ kind: "error", message: e instanceof Error ? e.message : "Failed to load" });
    }
  }, [applicationId]);

  useEffect(() => {
    void load();
  }, [load]);

  if (state.kind === "loading") {
    return <div className="bj-muted bj-small">Loading tailor panel...</div>;
  }
  if (state.kind === "error") {
    return (
      <div>
        <div className="bj-error">{state.message}</div>
        <button onClick={() => void load()}>Retry</button>
      </div>
    );
  }

  const { documentId, coverage, skills, facts, selected, expanded, saving } = state;
  const factsById = new Map(facts.map((f) => [f.id, f]));

  async function persistSelection(next: Set<string>) {
    setState((prev) => (prev.kind === "ready" ? { ...prev, selected: next, saving: true } : prev));
    try {
      await apiFetch(`/resume-documents/${documentId}/evidence`, {
        method: "PATCH",
        body: JSON.stringify({ evidence_fact_ids: Array.from(next) }),
      });
      setState((prev) => (prev.kind === "ready" ? { ...prev, saving: false } : prev));
    } catch (e) {
      setState({ kind: "error", message: e instanceof Error ? e.message : "Failed to save" });
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
    setState((prev) =>
      prev.kind === "ready" ? { ...prev, expanded: prev.expanded === name ? null : name } : prev,
    );
  }

  return (
    <div className="bj-tailor-panel">
      <h2>Tailor</h2>

      <h3>Requirement coverage</h3>
      <div className="bj-coverage-list">
        {coverage.map((cluster) => (
          <div key={cluster.name} className="bj-coverage-cluster">
            <button className="bj-coverage-row" onClick={() => toggleExpanded(cluster.name)}>
              <span
                className={
                  cluster.priority === "must_have" ? "bj-badge-danger" : "bj-badge-gold"
                }
              >
                {cluster.priority === "must_have" ? "Must have" : "Preferred"}
              </span>
              <span className="bj-coverage-name">{cluster.name}</span>
              <span className="bj-muted bj-small">
                {cluster.coverage_count} {cluster.coverage_count === 1 ? "match" : "matches"}
              </span>
            </button>
            {expanded === cluster.name && (
              <div className="bj-coverage-evidence">
                {cluster.matched_fact_ids.length === 0 && (
                  <div className="bj-muted bj-small">No matching evidence in your profile.</div>
                )}
                {cluster.matched_fact_ids.map((factId) => {
                  const fact = factsById.get(factId);
                  return (
                    <label key={factId} className="bj-evidence-item">
                      <input
                        type="checkbox"
                        checked={selected.has(factId)}
                        onChange={() => toggleFact(factId)}
                      />
                      {fact ? factLabel(fact) : factId}
                    </label>
                  );
                })}
              </div>
            )}
          </div>
        ))}
      </div>

      <h3>Skills</h3>
      <div className="bj-skill-chips">
        {skills.map((s) => (
          <span key={s.requested_as} className={`bj-skill-chip ${skillBadgeClass(s.state)}`}>
            {s.skill}
            <span className="bj-skill-chip-state">{SKILL_STATE_LABELS[s.state]}</span>
          </span>
        ))}
      </div>

      {saving && <div className="bj-muted bj-small">Saving...</div>}
    </div>
  );
}
