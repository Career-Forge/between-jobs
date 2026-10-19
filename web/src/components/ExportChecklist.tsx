import { useEffect, useRef, useState } from "react";
import type { ChecklistItem } from "../lib/generateTypes";
import type { ChecklistState } from "../lib/useGeneration";
import { checklistSummary } from "../lib/checklistSummary";
import { checklistTrail } from "../lib/workspaceTrails";
import { BlockTrail } from "./paper/DocumentLayout";
import { ChecklistGroup, ChecklistSummary, type ChecklistRowProps } from "./paper/ChecklistRow";
import { FailureState, LoadingState } from "./paper/StateViews";
import "../styles/workspace.css";

// The export checks: what Between Jobs could check on the compiled PDF, and what is left for the person.
//
// Two groups, counted in their headings. "Checked by Between Jobs" holds what code ran, as ✓ Pass or
// ✕ Fail. "Yours to check" holds what nothing here checked, as ○ Not checked with the way to check it
// yourself -- links work, text can be selected, fonts are embedded, no repeated bullets, and the claim
// check when the engine that wrote the resume does not run one. Not checked is never a pass and is
// never counted as one: the summary is counts only ("5 pass · 1 fail · 4 not checked").
//
// The checks recompile the resume's LaTeX on every run, so they are never run on mount and never called
// free: a person asks for them, and asks again to re-run them. The result belongs to the generation it
// was run for; a new generation clears it.
//
// A row in "Yours to check" has an "I checked this" button. It is the person's own marker for this visit
// and nothing more: it is not saved, it is not counted in the summary, and nothing reads it -- Between
// Jobs has no record that anything was reviewed, so it does not pretend to have one. It uses the shared
// selection style (a maroon fill with aria-pressed). Nothing here submits or sends anything.
//
// The marks belong to the PDF they were made on: when the checks go back to idle (a new generation) they
// are cleared, so a mark made on draft 1 never reads as "I checked this" on draft 2's rows. A button
// keeps one name whatever its state ("I checked this", with the row's own label read after it, since four
// buttons with one name cannot be told apart in a list of controls); aria-pressed carries the state and the
// bullet is only a second cue for the eye.

const BETWEEN_JOBS = "Checked by Between Jobs";
const YOURS = "Yours to check";
const ONE_PAGE_CAVEAT = "This check assumes one page; your settings allow two.";

type Row = ChecklistRowProps & { id: string };

// The one-page check is naive about the person's own page allowance; when the shape check, which knows
// it, passes while the one-page check fails, the failure is the assumption, and the row says so.
function caveatFor(item: ChecklistItem, items: readonly ChecklistItem[]): string | undefined {
  if (item.key !== "one_page" || item.status !== "fail") return undefined;
  return items.some((other) => other.key === "page_count_within_shape" && other.status === "pass") ? ONE_PAGE_CAVEAT : undefined;
}

export function ExportChecklist({ checklist, onRun }: { checklist: ChecklistState; onRun: () => void }) {
  const [ticked, setTicked] = useState<ReadonlySet<string>>(new Set());
  const loading = checklist.kind === "loading";

  useEffect(() => {
    if (checklist.kind === "idle") setTicked((current) => (current.size === 0 ? current : new Set()));
  }, [checklist.kind]);

  const toggle = (key: string) =>
    setTicked((current) => {
      const next = new Set(current);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });

  // The placeholder rows, and then the checks, are drawn ABOVE the button that was pressed, and a browser
  // does not follow a focused control down the page: a tall list leaves the focus ring below the screen.
  // Whenever the state changes with the keyboard still on the button, bring the button back into view.
  const runButton = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    const button = runButton.current;
    if (button !== null && document.activeElement === button) button.scrollIntoView({ block: "nearest" });
  }, [checklist.kind]);

  const run = (
    <button ref={runButton} type="button" aria-disabled={loading ? true : undefined} onClick={() => !loading && onRun()}>
      {loading ? "Checking..." : checklist.kind === "ready" ? "Check again" : checklist.kind === "error" ? "Retry" : "Run the export checks"}
    </button>
  );

  // One button, in one place in the tree whatever the state: Run, Checking..., Check again, Retry. If it were
  // rebuilt for each state a keyboard user's focus would be dropped to the page when they pressed it.
  const ready = checklist.kind === "ready" ? checklist.items : null;

  return (
    <div className="bj-ws-export">
      <h3 className="bj-ws-block-title">Export checks</h3>
      {checklist.kind === "idle" && (
        <p className="bj-small bj-muted">
          Checks the compiled PDF for page count, warnings, claim flags and fit to your settings. It recompiles your resume, so it runs only when you ask.
        </p>
      )}
      {checklist.kind === "loading" && <LoadingState rows={3} rowHeight={40} label="Checking the PDF..." />}
      {checklist.kind === "error" && <FailureState message={checklist.message} />}
      {ready !== null && <ReadyChecklist items={ready} ticked={ticked} onToggle={toggle} />}
      <div className="bj-actions">{run}</div>
      {ready !== null && <ChecklistTrail />}
      <div className="bj-visually-hidden" role="status" aria-live="polite">
        {ready === null ? "" : `Export checks finished: ${checklistSummary(ready.map((item) => item.status))}.`}
      </div>
    </div>
  );
}

function ReadyChecklist({ items, ticked, onToggle }: { items: ChecklistItem[]; ticked: ReadonlySet<string>; onToggle: (key: string) => void }) {
  const checked: Row[] = items
    .filter((item) => item.status !== "not_checked")
    .map((item) => ({ id: item.key, state: item.status, label: item.label, hint: item.detail, caveat: caveatFor(item, items) }));
  const yours: Row[] = items
    .filter((item) => item.status === "not_checked")
    .map((item) => ({
      id: item.key,
      state: "not_checked" as const,
      label: item.label,
      hint: item.detail,
      actions: (
        <button type="button" className="bj-selectable" aria-pressed={ticked.has(item.key)} onClick={() => onToggle(item.key)}>
          {ticked.has(item.key) && <span aria-hidden="true">● </span>}
          I checked this<span className="bj-visually-hidden">: {item.label}</span>
        </button>
      ),
    }));
  return (
    <>
      <ChecklistSummary states={items.map((item) => item.status)} />
      {checked.length > 0 && <ChecklistGroup title={BETWEEN_JOBS} rows={checked} level={4} />}
      {yours.length > 0 && (
        <>
          <ChecklistGroup title={YOURS} rows={yours} level={4} />
          <p className="bj-ws-caption">Your marks are for you while this page is open. They are not saved, and Between Jobs does not count them as checked.</p>
        </>
      )}
    </>
  );
}

function ChecklistTrail() {
  const trail = checklistTrail();
  return <BlockTrail trail={trail.trail} receipt={trail.receipt} receiptTitle={trail.receiptTitle} />;
}
