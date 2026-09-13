import * as ashby from "@/lib/ashby";
import * as greenhouse from "@/lib/greenhouse";
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
import { applyReactControlledFillPlan } from "@/lib/standardFields";
import type {
  AtsType,
  BackgroundMessage,
  ContentScriptMessage,
  DetectionStateResponse,
  ExtensionPersonalInfo,
  FillFieldResult,
  FillResult,
  GeneratedFile,
  TabState,
} from "@/lib/types";

function base64ToArrayBuffer(base64: string): ArrayBuffer {
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return bytes.buffer;
}

// E4/E5 -- which ATS this page belongs to, decided once by hostname.
// Every other Lever/Greenhouse/Ashby-specific decision in this file
// (which engine's GENERIC_FIELD_DEFAULTS, which fill-plan mechanism,
// which custom-question extraction) branches off this single value
// rather than re-deriving it. `null` means "not a page any engine here
// understands" -- `formDetected` below stays false and nothing else in
// this file runs.
const ATS_HOST_SUFFIXES: [suffix: string, atsType: AtsType][] = [
  [".lever.co", "lever"],
  [".greenhouse.io", "greenhouse"],
  [".ashbyhq.com", "ashby"],
];

function detectAtsType(hostname: string): AtsType | null {
  for (const [suffix, type] of ATS_HOST_SUFFIXES) {
    if (hostname === suffix.slice(1) || hostname.endsWith(suffix)) return type;
  }
  return null;
}

