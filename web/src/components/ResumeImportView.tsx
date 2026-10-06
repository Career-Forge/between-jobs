import type { ChangeEvent, Ref } from "react";
import { highlightSegments } from "../lib/highlightSegments";
import { IMPORT_IDS, canChooseFile, type ImportState } from "../lib/resumeImport";
import type { ImportDraft } from "../lib/resumeImportDraft";
import { DOCX_CONTENT_TYPE, MAX_IMPORT_BYTES, PDF_CONTENT_TYPE } from "../lib/resumeImportFile";
import {
  assumptionLines,
  buildReview,
  droppedLines,
  type ReviewField,
  type ReviewSection,
} from "../lib/resumeImportReview";
import type { Failure } from "../lib/setupRequired";
import { SetupRequiredNotice } from "./SetupRequiredNotice";

// The card that imports a resume from a PDF or DOCX, as a function of its props (the same split as
// AccountCardView): no hooks, so a test can render it or call it and press its buttons. The state,
// the requests and the focus are components/ResumeImportCard.tsx and lib/resumeImport.ts.
//
// WHAT THIS SCREEN IS FOR. A model read the file, and a model can misread it. So the draft is shown
// value by value, each with a button that opens the text of the file with that value marked, and what
// the model proposed that the check against the file did not keep is listed apart, as left out, each
// with its own reason (some are not in the file; others may be, but cannot be used). Nothing is used
// until the person presses "Use this profile" and then confirms; "Discard this draft" removes it.
//
// ACCESSIBLE BY CONSTRUCTION. A labelled region with real headings; every control is a button
// (never a clickable div) whose name says what it acts on; a source toggle says whether it is open
// (`aria-expanded`) and is named by its value ("Show where Email 1 came from"); the marked text is
// a <mark> carrying visually hidden words that say where the value starts and ends, so a screen
// reader hears the marking that a sighted person sees; the scrollable text can be reached with the
// keyboard; every state change that removes the control just used has somewhere for focus to go
// (lib/resumeImport.ts: IMPORT_IDS, which this view draws with tabIndex -1).

export const IMPORT_TITLE = "Import from a resume file (PDF or DOCX)";

// What is sent and what is kept, in the words of the privacy policy's "Importing a resume file".
export const IMPORT_PRIVACY_STATEMENT =
  "The text of your file is sent to your own AI provider, using the model key you saved in Integrations, so a model can fill in a draft profile for you to review. We do not keep the file itself.";

export const IMPORT_LIMITS = `PDF or DOCX, up to ${MAX_IMPORT_BYTES / (1024 * 1024)} MiB.`;

export const REVIEW_TITLE = "Check what was read from your file";
export const REVIEW_LABEL = "Read from your file by an AI model: check every field before you use it";
export const REVIEW_DETAIL =
  "Each value was checked against the text of your file, which shows it appears there, not that it is in the right place. This draft is saved but not in use: nothing changes until you press \"Use this profile\".";
export const ALREADY_ACTIVE_NOTICE = "This matches your current profile: nothing to change.";
// The heading and the intro are about every reason a value is left out, so they claim none of them:
// "not in your file" is one reason among several (a work arrangement given as a city, or a date of
// birth, may well be in the file and is still not kept). Each line says its own.
export const LEFT_EMPTY_TITLE = "Left out of the draft";
export const LEFT_EMPTY_INTRO =
  "A model proposed these, but the check against your file did not keep them, so they are not in the draft and nothing was made up to fill them. Each line says why: some are not in your file; others may be in it but are not usable for that field, or are never read from a file. Entry and item numbers are the model's own and can differ from the list above.";
export const LEAVE_NOTE =
  "If you leave this page without choosing, the draft stays saved but is not used. This page does not list saved drafts, so to review it again, upload the file again.";
