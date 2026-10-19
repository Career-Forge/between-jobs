import { useCallback, useEffect, useId, useState } from "react";
import { apiFetch } from "../lib/api";
import type {
  BulletStyleOverride,
  Density,
  PageCountOverride,
  ResumeDocument,
  ShapeOverrides,
  SummaryMode,
} from "../lib/headerComposerTypes";
import { failedBlockTrail } from "../lib/profileTrails";
import { REGION_OPTIONS } from "../lib/regionOptions";
import { failureOf, type SetupNotice } from "../lib/setupRequired";
import { SHAPE_PRESETS } from "../lib/shapePresets";
import { BlockTrail } from "./paper/DocumentLayout";
import { FailureState, LoadingState } from "./paper/StateViews";
import { RadioSegments } from "./RadioSegments";
import { SetupRequiredNotice } from "./SetupRequiredNotice";
import "../styles/profile-studio.css";

// Resume settings -- page count, density, summary, bullet style, region, show GPA. Same load/persist
// pattern as HeaderComposer/SectionOrderEditor: `applicationId` omitted -> the master/default document,
// provided -> that application's overrides. Every field left at "Auto"/unset here means "inherit"
// (per-application -> master -> system default, see shape_overrides.py) -- this panel never sends a
// field's literal system-default value just because that's what's currently in effect, only what the
// user actually picked.
//
// The short choices are radio groups drawn as outlined segments (RadioSegments); region has a long list
// and stays a select. `showTrail` draws the red-cross trail line when the read or the save failed (off
// by default, so an embedder that draws its own is unchanged); settings have nothing to attribute
// otherwise.

type State =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  // No active profile yet: the notice links to the page that fixes it (no Retry).
  | { kind: "setup"; notice: SetupNotice }
  | { kind: "ready"; documentId: string; overrides: ShapeOverrides; saving: boolean };

const PAGE_COUNT_OPTIONS: readonly { value: PageCountOverride; label: string }[] = [
  { value: "auto", label: "Auto (by region/tier)" },
  { value: "1", label: "1 page" },
  { value: "2", label: "2 pages" },
];
const DENSITY_OPTIONS: readonly { value: Density; label: string }[] = [
  { value: "balanced", label: "Balanced" },
  { value: "compact", label: "Compact" },
  { value: "spacious", label: "Spacious" },
];
const SUMMARY_OPTIONS: readonly { value: SummaryMode; label: string }[] = [
  { value: "off", label: "Off" },
  { value: "on", label: "On" },
  { value: "auto", label: "Auto (let the model decide)" },
];
const BULLET_OPTIONS: readonly { value: BulletStyleOverride; label: string }[] = [
  { value: "plain", label: "Plain" },
  { value: "bold_lead_in", label: "Bold keyword lead-in" },
];

