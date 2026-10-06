import { describe, expect, it } from "vitest";
import appCss from "../app.css?raw";
import cardSource from "../components/ResumeImportCard.tsx?raw";
import viewSource from "../components/ResumeImportView.tsx?raw";
import profileSource from "../pages/Profile.tsx?raw";
import apiSource from "./api.ts?raw";
import importSource from "./resumeImport.ts?raw";
import { rulesOf } from "../testing/css";

// What the source of the resume-file import says, read as text: the places the rendering tests do
// not reach (the connected card is only ever drawn once, in its first state, without effects).
// A reverted line type-checks and passes every other test, so the promises that matter are pinned
// here: a draft is used ONLY by the confirm button, and the JSON import is as it was.
//
// vite's `?raw` imports and glob, not `node:fs`: @types/node is not installed here.

const allSources = import.meta.glob("../**/*.{ts,tsx}", {
  query: "?raw",
  import: "default",
  eager: true,
}) as Record<string, string>;

// A path from the glob, as it is under src/: "../components/X.tsx" is components/X.tsx, and a file next
// to this one comes as "./x.ts".
function underSrc(path: string): string {
  return path.startsWith("../") ? path.slice(3) : `lib/${path.replace(/^\.\//, "")}`;
}

const appSources = Object.entries(allSources).filter(([path]) => !/\.test\.tsx?$/.test(path));

// The source with its comments taken out, so that a scan for a statement is not satisfied by the same
// text sitting in a comment. Only `//` to the end of a line and `/* ... */`, which is all these files
// use; a `//` inside a string (a URL) is not touched, since none of the scanned statements has one.
function withoutComments(source: string): string {
  return source
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .split("\n")
    .map((line) => line.replace(/(^|\s)\/\/.*$/, "$1"))
    .join("\n");
}

// The body of a function declared with `async function <name>` or `function <name>`, to the line
// that closes it: a line holding only `}` at the indentation the declaration has.
function bodyOf(source: string, name: string): string {
  const match = new RegExp(`^( *)(?:export )?(?:async )?function ${name}\\b`, "m").exec(source);
  if (match === null) throw new Error(`no function ${name}`);
  const close = source.indexOf(`\n${match[1]}}\n`, match.index);
  return source.slice(match.index, close === -1 ? undefined : close + match[1].length + 3);
}