// Shown, with the file button off, while the JSON import below is being sent: the two imports do not
// run at once (see `importInProgress`).
export const BLOCKED_NOTE = "A JSON import below is in progress. Choose a file when it has finished.";
export const USE_LABEL = "Use this profile";
export const DISCARD_LABEL = "Discard this draft";
export const IMPORT_MARK_ID = "bj-import-mark";

const ACCEPT = `.pdf,.docx,${PDF_CONTENT_TYPE},${DOCX_CONTENT_TYPE}`;
const FILE_INPUT_ID = "bj-import-file";
const TITLE_ID = "bj-import-title";

export interface ResumeImportActions {
  chooseFile: () => void;
  fileChosen: (files: FileList | null) => void;
  retry: () => void;
  close: () => void;
  toggleSource: (path: string) => void;
  askUse: () => void;
  cancelUse: () => void;
  confirmUse: () => void;
  discard: () => void;
}

function sourcePanelId(path: string): string {
  return `bj-import-source-${encodeURIComponent(path)}`;
}

function FailureView({ failure, id }: { failure: Failure; id: string }) {
  return (
    <div id={id} tabIndex={-1} className="bj-import-failure">
      {failure.kind === "setup" ? (
        <SetupRequiredNotice notice={failure.notice} />
      ) : (
        <div className="bj-error" role="alert">
          {failure.message}
        </div>
      )}
    </div>
  );
}

// ── the card ───────────────────────────────────────────────────────────────

export function ResumeImportView({
  state,
  replacesCurrent,
  fileInputRef,
  actions,
  blocked = false,
}: {
  state: ImportState;
  // There is an active profile this draft would take the place of.
  replacesCurrent: boolean;
  fileInputRef?: Ref<HTMLInputElement>;
  actions: ResumeImportActions;
  // The page is busy with the other import: a file cannot be chosen now.
  blocked?: boolean;
}) {
  return (
    <section className="bj-card bj-import" aria-labelledby={TITLE_ID}>
      <div className="bj-card-header">
        <h2 id={TITLE_ID}>{IMPORT_TITLE}</h2>
        <span className="bj-badge-violet">Read by an AI model</span>
      </div>

      {state.kind === "review" || state.kind === "activating" || state.kind === "discarding" ? (
        <ReviewPanel state={state} replacesCurrent={replacesCurrent} actions={actions} />
      ) : (
        <UploadPanel state={state} fileInputRef={fileInputRef} actions={actions} blocked={blocked} />
      )}
    </section>
  );
}

// ── choosing a file ────────────────────────────────────────────────────────