export function ShapeSettingsPanel({ applicationId, showTrail = false }: { applicationId?: string; showTrail?: boolean }) {
  const [state, setState] = useState<State>({ kind: "loading" });
  const ids = { pageCount: useId(), density: useId(), summary: useId(), bullets: useId(), region: useId() };

  const load = useCallback(async () => {
    setState({ kind: "loading" });
    try {
      const path = applicationId ? `/resume-documents/mine?application_id=${applicationId}` : "/resume-documents/mine";
      const document = await apiFetch<ResumeDocument>(path);
      setState({
        kind: "ready",
        documentId: document.id,
        overrides: document.shape_overrides ?? {},
        saving: false,
      });
    } catch (e) {
      setState(failureOf(e, "Failed to load"));
    }
  }, [applicationId]);

  useEffect(() => {
    void load();
  }, [load]);

  async function persist(documentId: string, overrides: ShapeOverrides) {
    setState({ kind: "ready", documentId, overrides, saving: true });
    try {
      await apiFetch(`/resume-documents/${documentId}/shape`, {
        method: "PATCH",
        body: JSON.stringify({ shape_overrides: overrides }),
      });
      setState({ kind: "ready", documentId, overrides, saving: false });
    } catch (e) {
      setState(failureOf(e, "Failed to save"));
    }
  }

  if (state.kind === "loading") {
    return <LoadingState rows={4} rowHeight={48} label="Loading resume settings..." />;
  }
  if (state.kind === "setup") {
    return <SetupRequiredNotice notice={state.notice} />;
  }
  if (state.kind === "error") {
    const failed = failedBlockTrail(state.message);
    return (
      <div>
        <FailureState message={state.message} onRetry={() => void load()} />
        {showTrail && <BlockTrail trail={failed.trail} />}
      </div>
    );
  }

  const { documentId, overrides, saving } = state;

  function set<K extends keyof ShapeOverrides>(key: K, value: ShapeOverrides[K]) {
    void persist(documentId, { ...overrides, [key]: value });
  }

  return (
    <div className="bj-shape-settings">
      <div className="bj-shape-presets" role="group" aria-label="Starting points">
        {SHAPE_PRESETS.map((preset) => (
          <button key={preset.key} type="button" className="bj-shape-preset" onClick={() => void persist(documentId, { ...preset.overrides })}>
            <span className="bj-shape-preset-label">{preset.label}</span>
            <span className="bj-shape-preset-note">{preset.description}</span>
          </button>
        ))}
      </div>

      <div className="bj-shape-settings-row">
        <span id={ids.pageCount} className="bj-header-composer-field">
          Page count
        </span>
        <RadioSegments labelledBy={ids.pageCount} value={overrides.page_count ?? "auto"} options={PAGE_COUNT_OPTIONS} onChange={(value) => set("page_count", value)} />
      </div>

      <div className="bj-shape-settings-row">
        <span id={ids.density} className="bj-header-composer-field">
          Density
        </span>
        <RadioSegments labelledBy={ids.density} value={overrides.density ?? "balanced"} options={DENSITY_OPTIONS} onChange={(value) => set("density", value)} />
      </div>

      <div className="bj-shape-settings-row">
        <span id={ids.summary} className="bj-header-composer-field">
          Summary
        </span>
        <RadioSegments labelledBy={ids.summary} value={overrides.summary ?? "off"} options={SUMMARY_OPTIONS} onChange={(value) => set("summary", value)} />
      </div>

      <div className="bj-shape-settings-row">
        <span id={ids.bullets} className="bj-header-composer-field">
          Bullet style
        </span>
        <RadioSegments labelledBy={ids.bullets} value={overrides.bullet_style ?? "plain"} options={BULLET_OPTIONS} onChange={(value) => set("bullet_style", value)} />
      </div>

      <div className="bj-shape-settings-row">
        <label htmlFor={ids.region} className="bj-header-composer-field">
          Region
        </label>
        <select id={ids.region} value={overrides.region ?? ""} onChange={(e) => set("region", e.target.value === "" ? null : e.target.value)}>
          <option value="">Auto-detect from the job posting</option>
          {REGION_OPTIONS.map((r) => (
            <option key={r.code} value={r.code}>
              {r.label}
            </option>
          ))}
        </select>
      </div>

      <div className="bj-shape-settings-row">
        <label className="bj-section-editor-toggle">
          <input type="checkbox" checked={overrides.show_gpa ?? false} onChange={(e) => set("show_gpa", e.target.checked)} />
          Show GPA
        </label>
      </div>

      <div className="bj-shape-settings-row bj-shape-settings-row-stacked">
        <label className="bj-section-editor-toggle">
          <input type="checkbox" checked={overrides.show_nationality ?? false} onChange={(e) => set("show_nationality", e.target.checked)} />
          Show nationality
        </label>
        <span className="bj-muted bj-small">
          Only rendered for employers whose country expects it (e.g. Germany, Austria) -- hidden everywhere else even when this is on.
        </span>
      </div>

      <p className="bj-muted bj-small bj-studio-saving" role="status">
        {saving ? "Saving..." : ""}
      </p>
    </div>
  );
}
