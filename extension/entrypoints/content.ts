import {
  applyFillPlan,
  attachResumeFile,
  extractCustomQuestions,
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
    function fillPage(forceRefillAll: boolean): FillResult | null {
      if (tabState === null) return null;
      const result: FillResult = {
        filledFields: [],
        skippedFields: [],
        resumeAttached: false,
        resumeError: null,
        unresolvedQuestions: [],
      };
      if (tabState.status !== "tracked" || tabState.payload.personal_info === null) {
        return result;
      }

      const plan = planStandardFieldFills(document, tabState.payload.personal_info, forceRefillAll);
      result.filledFields = applyFillPlan(document, plan);

      if (tabState.resume !== null) {
        const resumeInput = document.querySelector<HTMLInputElement>('input[name="resume"]');
        if (resumeInput !== null && (forceRefillAll || resumeInput.files?.length === 0)) {
          try {
            attachResumeFile(
              resumeInput,
              base64ToArrayBuffer(tabState.resume.base64),
              tabState.resume.filename,
              "application/pdf",
            );
            result.resumeAttached = true;
          } catch (e) {
            result.resumeError = e instanceof Error ? e.message : "Failed to attach résumé";
          }
        }
      }

      result.unresolvedQuestions = extractCustomQuestions(document).map((q) => ({
        fieldName: q.fieldName,
        label: q.label,
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