function UploadPanel({
  state,
  fileInputRef,
  actions,
  blocked,
}: {
  state: ImportState;
  fileInputRef?: Ref<HTMLInputElement>;
  actions: ResumeImportActions;
  blocked: boolean;
}) {
  const busy = state.kind === "reading" || state.kind === "uploading";
  return (
    <div>
      <p className="bj-muted">{IMPORT_PRIVACY_STATEMENT}</p>
      <p className="bj-muted bj-small">{IMPORT_LIMITS}</p>

      {state.kind === "activated" && (
        <div id={IMPORT_IDS.notice} tabIndex={-1} role="status" className="bj-import-notice">
          <strong>Your profile now comes from your file.</strong>{" "}
          {state.replacedCurrent
            ? "It replaced your previous profile as the active one, and that version stays in your history."
            : "It is your active profile."}
          <div className="bj-actions">
            <button type="button" onClick={actions.close}>
              Dismiss
            </button>
          </div>
        </div>
      )}
      {state.kind === "discarded" && (
        <div id={IMPORT_IDS.notice} tabIndex={-1} role="status" className="bj-import-notice">
          {state.keptInHistory
            ? "That draft matches a version you used before, so it stays in your history. Nothing was changed."
            : "Draft discarded. Nothing was changed."}
          <div className="bj-actions">
            <button type="button" onClick={actions.close}>
              Dismiss
            </button>
          </div>
        </div>
      )}

      {busy && (
        <p id={IMPORT_IDS.status} tabIndex={-1} role="status" className="bj-muted">
          {state.kind === "reading"
            ? `Reading "${state.fileName}"...`
            : `Reading "${state.fileName}" with your AI model. This can take a little while.`}
        </p>
      )}

      {state.kind === "error" && (
        <>
          <FailureView failure={state.failure} id={IMPORT_IDS.error} />
          <div className="bj-actions">
            {state.retry && (
              <button type="button" className="bj-primary" onClick={actions.retry}>
                Try again
              </button>
            )}
            <button type="button" onClick={actions.close}>
              Dismiss
            </button>
          </div>
        </>
      )}

      <div className="bj-actions">
        <button
          type="button"
          id={IMPORT_IDS.choose}
          className={state.kind === "idle" ? "bj-primary" : undefined}
          onClick={actions.chooseFile}
          disabled={!canChooseFile(state) || blocked}
        >
          {state.kind === "error" ? "Choose a different file" : "Choose a file"}
        </button>
        <input
          ref={fileInputRef}
          id={FILE_INPUT_ID}
          type="file"
          accept={ACCEPT}
          style={{ display: "none" }}
          tabIndex={-1}
          onChange={(event: ChangeEvent<HTMLInputElement>) => actions.fileChosen(event.target.files)}
        />
      </div>
      {blocked && canChooseFile(state) && (
        <p role="status" className="bj-muted bj-small">
          {BLOCKED_NOTE}
        </p>
      )}
    </div>
  );
}

// ── the review ─────────────────────────────────────────────────────────────

function documentLine(draft: ImportDraft, fileName: string): string {
  const { kind, pagesRead, pagesTotal, truncated } = draft.document;
  const parts = [`Read from "${fileName}"`];
  if (kind === "pdf" || kind === "docx") parts.push(kind.toUpperCase());
  if (pagesRead !== null && pagesTotal !== null) {
    parts.push(pagesRead === pagesTotal ? `${pagesTotal} ${pagesTotal === 1 ? "page" : "pages"}` : `${pagesRead} of ${pagesTotal} pages`);
  }
  const line = parts.join(" -- ") + ".";
  return truncated ? `${line} Part of the file was not read, so values from the cut part are missing.` : line;
}

function countsLine(draft: ImportDraft): string | null {
  if (draft.keptFields === null || draft.droppedFields === null) return null;
  const kept = `${draft.keptFields} ${draft.keptFields === 1 ? "value was" : "values were"} found in your file and kept`;
  const left = `${draft.droppedFields} proposed ${draft.droppedFields === 1 ? "value was" : "values were"} left out`;
  return `${kept}; ${left}.`;
}

