import { useCallback, useEffect, useState } from "react";
import { apiFetch } from "../lib/api";
import type {
  BulletStyleOverride,
  Density,
  PageCountOverride,
  ResumeDocument,
  ShapeOverrides,
  SummaryMode,
} from "../lib/headerComposerTypes";
import { REGION_OPTIONS } from "../lib/regionOptions";
import { SHAPE_PRESETS } from "../lib/shapePresets";

// Resume settings (R6, resumeforge-shape-and-fit.md) -- page count,
// density, summary, bullet style, region, show GPA. Same load/persist
// pattern as HeaderComposer/SectionOrderEditor: `applicationId` omitted ->
// the master/default document, provided -> that application's overrides.
// Every field left at "Auto"/unset here means "inherit" (per-application
// -> master -> system default, see shape_overrides.py) -- this panel
// never sends a field's literal system-default value just because that's
// what's currently in effect, only what the user actually picked.

type State =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ready"; documentId: string; overrides: ShapeOverrides; saving: boolean };

export function ShapeSettingsPanel({ applicationId }: { applicationId?: string }) {
  const [state, setState] = useState<State>({ kind: "loading" });

  const load = useCallback(async () => {
    setState({ kind: "loading" });
    try {
      const path = applicationId
        ? `/resume-documents/mine?application_id=${applicationId}`
        : "/resume-documents/mine";
      const document = await apiFetch<ResumeDocument>(path);
      setState({
        kind: "ready",
        documentId: document.id,
        overrides: document.shape_overrides ?? {},
        saving: false,
      });
    } catch (e) {
      setState({ kind: "error", message: e instanceof Error ? e.message : "Failed to load" });
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
      setState({ kind: "error", message: e instanceof Error ? e.message : "Failed to save" });
    }
  }

  if (state.kind === "loading") {
    return <div className="bj-muted bj-small">Loading resume settings...</div>;
  }
  if (state.kind === "error") {
    return (
      <div>
        <div className="bj-error">{state.message}</div>
        <button onClick={() => void load()}>Retry</button>
      </div>
    );
  }

  const { documentId, overrides, saving } = state;

  function set<K extends keyof ShapeOverrides>(key: K, value: ShapeOverrides[K]) {
    void persist(documentId, { ...overrides, [key]: value });
  }

  return (
    <div className="bj-shape-settings">
      <div className="bj-shape-presets">
        {SHAPE_PRESETS.map((preset) => (
          <button
            key={preset.key}
            type="button"
            title={preset.description}
            onClick={() => void persist(documentId, { ...preset.overrides })}
          >
            {preset.label}
          </button>
        ))}
      </div>

      <div className="bj-shape-settings-row">
        <span className="bj-header-composer-field">Page count</span>
        <select
          value={overrides.page_count ?? "auto"}
          onChange={(e) => set("page_count", e.target.value as PageCountOverride)}
        >
          <option value="auto">Auto (by region/tier)</option>
          <option value="1">1 page</option>
          <option value="2">2 pages</option>
        </select>
      </div>

      <div className="bj-shape-settings-row">
        <span className="bj-header-composer-field">Density</span>
        <select
          value={overrides.density ?? "balanced"}
          onChange={(e) => set("density", e.target.value as Density)}
        >
          <option value="balanced">Balanced</option>
          <option value="compact">Compact</option>
          <option value="spacious">Spacious</option>
        </select>
      </div>

      <div className="bj-shape-settings-row">
        <span className="bj-header-composer-field">Summary</span>
        <select
          value={overrides.summary ?? "off"}
          onChange={(e) => set("summary", e.target.value as SummaryMode)}
        >
          <option value="off">Off</option>
          <option value="on">On</option>
          <option value="auto">Auto (let the model decide)</option>
        </select>
      </div>

      <div className="bj-shape-settings-row">
        <span className="bj-header-composer-field">Bullet style</span>
        <select
          value={overrides.bullet_style ?? "plain"}
          onChange={(e) => set("bullet_style", e.target.value as BulletStyleOverride)}
        >
          <option value="plain">Plain</option>
          <option value="bold_lead_in">Bold keyword lead-in</option>
        </select>
      </div>

      <div className="bj-shape-settings-row">
        <span className="bj-header-composer-field">Region</span>
        <select
          value={overrides.region ?? ""}
          onChange={(e) => set("region", e.target.value === "" ? null : e.target.value)}
        >
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
          <input
            type="checkbox"
            checked={overrides.show_gpa ?? false}
            onChange={(e) => set("show_gpa", e.target.checked)}
          />
          Show GPA
        </label>
      </div>

      <div className="bj-shape-settings-row bj-shape-settings-row-stacked">
        <label className="bj-section-editor-toggle">
          <input
            type="checkbox"
            checked={overrides.show_nationality ?? false}
            onChange={(e) => set("show_nationality", e.target.checked)}
          />
          Show nationality
        </label>
        <span className="bj-muted bj-small">
          Only rendered for employers whose country expects it (e.g. Germany, Austria) --
          hidden everywhere else even when this is on.
        </span>
      </div>

      {saving && <div className="bj-muted bj-small">Saving...</div>}
    </div>
  );
}