export default defineContentScript({
  // E4/E5 -- `jobs.lever.co` unchanged from E2. `job-boards.greenhouse.io`
  // is Greenhouse's real, confirmed-live modern surface (Melio/Wheely/
  // Cloudflare, 2026-09-13); `boards.greenhouse.io` is kept too even
  // though every real posting tested there 30x-redirected to the modern
  // host, since a still-legacy org (not among the 3 tested) would land
  // here first before any redirect completes, and the manual "try this
  // page" trigger (D3) needs the content script already present to
  // retry against. `jobs.ashbyhq.com` covers both Ashby's job-
  // description page (no form) and its own `/application` sub-path
  // (the real form) -- both confirmed live.
  matches: [
    "https://jobs.lever.co/*",
    "https://job-boards.greenhouse.io/*",
    "https://boards.greenhouse.io/*",
    "https://jobs.ashbyhq.com/*",
  ],
  main() {
    const atsType = detectAtsType(location.hostname);
    const formDetected =
      atsType === "lever"
        ? isLeverApplyForm(document)
        : atsType === "greenhouse"
          ? greenhouse.isGreenhouseApplyForm(document)
          : atsType === "ashby"
            ? ashby.isAshbyApplyForm(document)
            : false;

    let tabState: TabState | null = null;

    // Guards against detect() calls resolving out of order -- the initial
    // page-load call and a later user-triggered RECHECK are both in flight
    // independently, with no request id of their own, so whichever promise
    // resolves last would otherwise win regardless of which was issued last.
    let detectGeneration = 0;

    async function detect(): Promise<void> {
      if (!formDetected || atsType === null) return;
      const generation = ++detectGeneration;
      const message: BackgroundMessage = { type: "PAGE_DETECTED", atsType, url: location.href };
      try {
        const result = await browser.runtime.sendMessage(message);
        if (generation !== detectGeneration) return;
        tabState = result;
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
    // Shared by the résumé (a fixed selector on every ATS) and the cover
    // letter (a runtime-discovered one on Lever via findCoverLetterField,
    // a fixed selector on Greenhouse, not yet supported on Ashby -- see
    // lib/ashby.ts's own top-of-file note) -- same D5 idempotency rule as
    // every other field: skip an input that already has a file, unless
    // forced.
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

    // Shared by Lever (a runtime-discovered field name, once
    // findCoverLetterField locates it) and Greenhouse (a fixed
    // `#cover_letter` selector) -- both need the identical three-way
    // outcome: no such field on this posting at all (stay silent, D5/UX
    // precedent), a field exists but this application never generated a
    // cover letter (say so explicitly), or attempt the attach.
    function tryAttachCoverLetter(
      selector: string,
      coverLetter: GeneratedFile | null,
      forceRefillAll: boolean,
    ): { attached: boolean; error: string | null } {
      if (document.querySelector(selector) === null) return { attached: false, error: null };
      if (coverLetter === null) {
        return {
          attached: false,
          error: "No cover letter was generated for this application yet.",
        };
      }
      return tryAttach(selector, coverLetter, forceRefillAll);
    }

    type TrackedTabState = Extract<TabState, { status: "tracked" }>;

    // E2/E3 (unchanged) -- Lever's own fill mechanism: server-rendered
    // HTML, a plain `.value` set plus a dispatched event is enough, and
    // the Lever-idiosyncratic behavior (cover-letter discovery, custom
    // questions, location/LinkedIn/portfolio) fails closed (D4) when no
    // verified signed map exists.
    function fillLeverPage(tracked: TrackedTabState, personalInfo: ExtensionPersonalInfo, forceRefillAll: boolean, result: FillResult): FillResult {
      const standardFields = [
        ...GENERIC_FIELD_DEFAULTS.standardFields,
        ...(tracked.fieldMap?.standard_fields ?? []),
      ];
      const plan = planStandardFieldFills(document, personalInfo, forceRefillAll, standardFields);
      result.filledFields = applyFillPlan(document, plan);

      const resumeOutcome = tryAttach(GENERIC_FIELD_DEFAULTS.resumeSelector, tracked.resume, forceRefillAll);
      result.resumeAttached = resumeOutcome.attached;
      result.resumeError = resumeOutcome.error;

      if (tracked.fieldMap === null || tracked.fieldMap.ats_type !== "lever") {
        // D4 fail-closed: no verified Lever map means no trustworthy way
        // to know which fields on this page are the cover-letter slot or
        // genuine custom questions -- neither gets attempted, and the
        // side panel is told why rather than silently showing an empty
        // "nothing to answer" list.
        result.fieldMapError = tracked.fieldMapError;
        return result;
      }
      const leverMap = tracked.fieldMap;

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
        const coverLetterField = findCoverLetterField(document, leverMap);
        if (coverLetterField !== null) {
          const coverLetterOutcome = tryAttachCoverLetter(
            // `coverLetterField` is an untrusted `name` attribute value
            // scraped from the page's own DOM (via findCoverLetterField),
            // not signed-map data -- CSS.escape matches the same
            // defense-in-depth precedent fillCustomTextAnswer/
            // extractCustomQuestions already apply to every other
            // selector built from page-scraped content.
            `[name="${CSS.escape(coverLetterField)}"]`,
            tracked.coverLetter,
            forceRefillAll,
          );
          result.coverLetterAttached = coverLetterOutcome.attached;
          result.coverLetterError = coverLetterOutcome.error;
        }

        result.unresolvedQuestions = extractCustomQuestions(document, leverMap, coverLetterField).map((q) => ({
          fieldName: q.fieldName,
          label: q.label,
          kind: q.kind,
        }));
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

    // E4 -- Greenhouse's own fill mechanism. Unlike Lever, this is a
    // React-controlled form (confirmed live, see lib/standardFields.ts's
    // own note), so standard-field fills go through
    // `applyReactControlledFillPlan`, not `applyFillPlan`. Custom-
    // question extraction and cover-letter attach are NOT gated behind
    // `tracked.fieldMap` -- lib/greenhouse.ts's own engine is fully
    // self-contained, open-source, and unsigned (matching the state
    // Lever itself was in before E3c; this repo never curates or signs a
    // real Greenhouse map -- see that file's own top-of-file note). A
    // future signed Greenhouse map would only ever ADD supplemental
    // standard_fields on top of the generic ones, same merge pattern as
    // Lever's.
    function fillGreenhousePage(tracked: TrackedTabState, personalInfo: ExtensionPersonalInfo, forceRefillAll: boolean, result: FillResult): FillResult {
      const fieldMap = tracked.fieldMap;
      const standardFields = [
        ...greenhouse.GENERIC_FIELD_DEFAULTS.standardFields,
        ...(fieldMap !== null && fieldMap.ats_type === "greenhouse" ? fieldMap.standard_fields : []),
      ];
      const plan = planStandardFieldFills(document, personalInfo, forceRefillAll, standardFields);
      result.filledFields = applyReactControlledFillPlan(document, plan);

      const resumeOutcome = tryAttach(greenhouse.GENERIC_FIELD_DEFAULTS.resumeSelector, tracked.resume, forceRefillAll);
      result.resumeAttached = resumeOutcome.attached;
      result.resumeError = resumeOutcome.error;

      const coverLetterOutcome = tryAttachCoverLetter(
        greenhouse.GENERIC_FIELD_DEFAULTS.coverLetterSelector,
        tracked.coverLetter,
        forceRefillAll,
      );
      result.coverLetterAttached = coverLetterOutcome.attached;
      result.coverLetterError = coverLetterOutcome.error;

      result.unresolvedQuestions = greenhouse.extractCustomQuestions(document).map((q) => ({
        fieldName: q.fieldName,
        label: q.label,
        kind: q.kind,
      }));
      return result;
    }

    // E5 -- Ashby's own fill mechanism. Also React-controlled (confirmed
    // live). Same self-contained, unsigned-engine shape as Greenhouse.
    // No cover-letter attach attempted at all (lib/ashby.ts's own note:
    // no stable selector/naming convention was found live on either
    // tested posting) -- a real, disclosed gap, not silently guessed at.
    function fillAshbyPage(tracked: TrackedTabState, personalInfo: ExtensionPersonalInfo, forceRefillAll: boolean, result: FillResult): FillResult {
      const fieldMap = tracked.fieldMap;
      const standardFields = [
        ...ashby.GENERIC_FIELD_DEFAULTS.standardFields,
        ...(fieldMap !== null && fieldMap.ats_type === "ashby" ? fieldMap.standard_fields : []),
      ];
      const plan = planStandardFieldFills(document, personalInfo, forceRefillAll, standardFields);
      result.filledFields = applyReactControlledFillPlan(document, plan);

      const resumeOutcome = tryAttach(ashby.GENERIC_FIELD_DEFAULTS.resumeSelector, tracked.resume, forceRefillAll);
      result.resumeAttached = resumeOutcome.attached;
      result.resumeError = resumeOutcome.error;

      result.unresolvedQuestions = ashby.extractCustomQuestions(document).map((q) => ({
        fieldName: q.fieldName,
        label: q.label,
        kind: q.kind,
      }));
      return result;
    }

    function fillPage(forceRefillAll: boolean): FillResult | null {
      if (tabState === null || atsType === null) return null;
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
      const personalInfo = tabState.payload.personal_info;

      if (atsType === "lever") return fillLeverPage(tabState, personalInfo, forceRefillAll, result);
      if (atsType === "greenhouse") return fillGreenhousePage(tabState, personalInfo, forceRefillAll, result);
      return fillAshbyPage(tabState, personalInfo, forceRefillAll, result);
    }

    // Never registers any listener capable of clicking any ATS's own
    // Submit button or checking a consent/EEO checkbox -- the only
    // message types this content script understands are read-only
    // status and a fill that only ever touches each ATS's own
    // GENERIC_FIELD_DEFAULTS/custom-question namespace, per
    // browser-extension.md's own invariant. Holds identically for
    // Lever, Greenhouse, and Ashby -- nothing about generalizing this
    // file to three ATSs relaxes it anywhere.
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
        // an LLM draft) ever reaches the real DOM. Each ATS's own
        // `fillCustomTextAnswer` re-validates the target itself, so this
        // handler doesn't need to duplicate that check. Lever alone
        // gates this on a verified field map (E3c: needs the map's own
        // `custom_question_prefix` to do the check at all -- no map, no
        // fill, matching D4's fail-closed scope); Greenhouse/Ashby's
        // engines are self-contained and never require one, matching
        // fillPage's own split above.
        let filled = false;
        const currentTabState = tabState;
        const leverFieldMap =
          currentTabState !== null && currentTabState.status === "tracked" && currentTabState.fieldMap !== null && currentTabState.fieldMap.ats_type === "lever"
            ? currentTabState.fieldMap
            : null;
        if (atsType === "lever" && leverFieldMap !== null) {
          filled = fillCustomTextAnswer(document, leverFieldMap, message.fieldName, message.value);
        } else if (atsType === "greenhouse") {
          filled = greenhouse.fillCustomTextAnswer(document, message.fieldName, message.value);
        } else if (atsType === "ashby") {
          filled = ashby.fillCustomTextAnswer(document, message.fieldName, message.value);
        }
        const response: FillFieldResult = { filled };
        return Promise.resolve(response);
      }
    });
  },
});
