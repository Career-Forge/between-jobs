import {
  applyFillPlan,
  attachFile,
  extractCustomQuestions,
  findCoverLetterField,
  isLeverApplyForm,
  planStandardFieldFills,
} from "@/lib/lever";
import type {
  BackgroundMessage,
  ContentScriptMessage,
  DetectionStateResponse,
  FillResult,
  TabState,
} from "@/lib/types";

function base64ToArrayBuffer(base64: string): ArrayBuffer {
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return bytes.buffer;
}

export default defineContentScript({
  matches: ["https://jobs.lever.co/*"],
  main() {
    let tabState: TabState | null = null;
    const formDetected = isLeverApplyForm(document);

    async function detect(): Promise<void> {
      if (!formDetected) return;
      const message: BackgroundMessage = { type: "LEVER_PAGE_DETECTED", url: location.href };
      try {
        tabState = await browser.runtime.sendMessage(message);
      } catch (e) {
        // The background service worker can be mid-restart when this
        // fires (MV3 kills an idle worker after ~30s) -- an
        // adversarially-confirmed gap: an unhandled rejection here used
        // to leave `tabState` stuck at null forever with no way for the
        // side panel to tell "still checking" from "gave up." Leaving
        // `tabState` at null either way (detect() is safely re-callable
        // via RECHECK), but at least this doesn't crash silently.
        console.error("[between-jobs] page detection failed", e);
      }
    }

    void detect();

    // `null` (not an all-empty FillResult) means "detection hasn't
    // resolved yet" -- an adversarially-confirmed gap: returning a
    // structurally valid empty result made an in-flight lookup
    // indistinguishable from a genuine "nothing to fill."
    // Shared by the résumé (a fixed selector) and the cover letter (a
    // runtime-discovered one, per findCoverLetterField) -- same D5
    // idempotency rule as every other field: skip an input that already
    // has a file, unless forced.
    //
    // `attached` means "is a file present on this input right now" --
    // NOT "did this specific call just set it." Adversarially confirmed
    // a real bug in an earlier version that conflated the two: a D5 skip
    // (file already there, not forced) returned attached:false, so the
    // side panel displayed "not attached" for a résumé that was, in
    // fact, still genuinely attached from a prior fill.
    function tryAttach(
      selector: string,
      file: { base64: string; filename: string } | null,
      forceRefillAll: boolean,
    ): { attached: boolean; error: string | null } {
      const input = document.querySelector<HTMLInputElement>(selector);
      if (input === null) return { attached: false, error: null };
      const alreadyHasFile = (input.files?.length ?? 0) > 0;
      if (alreadyHasFile && !forceRefillAll) {
        return { attached: true, error: null };
      }
      if (file === null) {
        return { attached: alreadyHasFile, error: null };
      }
      try {
        attachFile(input, base64ToArrayBuffer(file.base64), file.filename, "application/pdf");
        return { attached: true, error: null };
      } catch (e) {
        return {
          attached: alreadyHasFile,
          error: e instanceof Error ? e.message : "Failed to attach file",
        };
      }
    }

    function fillPage(forceRefillAll: boolean): FillResult | null {
      if (tabState === null) return null;
      const result: FillResult = {
        filledFields: [],
        skippedFields: [],
        resumeAttached: false,
        resumeError: null,
        coverLetterAttached: false,
        coverLetterError: null,
        unresolvedQuestions: [],
      };
      if (tabState.status !== "tracked" || tabState.payload.personal_info === null) {
        return result;
      }

      const plan = planStandardFieldFills(document, tabState.payload.personal_info, forceRefillAll);
      result.filledFields = applyFillPlan(document, plan);

      const resumeOutcome = tryAttach('input[name="resume"]', tabState.resume, forceRefillAll);
      result.resumeAttached = resumeOutcome.attached;
      result.resumeError = resumeOutcome.error;

      const coverLetterField = findCoverLetterField(document);
      if (coverLetterField !== null) {
        if (tabState.coverLetter === null) {
          // The page has a cover-letter slot but this application never
          // generated one (generate_cover_letter wasn't requested) --
          // told explicitly rather than left silently invisible, since
          // this field is otherwise excluded from unresolvedQuestions
          // entirely (it's a file field, not a text-answerable one).
          result.coverLetterError = "No cover letter was generated for this application yet.";
        } else {
          const coverLetterOutcome = tryAttach(
            `[name="${coverLetterField}"]`,
            tabState.coverLetter,
            forceRefillAll,
          );
          result.coverLetterAttached = coverLetterOutcome.attached;
          result.coverLetterError = coverLetterOutcome.error;
        }
      }

      result.unresolvedQuestions = extractCustomQuestions(document, coverLetterField).map((q) => ({
        fieldName: q.fieldName,
        label: q.label,
        kind: q.kind,
      }));
      return result;
    }

    // Never registers any listener capable of clicking Lever's own
    // Submit button or checking a consent/EEO checkbox -- the only two
    // message types this content script understands are read-only status
    // and a fill that only ever touches STANDARD_FIELDS + the resume
    // input, per browser-extension.md's own invariant.
    browser.runtime.onMessage.addListener((message: ContentScriptMessage) => {
      if (message.type === "GET_DETECTION_STATE") {
        const response: DetectionStateResponse = { formDetected, tabState };
        return Promise.resolve(response);
      }
      if (message.type === "RECHECK") {
        // D3 (browser-extension.md) -- an explicit manual retrigger,
        // shipped from day one: even the best-resourced competitor
        // (Simplify) maintains a permanent "not supported" failure
        // category, so automated detection alone was never going to be
        // sufficient on its own.
        return detect().then((): DetectionStateResponse => ({ formDetected, tabState }));
      }
      if (message.type === "REQUEST_FILL") {
        return Promise.resolve(fillPage(message.forceRefillAll));
      }
    });
  },
});
