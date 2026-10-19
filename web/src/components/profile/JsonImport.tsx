import { useRef, useState } from "react";
import { downloadJson } from "../../lib/profileDownload";
import { CONVERSION_PROMPT, RESUME_TEMPLATE } from "../../lib/template";
import "../../styles/profile.css";

// The JSON import: download the blank template, fill it (an AI assistant can do the conversion; only the
// fixed template ever gets imported), then paste or upload it. It is parsed by code on the server -- no
// model is involved -- and the result is a preview the person must activate (JsonReview), never a profile
// in use.
//
// Nothing here is cyan: the page's one cyan fill is the next-step band's button, so Import is an ordinary
// outlined button. The hidden file input is the `hidden` attribute, not a style: the page's
// Content-Security-Policy allows styles from the site's own stylesheet only.

const FILE_IMPORT_OPEN_NOTE =
  "A file import above is in progress. Finish it, or discard its draft, before importing JSON.";

const TITLE_ID = "bj-json-import-title";

export function ImportSection({
  replacing,
  busy,
  disabled,
  error,
  notice,
  onImport,
}: {
  replacing: boolean;
  busy: boolean;
  // A resume-file import is under way above: this one waits for it.
  disabled: boolean;
  error: string | null;
  // Something true that is not a failure: the pasted JSON is the profile already in use, so there is
  // nothing to review. It is its own line (not the error's) so it is never drawn as a failure.
  notice?: string | null;
  onImport: (rawText: string) => void;
}) {
  const [pasted, setPasted] = useState("");
  const [promptCopied, setPromptCopied] = useState(false);
  const [copyFailed, setCopyFailed] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);

  async function onFileChosen(files: FileList | null) {
    const file = files?.[0];
    if (!file) {
      return;
    }
    onImport(await file.text());
    if (fileInput.current) {
      fileInput.current.value = "";
    }
  }

  async function copyPrompt() {
    setCopyFailed(false);
    try {
      await navigator.clipboard.writeText(CONVERSION_PROMPT);
    } catch {
      setCopyFailed(true);
      return;
    }
    setPromptCopied(true);
    setTimeout(() => setPromptCopied(false), 2000);
  }

  return (
    <section className="bj-prof-block" aria-labelledby={TITLE_ID}>
      <h2 id={TITLE_ID} className="bj-prof-title" tabIndex={-1}>
        {replacing ? "Import a new version" : "Set up your profile"}
      </h2>
      <p className="bj-muted">
        Your resume lives here as structured JSON -- private, and read by code rather than a model.
        What you import is saved as an inactive draft that you review before it becomes your
        profile. Fill the template (an AI assistant can do the conversion; only the fixed
        template ever gets imported), then paste or upload it.
      </p>
      <div className="bj-actions">
        <button type="button" onClick={() => downloadJson(RESUME_TEMPLATE, "between-jobs-template.json")}>
          Download blank template
        </button>
        <button type="button" onClick={() => void copyPrompt()}>
          {promptCopied ? "Copied ✓" : "Copy conversion prompt"}
        </button>
        {/* aria-disabled, never disabled, for the buttons that start an import: the press (or the file it chose)
            is what makes the section busy, and a button that disables itself drops keyboard focus to the top
            of the page, where a refused import would leave it. */}
        <button type="button" aria-disabled={busy || disabled ? true : undefined} onClick={() => !(busy || disabled) && fileInput.current?.click()}>
          Upload .json file
        </button>
        <input
          ref={fileInput}
          type="file"
          accept=".json,application/json"
          hidden
          tabIndex={-1}
          aria-label="JSON file to import"
          onChange={(e) => void onFileChosen(e.target.files)}
        />
      </div>
      {copyFailed && (
        <p role="status" className="bj-muted bj-small">
          Couldn't copy to the clipboard. Your browser may be blocking it.
        </p>
      )}
      <label className="bj-field">
        <span className="bj-visually-hidden">Your filled template, as JSON</span>
        <textarea
          rows={8}
          placeholder='Paste your filled template here ({"personal": ...)'
          value={pasted}
          onChange={(e) => setPasted(e.target.value)}
          disabled={disabled}
        />
      </label>
      {error && (
        <div className="bj-error" role="alert">
          {error}
        </div>
      )}
      <div role="status">{notice && <p className="bj-muted">{notice}</p>}</div>
      {disabled && (
        <p role="status" className="bj-muted bj-small">
          {FILE_IMPORT_OPEN_NOTE}
        </p>
      )}
      <div className="bj-actions">
        <button
          type="button"
          onClick={() => !(busy || disabled || pasted.trim() === "") && onImport(pasted)}
          aria-disabled={busy || disabled || pasted.trim() === "" ? true : undefined}
        >
          {busy ? "Importing..." : "Import"}
        </button>
      </div>
    </section>
  );
}