describe("a draft is used only by the confirm button", () => {
  it("scans the whole app, so a new caller cannot hide", () => {
    expect(appSources.length).toBeGreaterThan(100);
    expect(appSources.some(([path]) => path.endsWith("/pages/Profile.tsx"))).toBe(true);
  });

  // Everything that asks the server to activate a profile version, and why each is not a way to use
  // a resume file's draft: the first three each have their own explicit button (the JSON import's
  // preview, saving an edit, the gap interview's approval) and know nothing of an uploaded file.
  it("the activation route is named in four files: the three older flows, and the card's confirm", () => {
    const naming = appSources
      .filter(([, text]) => /versions\/\$\{[A-Za-z.]+\}\/activate/.test(text))
      .map(([path]) => underSrc(path))
      .sort();
    expect(naming).toEqual([
      "components/GapInterview.tsx",
      "components/ResumeImportCard.tsx",
      "lib/useProfileEditor.ts",
      "pages/Profile.tsx",
    ]);
  });

  it("the import's three entry points (the upload, the draft's parsing and the review's state) are reached from the card and nowhere else", () => {
    const reaching = (needle: RegExp) =>
      appSources
        .filter(([path, text]) => needle.test(text) && !/^lib\/resumeImport[A-Za-z]*\.ts$/.test(underSrc(path)))
        .map(([path]) => underSrc(path))
        .sort();
    expect(reaching(/apiFetchBytes/)).toEqual(["components/ResumeImportCard.tsx", "lib/api.ts"]);
    expect(
      reaching(/import\s*\{[^}]*\b(?:uploadFile|activateDraft|discardDraft)\b[^}]*\}\s*from\s*"[^"]*\/resumeImport"/),
    ).toEqual(["components/ResumeImportCard.tsx"]);
    expect(reaching(/import-document/)).toEqual([]);
  });

  it("uploading a file only ever ends in a draft to review: the upload function never names activation", () => {
    const upload = bodyOf(importSource, "uploadFile");
    expect(upload.length).toBeGreaterThan(300);
    expect(upload).not.toMatch(/activat/i);
    expect(upload).not.toContain("io.discard");
    expect(upload).toContain("io.upload(");
    // the one place the library asks the server to activate is the function the card calls on confirm
    expect((importSource.match(/io\.activate\(/g) ?? []).length).toBe(1);
    // and that function sends nothing unless the state is a draft the person was asked about: the
    // guard is the first thing in it, before anything is dispatched or sent
    const activate = withoutComments(bodyOf(importSource, "activateDraft"));
    expect(activate).toContain("io.activate(draft.versionId)");
    expect(activate).toContain("const draft = draftToActivate(state);");
    expect(activate).toContain("if (draft === null) return false;");
    expect(activate.indexOf("if (draft === null) return false;")).toBeLessThan(activate.indexOf("dispatch("));
    expect(activate.indexOf("if (draft === null) return false;")).toBeLessThan(activate.indexOf("io.activate("));
  });

  it("the card names activation once, in a function that is called only from the confirm handler", () => {
    expect((cardSource.match(/\/activate/g) ?? []).length).toBe(1);
    expect(cardSource).toContain('activate: (versionId) => apiFetch(`/profile/versions/${versionId}/activate`, { method: "POST" })');
    // and the other binding, which must stay a DELETE of the version itself
    expect(cardSource).toContain('discard: (versionId) => apiFetch(`/profile/versions/${versionId}`, { method: "DELETE" })');
    expect((cardSource.match(/activateDraft\(/g) ?? []).length).toBe(1);
    // read without its comments: the guard must be code, not a mention of it
    const confirm = withoutComments(bodyOf(cardSource, "confirmUse"));
    expect(confirm).toContain("activateDraft(state, replacesCurrent, io, dispatch)");
    expect(confirm).toContain("const draft = draftToActivate(state);");
    expect(confirm).toContain("if (inFlight.current || draft === null) return;");
    // the page is told only when the server said yes
    expect((cardSource.match(/onActivated\(\)/g) ?? []).length).toBe(1);
    expect(confirm).toContain("if (done) onActivated();");
    // nothing else of the card calls it
    expect((cardSource.match(/confirmUse\(/g) ?? []).length).toBe(2); // its own declaration and the action
    expect(cardSource).toContain("confirmUse: () => void confirmUse(),");
    for (const other of ["start", "discard"]) {
      expect(bodyOf(cardSource, other)).not.toMatch(/activat/i);
    }
  });

  it("the view wires the confirming action to one button, which exists only while the question is on screen", () => {
    expect((viewSource.match(/actions\.confirmUse/g) ?? []).length).toBe(1);
    const open = viewSource.indexOf("{confirming && (");
    const wired = viewSource.indexOf("actions.confirmUse");
    const close = viewSource.indexOf("Not yet");
    expect(open).toBeGreaterThan(-1);
    expect(wired).toBeGreaterThan(open);
    expect(wired).toBeLessThan(close);
    // and "Use this profile" is wired to the question, not to the request
    expect(viewSource).toContain("onClick={actions.askUse}");
  });

  it("the upload handlers of the card start an upload and do nothing else", () => {
    expect(cardSource).toContain("fileChosen: (files) => {");
    const start = bodyOf(cardSource, "start");
    expect(start).toContain("uploadFile(file, io, dispatch)");
    expect(start).not.toContain("activateDraft");
  });

  it("guards each request with the in-flight flag, so a second click cannot send a second one", () => {
    expect((cardSource.match(/if \(inFlight\.current/g) ?? []).length).toBe(3);
    expect((cardSource.match(/inFlight\.current = true;/g) ?? []).length).toBe(3);
    expect((cardSource.match(/inFlight\.current = false;/g) ?? []).length).toBe(3);
  });

  it("moves focus after each change, through the tested function, and does not move it on first render", () => {
    expect(cardSource).toContain("focusTargetAfterChange(previous.current, state)");
    expect(cardSource).toContain("const previous = useRef<ImportState>(state);");
  });

  it("sends the file through the raw-bytes helper, not the JSON one", () => {
    expect(cardSource).toContain("upload: (path, body, contentType) => apiFetchBytes(path, body, contentType),");
    expect(apiSource).toContain("export function apiFetchBytes<T>(path: string, body: BodyInit, contentType: string)");
    expect(apiSource).toContain('{ method: "POST", body }, contentType');
  });
});

describe("the Profile page", () => {
  it("draws the file import above the JSON import, for a person with a profile and one without", () => {
    const card = profileSource.indexOf("<ResumeImportCard");
    const json = profileSource.indexOf("<ImportSection");
    const active = profileSource.indexOf('state.kind === "active" && (');
    expect(card).toBeGreaterThan(-1);
    expect(active).toBeLessThan(card);
    expect(card).toBeLessThan(json);
    expect(profileSource).toContain('replacesCurrent={state.kind === "active"}');
    expect(profileSource).toContain("onActivated={() => void loadCurrent()}");
    expect((profileSource.match(/<ResumeImportCard/g) ?? []).length).toBe(1);
  });

  // The review tells a person who leaves that the page does not list saved drafts. That is true
  // while nothing in the app reads the collection of versions: every use of that address is a POST
  // (the JSON import, and saving an edit).
  it("lists no pending drafts anywhere, which is what the review says about leaving", () => {
    const uses = appSources.flatMap(([path, text]) =>
      [...text.matchAll(/"\/profile\/versions"(.{0,40})/gs)].map((match) => [underSrc(path), match[1]] as const),
    );
    expect(uses.map(([path]) => path).sort()).toEqual(["lib/useProfileEditor.ts", "pages/Profile.tsx"]);
    for (const [path, after] of uses) expect(after, path).toMatch(/^,\s*\{\s*method: "POST"/);
    expect(viewSource).toContain("This page does not list saved drafts");
  });

  // The JSON import's preview replaces the whole list of cards, the file card with it, so a file
  // import that is open (a file being read, a draft waiting) would be dropped without a word. The two
  // are held apart both ways: the card is blocked while the JSON import is sent, and the JSON import
  // is disabled and refuses while a file import is open.
  it("holds the two imports apart: neither starts while the other is under way", () => {
    // read without comments: the page's own comment names these props, and must not satisfy the scan
    const page = withoutComments(profileSource);
    expect(page).toContain("const [fileImportOpen, setFileImportOpen] = useState(false);");
    expect(page).toContain("blocked={busy}");
    expect(page).toContain("onOpenChange={setFileImportOpen}");
    expect(page).toContain("disabled={fileImportOpen}");
    // the section's own controls: the upload button, the text box and the Import button
    expect(page).toContain("disabled={busy || disabled}");
    expect(page).toContain("disabled={disabled}");
    expect(page).toContain('disabled={busy || disabled || pasted.trim() === ""}');
    // refused in the function itself, so a way round the disabled controls is no way round
    const importJson = withoutComments(bodyOf(profileSource, "importJson"));
    expect(importJson).toContain("if (fileImportOpen) return;");
    expect(importJson.indexOf("if (fileImportOpen) return;")).toBeLessThan(importJson.indexOf("setBusy(true)"));
    expect(importJson.indexOf("if (fileImportOpen) return;")).toBeLessThan(importJson.indexOf("apiFetch"));
  });

  it("reaches the JSON preview, which has no file card, from the JSON import only", () => {
    // one place makes the preview (the JSON import, which refuses while a file import is open), and
    // the preview's own branch does not draw the file card
    expect((profileSource.match(/kind: "pending"/g) ?? []).length).toBe(2); // the type, and the one setState
    expect((profileSource.match(/setState\(\{ kind: "pending"/g) ?? []).length).toBe(1);
    expect(bodyOf(profileSource, "importJson")).toContain('setState({ kind: "pending"');
    const pending = profileSource.indexOf('if (state.kind === "pending") {');
    const rest = profileSource.indexOf("return (", pending + 40);
    expect(pending).toBeGreaterThan(-1);
    expect(profileSource.slice(pending, rest)).not.toContain("ResumeImportCard");
  });

  it("tells the page whether a file import is under way, from an effect on the state, and when it goes away", () => {
    const card = withoutComments(cardSource);
    expect(card).toContain("onOpenChange?.(importInProgress(state));");
    expect(card).toContain("}, [state, onOpenChange]);");
    expect(card).toContain("useEffect(() => () => onOpenChange?.(false), [onOpenChange]);");
    expect(card).toContain("blocked={blocked}");
  });

  it("refreshes the page's profile after a draft is used", () => {
    // loadCurrent reads /profile/current and replaces what the page shows
    expect(profileSource).toContain('apiFetch<ProfileVersion>("/profile/current")');
  });

  it("still has the JSON import exactly as it was: the template, the prompt, the upload and the preview", () => {
    for (const piece of [
      'apiFetch<ProfileVersion>("/profile/versions", {',
      "body: JSON.stringify({ raw_text: rawText }),",
      "setState({ kind: \"pending\", version, hadActive });",
      "Download blank template",
      "Copy conversion prompt",
      "Upload .json file",
      'accept=".json,application/json"',
      "Looks good -- activate",
      "Review before saving",
      'await apiFetch(`/profile/versions/${versionId}/activate`, { method: "POST" });',
      'await apiFetch(`/profile/versions/${versionId}`, { method: "DELETE" });',
    ]) {
      expect(profileSource, piece).toContain(piece);
    }
  });
});

describe("the stylesheet", () => {
  it("has a rule for every class the card's view uses", () => {
    const defined = new Set(rulesOf(appCss).flatMap((rule) => rule.selectors.flatMap((selector) => selector.match(/\.bj-[a-z0-9-]+/g) ?? [])));
    const used = [...viewSource.matchAll(/className="([^"]+)"/g)].flatMap((match) => match[1].split(/\s+/));
    // `bj-primary` is the global primary button of tokens.css (`button.bj-primary`), which vitest's
    // config does not load as text (only app.css, see vite.config.ts); the other classes are this card's.
    const missing = [...new Set(used)].filter(
      (name) => name.startsWith("bj-") && name !== "bj-primary" && !defined.has(`.${name}`),
    );
    expect(missing).toEqual([]);
  });
});