function ReviewPanel({
  state,
  replacesCurrent,
  actions,
}: {
  state: Extract<ImportState, { kind: "review" | "activating" | "discarding" }>;
  replacesCurrent: boolean;
  actions: ResumeImportActions;
}) {
  const { draft } = state;
  const busy = state.kind !== "review";
  const confirming = state.kind === "review" && state.confirming;
  const sections = buildReview(draft.profile, draft.spans, draft.assumptions);
  const counts = countsLine(draft);

  return (
    <div>
      <h3 id={IMPORT_IDS.review} tabIndex={-1}>
        {REVIEW_TITLE}
      </h3>
      <div className="bj-import-warning" role="note">
        <strong>{REVIEW_LABEL}.</strong> {REVIEW_DETAIL}
      </div>
      <p className="bj-muted bj-small">{documentLine(draft, state.fileName)}</p>
      {counts !== null && <p className="bj-muted bj-small">{counts}</p>}

      {draft.alreadyActive && (
        <div className="bj-import-notice" role="status">
          {ALREADY_ACTIVE_NOTICE}
        </div>
      )}

      {draft.warnings.length > 0 && (
        <div className="bj-warnings" role="group" aria-label="Notes from reading your file">
          {draft.warnings.map((warning, index) => (
            <div key={`${index}-${warning}`}>Note: {warning}</div>
          ))}
        </div>
      )}

      <AssumptionList draft={draft} />

      <div>
        {sections.map((section) => (
          <SectionView
            key={section.id}
            section={section}
            draft={draft}
            openPath={state.openPath}
            locked={busy}
            toggleSource={actions.toggleSource}
          />
        ))}
      </div>

      <DroppedList draft={draft} />

      {state.kind === "review" && state.problem !== null && (
        <FailureView failure={state.problem} id={IMPORT_IDS.problem} />
      )}
      {busy && (
        <p id={IMPORT_IDS.status} tabIndex={-1} role="status" className="bj-muted">
          {state.kind === "activating" ? "Switching to this profile..." : "Discarding the draft..."}
        </p>
      )}

      {draft.alreadyActive ? (
        <div className="bj-actions">
          <button type="button" id={IMPORT_IDS.closeReview} className="bj-primary" onClick={actions.close}>
            Done
          </button>
        </div>
      ) : (
        <>
          {confirming && (
            <div id={IMPORT_IDS.confirm} tabIndex={-1} role="group" aria-labelledby="bj-import-confirm-text" className="bj-import-confirm">
              <p id="bj-import-confirm-text">
                {replacesCurrent
                  ? "Use this profile in place of your current one? It becomes your active profile, and your current version stays in your history."
                  : "Use this profile? It becomes your active profile."}
              </p>
              <div className="bj-actions">
                <button type="button" className="bj-primary" onClick={actions.confirmUse} disabled={busy}>
                  Yes, use this profile
                </button>
                <button type="button" onClick={actions.cancelUse} disabled={busy}>
                  Not yet
                </button>
              </div>
            </div>
          )}
          <div className="bj-actions">
            {!confirming && (
              <button
                type="button"
                id={IMPORT_IDS.use}
                className="bj-primary"
                onClick={actions.askUse}
                disabled={busy}
              >
                {USE_LABEL}
              </button>
            )}
            <button type="button" onClick={actions.discard} disabled={busy}>
              {DISCARD_LABEL}
            </button>
          </div>
          <p className="bj-muted bj-small">{LEAVE_NOTE}</p>
        </>
      )}
    </div>
  );
}

function AssumptionList({ draft }: { draft: ImportDraft }) {
  const lines = assumptionLines(draft.assumptions);
  if (lines.length === 0) return null;
  return (
    <div className="bj-import-assumptions" role="group" aria-label="Things assumed while reading your file">
      <strong>Assumed while reading your file</strong>
      <ul>
        {lines.map((line, index) => (
          <li key={`${index}-${line.where}`}>
            {line.where}: {line.value}. {line.note}
          </li>
        ))}
      </ul>
      <p className="bj-muted bj-small">
        Entry numbers here are the model&apos;s own and can differ from the lists on this page.
      </p>
    </div>
  );
}

// ── the values ─────────────────────────────────────────────────────────────

function SectionView({
  section,
  draft,
  openPath,
  locked,
  toggleSource,
}: {
  section: ReviewSection;
  draft: ImportDraft;
  openPath: string | null;
  // A decision is being sent: the draft is not to be changed on screen meanwhile.
  locked: boolean;
  toggleSource: (path: string) => void;
}) {
  const titleId = `bj-import-section-${section.id}`;
  return (
    <section aria-labelledby={titleId} className="bj-import-section">
      <h4 id={titleId}>{section.title}</h4>
      {section.blocks.map((block) => (
        <div key={block.id} className="bj-import-block">
          {block.heading !== null && <h5>{block.heading}</h5>}
          <dl className="bj-import-fields">
            {block.fields.map((field) => (
              <FieldView
                key={field.path}
                field={field}
                draft={draft}
                open={openPath === field.path}
                locked={locked}
                toggleSource={toggleSource}
              />
            ))}
          </dl>
        </div>
      ))}
    </section>
  );
}

