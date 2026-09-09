import {
  applyFillPlan,
  attachFile,
  extractCustomQuestions,
  fillCustomTextAnswer,
  findCoverLetterField,
  GENERIC_FIELD_DEFAULTS,
  isLeverApplyForm,
  planStandardFieldFills,
} from "@/lib/lever";
import type {
  BackgroundMessage,
  ContentScriptMessage,
  DetectionStateResponse,
  FillFieldResult,
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
        fieldMapError: null,
      };
      if (tabState.status !== "tracked" || tabState.payload.personal_info === null) {
        return result;
      }

      // E3c -- the open-source GENERIC_FIELD_DEFAULTS fields fill
      // regardless of whether the signed field map is available; only
      // the genuinely Lever-idiosyncratic behavior below (location/
      // LinkedIn/portfolio, cover-letter discovery, custom questions) is
      // gated on it, matching D4's fail-closed scope to what the signed
      // map actually protects rather than the whole extension.
      const standardFields = [
        ...GENERIC_FIELD_DEFAULTS.standardFields,
        ...(tabState.fieldMap?.standard_fields ?? []),
      ];
      const plan = planStandardFieldFills(
        document,
        tabState.payload.personal_info,
        forceRefillAll,
        standardFields,
      );
      result.filledFields = applyFillPlan(document, plan);

      const resumeOutcome = tryAttach(GENERIC_FIELD_DEFAULTS.resumeSelector, tabState.resume, forceRefillAll);
      result.resumeAttached = resumeOutcome.attached;
      result.resumeError = resumeOutcome.error;

      if (tabState.fieldMap === null) {
        // D4 fail-closed: no verified map means no trustworthy way to
        // know which fields on this page are the cover-letter slot or
        // genuine custom questions -- neither gets attempted, and the
        // side panel is told why rather than silently showing an empty
        // "nothing to answer" list.
        result.fieldMapError = tabState.fieldMapError;
        return result;
      }

      // Adversarially-confirmed gap: a passed Ed25519 signature proves a
      // map's AUTHENTICITY, not that every selector/pattern inside it is
      // syntactically valid -- a maintainer typo (an unbalanced paren in
      // cover_letter_label_pattern, a malformed CSS selector) would
      // otherwise throw a synchronous, uncaught exception here, silently
      // aborting the ENTIRE fill (custom-question extraction included,
      // even though it doesn't itself touch the broken field) for every
      // user of this ATS. Treated the same as D4's own "no usable map"
      // case -- fail closed on the Lever-idiosyncratic behavior, but the
      // GENERIC_FIELD_DEFAULTS fields above have already filled by now
      // regardless, matching this function's own established scope split.
      try {
        const coverLetterField = findCoverLetterField(document, tabState.fieldMap);
        if (coverLetterField !== null) {
          if (tabState.coverLetter === null) {
            // The page has a cover-letter slot but this application never
            // generated one (generate_cover_letter wasn't requested) --
            // told explicitly rather than left silently invisible, since
            // this field is otherwise excluded from unresolvedQuestions
            // entirely (it's a file field, not a text-answerable one).
            result.coverLetterError = "No cover letter was generated for this application yet.";
          } else {
            // `coverLetterField` is an untrusted `name` attribute value
            // scraped from the page's own DOM (via findCoverLetterField),
            // not signed-map data -- CSS.escape matches the same
            // defense-in-depth precedent fillCustomTextAnswer/
            // extractCustomQuestions already apply to every other
            // selector built from page-scraped content.
            const coverLetterOutcome = tryAttach(
              `[name="${CSS.escape(coverLetterField)}"]`,
              tabState.coverLetter,
              forceRefillAll,
            );
            result.coverLetterAttached = coverLetterOutcome.attached;
            result.coverLetterError = coverLetterOutcome.error;
          }
        }

        result.unresolvedQuestions = extractCustomQuestions(
          document,
          tabState.fieldMap,
          coverLetterField,
        ).map((q) => ({ fieldName: q.fieldName, label: q.label, kind: q.kind }));
      } catch (e) {
        result.coverLetterAttached = false;
        result.coverLetterError = null;
        result.unresolvedQuestions = [];
        result.fieldMapError =
          "This ATS's field map has an invalid selector or pattern -- refusing to use it. " +
          (e instanceof Error ? e.message : "");
      }
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
      if (message.type === "FILL_FIELD") {
        // E3b -- the one path a value chosen off-page (a saved answer,
        // an LLM draft) ever reaches the real DOM. `fillCustomTextAnswer`
        // itself re-validates the target (genuine custom-question text
        // field only, never a radio/file/eeo/standard field), so this
        // handler doesn't need to duplicate that check. E3c: needs the
        // verified map's own `custom_question_prefix` to do that check at
        // all -- no map, no fill, matching D4's fail-closed scope.
        const fieldMap = tabState?.status === "tracked" ? tabState.fieldMap : null;
        const response: FillFieldResult = {
          filled:
            fieldMap !== null &&
            fillCustomTextAnswer(document, fieldMap, message.fieldName, message.value),
        };
        return Promise.resolve(response);
      }
    });
  },
});