function FieldView({
  field,
  draft,
  open,
  locked,
  toggleSource,
}: {
  field: ReviewField;
  draft: ImportDraft;
  open: boolean;
  locked: boolean;
  toggleSource: (path: string) => void;
}) {
  const panelId = sourcePanelId(field.path);
  return (
    <div className="bj-import-field">
      <dt>{field.label}</dt>
      <dd>
        <span>{field.value}</span>
        {field.derived ? (
          <div className="bj-muted bj-small">Set by a rule from other values, not read from your file.</div>
        ) : field.span !== null ? (
          <button
            type="button"
            className="bj-import-toggle"
            aria-expanded={open}
            aria-controls={open ? panelId : undefined}
            disabled={locked}
            onClick={() => toggleSource(field.path)}
          >
            {open ? "Hide where this came from" : "Show where this came from"}
            <span className="bj-visually-hidden"> for {field.label}</span>
          </button>
        ) : (
          <div className="bj-muted bj-small">
            {draft.spansKnown
              ? "No place in your file was found for this value."
              : "Where this came from is not available."}
          </div>
        )}
        {field.assumption !== null && (
          <div className="bj-import-assumed bj-small">Assumed: {field.assumption}</div>
        )}
        {open && field.span !== null && <SourcePanel field={field} text={draft.extractedText} panelId={panelId} />}
      </dd>
    </div>
  );
}

// The text read from the file, with the part this value came from marked. The marking is a <mark>
// element (which a screen reader can announce) holding visually hidden words that say where the value
// starts and ends, since a highlight that only shows as colour would not be there for everyone.
function SourcePanel({ field, text, panelId }: { field: ReviewField; text: string; panelId: string }) {
  const segments = highlightSegments(text, field.span === null ? [] : [field.span]);
  const marked = segments.some((segment) => segment.highlighted);
  return (
    <div id={panelId} className="bj-import-source">
      <p className="bj-muted bj-small">
        {marked
          ? `The text read from your file, with the part that ${field.label} came from marked.`
          : `The place given for ${field.label} is not in the text read from your file, so nothing is marked.`}
      </p>
      <div role="region" aria-label="Text read from your file" tabIndex={0} className="bj-import-text">
        <pre>
          {segments.map((segment, index) =>
            segment.highlighted ? (
              <mark key={index} id={IMPORT_MARK_ID} className="bj-import-mark">
                <span className="bj-visually-hidden">Start of the part this value came from: </span>
                {segment.text}
                <span className="bj-visually-hidden"> End of that part.</span>
              </mark>
            ) : (
              <span key={index}>{segment.text}</span>
            ),
          )}
        </pre>
      </div>
    </div>
  );
}

// ── what the model proposed that the check did not keep ────────────────────

function DroppedList({ draft }: { draft: ImportDraft }) {
  const lines = droppedLines(draft.dropped);
  return (
    <section aria-labelledby="bj-import-dropped-title" className="bj-import-section bj-import-dropped">
      <h4 id="bj-import-dropped-title">{LEFT_EMPTY_TITLE}</h4>
      {lines.length === 0 ? (
        <p className="bj-muted bj-small">Nothing the model proposed was left out.</p>
      ) : (
        <>
          <p className="bj-muted bj-small">{LEFT_EMPTY_INTRO}</p>
          <ul>
            {lines.map((line, index) => (
              <li key={`${index}-${line.where}`}>
                <strong>{line.where}.</strong> {line.why}
                {line.detail !== null && <span className="bj-muted bj-small"> ({line.detail})</span>}
                {line.proposed !== null && (
                  <div className="bj-muted bj-small">The model proposed: &ldquo;{line.proposed}&rdquo;</div>
                )}
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}
