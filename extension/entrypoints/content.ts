import * as ashby from "@/lib/ashby";
import { detectAtsType } from "@/lib/atsHosts";
import { summarizeFill } from "@/lib/fillOutcome";
import { consentDecisionFromChange, readConsentDecision } from "@/lib/consent";
import * as greenhouse from "@/lib/greenhouse";
import {
  applyFillPlan,
  attachFile,
  extractCustomQuestions,
  fillCustomTextAnswer,
  findCoverLetterField,
  GENERIC_FIELD_DEFAULTS,
  isLeverApplyForm,
  leverFieldLabel,
} from "@/lib/lever";
import type { FillAnswerOutcome } from "@/lib/questionSafety";
import { applyReactControlledFillPlan, planStandardFieldFillsChecked } from "@/lib/standardFields";
import type {
  BackgroundMessage,
  ContentScriptMessage,
  DetectionStateResponse,
  ExtensionPersonalInfo,
  FetchApplicationFilesResult,
  FillFieldResult,
  FillResult,
  GeneratedFile,
  PageChangedMessage,
  ReportFillOutcomeMessage,
  TabState,
  VerifySessionResult,
} from "@/lib/types";

function base64ToArrayBuffer(base64: string): ArrayBuffer {
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return bytes.buffer;
}

// Chains an extra note onto a message that may already have one, so a
// second problem never silently replaces the first.
function appendNote(existing: string | null, note: string): string {
  return existing === null ? note : `${existing} ${note}`;
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
  main(ctx) {
    // E4/E5 -- which ATS this page belongs to, decided once by hostname.
    // Every other Lever/Greenhouse/Ashby-specific decision in this file
    // (which engine's GENERIC_FIELD_DEFAULTS, which fill-plan mechanism,
    // which custom-question extraction) branches off this single value
    // rather than re-deriving it. `null` means "not a page any engine here
    // understands" -- no form is ever detected and nothing else runs.
    const atsType = detectAtsType(location.hostname);

    // A function, not a value computed once at load (E6): Greenhouse and
    // Ashby are React SPAs whose apply form can render after this content
    // script starts, or only after a client-side route change from the
    // posting page to `/application` -- neither starts a new content script.
    // A load-time constant made the manual "try this page" trigger (D3)
    // useless in exactly the case it exists for.
    function isFormPresent(): boolean {
      if (atsType === "lever") return isLeverApplyForm(document);
      if (atsType === "greenhouse") return greenhouse.isGreenhouseApplyForm(document);
      if (atsType === "ashby") return ashby.isAshbyApplyForm(document);
      return false;
    }

    type TrackedTabState = Extract<TabState, { status: "tracked" }>;

    // E6 continuation -- carries a résumé/cover-letter fetch failure (see
    // ensureFilesFetched below) down into fillLeverPage/fillGreenhousePage/
    // fillAshbyPage as its own argument, rather than stuffing it onto
    // TrackedTabState itself: `tracked` here is exactly the shape
    // background.ts's own TabState declares, and the three fill functions
    // narrow `fillPage`'s TabState parameter down to it via the ordinary
    // discriminated-union check, which drops any extra properties TS
    // doesn't know the type has. A constant `null`/`null` value stands in
    // for "no fetch was attempted" wherever a fill runs without ever
    // calling ensureFilesFetched (there is no such caller today, but the
    // type keeps that possibility honest rather than assuming one shape).
    interface FilesFetchInfo {
      resumeFetchError: string | null;
      coverLetterFetchError: string | null;
    }
    const NO_FILE_FETCH_ERRORS: FilesFetchInfo = { resumeFetchError: null, coverLetterFetchError: null };

    // What background resolved for this tab, together with the URL it was
    // resolved FOR. Every consumer goes through `currentTabState()`, which
    // discards it once `location.href` has moved on: without that binding,
    // a client-side navigation from posting A to posting B left A's
    // application id, résumé and cover letter driving fills, drafts and
    // "mark as applied" on B.
    let resolved: { state: TabState; url: string } | null = null;

    // Guards against detect() calls resolving out of order -- the initial
    // page-load call and a later user-triggered RECHECK are both in flight
    // independently, with no request id of their own, so whichever promise
    // resolves last would otherwise win regardless of which was issued last.
    let detectGeneration = 0;

    // The URL a detect() is currently resolving, so a panel refresh doesn't
    // start a duplicate lookup while the initial one is still in flight.
    let detectingUrl: string | null = null;

    // The consent gate (lib/consent.ts is the one place that decides). Until the
    // person has agreed in the side panel to the CURRENT disclosure, this script
    // reads nothing from the page -- not even whether a form is there -- sends
    // no address to the API and fills nothing. The consent screen itself needs
    // nothing from the page. The answer is read fresh from storage each time
    // rather than remembered, so a withdrawal or a version change closes the gate
    // in a tab that is already open on the very next thing it is asked to do.
    async function consentGranted(): Promise<boolean> {
      return (await readConsentDecision()) === "granted";
    }

    const CONSENT_REQUIRED: DetectionStateResponse = {
      formDetected: false,
      tabState: { status: "consent_required" },
    };

    // What was resolved for this page holds the person's profile details and
    // PDFs. Once consent stops being valid none of it may be kept, and a lookup
    // still in flight must not land afterwards.
    function dropCachedState(): void {
      resolved = null;
      detectGeneration++;
      detectingUrl = null;
    }

    async function detect(): Promise<void> {
      if (atsType === null) return;
      const generation = ++detectGeneration;
      const granted = await consentGranted();
      if (generation !== detectGeneration) return;
      if (!granted) {
        resolved = null;
        detectingUrl = null;
        return;
      }
      if (!isFormPresent()) {
        resolved = null;
        detectingUrl = null;
        return;
      }
      const url = location.href;
      detectingUrl = url;
      const message: BackgroundMessage = { type: "PAGE_DETECTED", atsType, url };
      try {
        const result: TabState | undefined = await browser.runtime.sendMessage(message);
        if (generation !== detectGeneration) return;
        if (result !== undefined && result !== null) resolved = { state: result, url };
      } catch (e) {
        // The background service worker can be mid-restart when this
        // fires (MV3 kills an idle worker after ~30s) -- an
        // adversarially-confirmed gap: an unhandled rejection here used
        // to leave the state stuck at null forever with no way for the
        // side panel to tell "still checking" from "gave up." Leaving
        // it as-is either way (detect() is safely re-callable via
        // RECHECK), but at least this doesn't crash silently.
        console.error("[between-jobs] page detection failed", e);
      } finally {
        if (generation === detectGeneration) detectingUrl = null;
      }
    }

    async function verifySession(userId: string): Promise<boolean> {
      try {
        const message: BackgroundMessage = { type: "VERIFY_SESSION", userId };
        const result: VerifySessionResult | undefined = await browser.runtime.sendMessage(message);
        return result?.valid === true;
      } catch {
        return false;
      }
    }

    // The one way to read the resolved state. Null means "nothing usable
    // for THIS page and THIS user right now" -- never a stale answer:
    // resolved for a different URL (SPA navigation), or, for a tracked
    // state (which holds the user's personal info and PDFs), resolved for a
    // user who is no longer the one signed in. The session check goes to the
    // service worker each time because sign-out reaches the side panel
    // and background but never this content script.
    async function currentTabState(): Promise<TabState | null> {
      if (resolved === null) return null;
      if (!(await consentGranted())) {
        dropCachedState();
        return null;
      }
      if (resolved === null) return null;
      if (resolved.url !== location.href) {
        resolved = null;
        return null;
      }
      const { state } = resolved;
      if (state.status === "tracked" && !(await verifySession(state.userId))) {
        if (resolved?.state === state) resolved = null;
        return null;
      }
      // Re-check after the await: the page or the resolved state may have
      // moved on while the service worker was answering.
      if (resolved === null || resolved.state !== state || resolved.url !== location.href) return null;
      return state;
    }

    // E6 continuation -- lazily fetches this application's résumé/cover-
    // letter PDFs, once, the first time a Fill is actually requested for
    // it (see GeneratedFile's own doc comment in lib/types.ts for why
    // this moved off PAGE_DETECTED). `tracked.resume`/`.coverLetter` are
    // always `null` coming out of detection now, so "want" is derived
    // from what the payload says SHOULD exist (`prepare_result`) rather
    // than from those fields being null -- and, once a fetch has actually
    // populated them, a second call for the same tracked state (e.g.
    // Refill all right after Fill) sees them already non-null and skips
    // re-fetching entirely.
    //
    // Caches the result onto `resolved` itself, but only if `resolved`
    // still IS the same state object for the same URL by the time the
    // fetch finishes -- a slow fetch racing a client-side navigation or a
    // fresh RECHECK must never resurrect a page or application the
    // person has already moved past.
    async function ensureFilesFetched(tracked: TrackedTabState): Promise<{
      tracked: TrackedTabState;
      resumeFetchError: string | null;
      coverLetterFetchError: string | null;
    }> {
      // "Should this exist at all" is a genuine presence check, not just
      // "the key isn't undefined" -- the real wire payload always carries
      // both keys (see ExtensionPayload's own doc comment in lib/types.ts),
      // with `cover_letter: null` for the common no-cover-letter-generated
      // case (JSON `null`, not an absent key, since `generate_cover_letter`
      // defaults to false). `!== undefined` alone treated that `null` the
      // same as "not fetched yet," so every resume-only application
      // re-requested (and re-404'd on) the cover letter on every single
      // Fill/Refill-all, forever -- fixed by checking truthiness instead.
      const wantResume = Boolean(tracked.payload.prepare_result?.resume) && tracked.resume === null;
      const wantCoverLetter =
        Boolean(tracked.payload.prepare_result?.cover_letter) && tracked.coverLetter === null;
      if (!wantResume && !wantCoverLetter) {
        return { tracked, resumeFetchError: null, coverLetterFetchError: null };
      }

      const message: BackgroundMessage = {
        type: "FETCH_APPLICATION_FILES",
        applicationId: tracked.applicationId,
        wantResume,
        wantCoverLetter,
      };
      let response: FetchApplicationFilesResult | undefined;
      try {
        response = await browser.runtime.sendMessage(message);
      } catch (e) {
        console.error("[between-jobs] fetching application files failed", e);
      }
      const updated: TrackedTabState = {
        ...tracked,
        resume: wantResume ? (response?.resume ?? null) : tracked.resume,
        coverLetter: wantCoverLetter ? (response?.coverLetter ?? null) : tracked.coverLetter,
      };
      if (resolved !== null && resolved.state === tracked && resolved.url === location.href) {
        resolved = { state: updated, url: resolved.url };
      }
      return {
        tracked: updated,
        resumeFetchError: wantResume && updated.resume === null ? (response?.resumeError ?? "Failed to fetch résumé.") : null,
        coverLetterFetchError:
          wantCoverLetter && updated.coverLetter === null
            ? (response?.coverLetterError ?? "Failed to fetch cover letter.")
            : null,
      };
    }

    // Best-effort, and harmless when the panel is closed (nothing to hear
    // it) -- the panel refreshes on it so it never keeps showing a page
    // that is gone.
    function notifyPanelPageChanged(): void {
      const message: PageChangedMessage = { type: "PAGE_CHANGED" };
      void Promise.resolve(browser.runtime.sendMessage(message)).catch(() => {});
    }

    // Drops the cached profile data as soon as consent stops being valid (the
    // flag removed, or replaced by another version). The per-use read above is
    // still what gates every action; this only shortens how long personal data
    // sits in this page's memory afterwards. Registration can throw once the
    // extension has been reloaded under an open tab (an invalidated context),
    // and a page with a dead script has nothing left to protect.
    try {
      chrome.storage.onChanged.addListener((changes, areaName) => {
        if (consentDecisionFromChange(changes, areaName) === "needed") dropCachedState();
      });
    } catch (e) {
      console.error("[between-jobs] could not watch the stored disclosure choice", e);
    }

    void detect();

    // Client-side navigation (Greenhouse/Ashby): drop what belonged to the
    // previous page immediately, then -- once the URL has actually moved --
    // tell the panel and look again. WXT raises this event from the
    // Navigation API's `navigate` event, which fires BEFORE `location.href`
    // updates, so reading the URL in the handler itself would resolve the
    // page being left; the next task sees the new one. The form may not
    // have rendered yet either, in which case detection finds nothing now
    // and the panel's own refresh (or a later Try this page) finds it.
    ctx.addEventListener(window, "wxt:locationchange", () => {
      resolved = null;
      detectGeneration++;
      detectingUrl = null;
      ctx.setTimeout(() => {
        notifyPanelPageChanged();
        void detect().then(notifyPanelPageChanged);
      }, 0);
    });

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
    // `written` is the other half: true only when THIS call put the file on the input. The
    // count-only fill report uses that, so a repeat fill that finds a file already in place
    // (or fails to replace it) is not counted as a field it filled.
    // `fetchError` (E6 continuation): the reason `file` is null when it's
    // null because ensureFilesFetched tried and failed to download it --
    // as opposed to null because this application genuinely never had one
    // (the ordinary case, still reported as `error: null`, unchanged).
    // Only surfaced when there's otherwise nothing already attached to
    // show instead.
    interface AttachOutcome {
      attached: boolean;
      written: boolean;
      error: string | null;
    }

    function tryAttach(
      selector: string,
      file: { base64: string; filename: string } | null,
      forceRefillAll: boolean,
      fetchError: string | null = null,
    ): AttachOutcome {
      const input = document.querySelector<HTMLInputElement>(selector);
      if (input === null) return { attached: false, written: false, error: null };
      return tryAttachToInput(input, file, forceRefillAll, fetchError);
    }

    function tryAttachToInput(
      input: HTMLInputElement,
      file: { base64: string; filename: string } | null,
      forceRefillAll: boolean,
      fetchError: string | null = null,
    ): AttachOutcome {
      const alreadyHasFile = (input.files?.length ?? 0) > 0;
      if (alreadyHasFile && !forceRefillAll) {
        return { attached: true, written: false, error: null };
      }
      if (file === null) {
        return { attached: alreadyHasFile, written: false, error: alreadyHasFile ? null : fetchError };
      }
      try {
        attachFile(input, base64ToArrayBuffer(file.base64), file.filename, "application/pdf");
        return { attached: true, written: true, error: null };
      } catch (e) {
        return {
          attached: alreadyHasFile,
          written: false,
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
      fetchError: string | null = null,
    ): AttachOutcome {
      if (document.querySelector(selector) === null) return { attached: false, written: false, error: null };
      if (coverLetter === null) {
        return {
          attached: false,
          written: false,
          // A real fetch failure is a more useful message than the
          // catch-all "never generated" one, and distinguishes an
          // application that genuinely has no cover letter from a network
          // hiccup fetching a real one.
          error: fetchError ?? "No cover letter was generated for this application yet.",
        };
      }
      return tryAttach(selector, coverLetter, forceRefillAll);
    }

    // A signed map can be authentic and still carry a selector the browser
    // won't parse. That costs the one field, never the fill -- but it is
    // reported, so a maintainer's typo shows up instead of a field quietly
    // never filling.
    // A field the form has and the profile could not honestly supply (a one-word name has no
    // last name; a name in a script whose word order isn't shown has neither part; a box that
    // asks for GitHub when the profile has none). Left empty and said so, never guessed.
    function noteSkipped(result: FillResult, skipped: readonly { field: string; reason: string }[]): void {
      for (const item of skipped) result.skippedFields.push(`${item.field}: not filled -- ${item.reason}.`);
    }

    function noteInvalidSelectors(result: FillResult, invalidSelectors: string[]): void {
      if (invalidSelectors.length === 0) return;
      result.fieldMapError = appendNote(
        result.fieldMapError,
        `This ATS's field map has an invalid selector (${invalidSelectors.join(", ")}) -- skipped that field; everything else still ran.`,
      );
    }

    // E2/E3 (unchanged) -- Lever's own fill mechanism: server-rendered
    // HTML, a plain `.value` set plus a dispatched event is enough, and
    // the Lever-idiosyncratic behavior (cover-letter discovery, custom
    // questions, location/LinkedIn/portfolio) fails closed (D4) when no
    // verified signed map exists.
    function fillLeverPage(
      tracked: TrackedTabState,
      personalInfo: ExtensionPersonalInfo,
      forceRefillAll: boolean,
      filesInfo: FilesFetchInfo,
      result: FillResult,
    ): FillResult {
      const standardFields = [
        ...GENERIC_FIELD_DEFAULTS.standardFields,
        ...(tracked.fieldMap?.ats_type === "lever" ? tracked.fieldMap.standard_fields : []),
      ];
      const leverMap = tracked.fieldMap?.ats_type === "lever" ? tracked.fieldMap : null;
      const { plan, invalidSelectors, skipped } = planStandardFieldFillsChecked(
        document,
        personalInfo,
        forceRefillAll,
        standardFields,
        leverMap === null ? {} : { labelFor: (element) => leverFieldLabel(element, leverMap) },
      );
      noteInvalidSelectors(result, invalidSelectors);
      noteSkipped(result, skipped);
      result.filledFields = applyFillPlan(document, plan);

      const resumeOutcome = tryAttach(
        GENERIC_FIELD_DEFAULTS.resumeSelector,
        tracked.resume,
        forceRefillAll,
        filesInfo.resumeFetchError,
      );
      result.resumeAttached = resumeOutcome.attached;
      result.resumeWritten = resumeOutcome.written;
      result.resumeError = resumeOutcome.error;

      if (tracked.fieldMap === null || tracked.fieldMap.ats_type !== "lever") {
        // D4 fail-closed: no verified Lever map means no trustworthy way
        // to know which fields on this page are the cover-letter slot or
        // genuine custom questions -- neither gets attempted, and the
        // side panel is told why rather than silently showing an empty
        // "nothing to answer" list.
        result.fieldMapError = appendNote(
          result.fieldMapError,
          tracked.fieldMapError ?? "This ATS's field map doesn't match this page's ATS -- refusing to use it.",
        );
        return result;
      }
      const verifiedLeverMap = tracked.fieldMap;

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
        const coverLetterField = findCoverLetterField(document, verifiedLeverMap);
        if (coverLetterField !== null) {
          const coverLetterOutcome = tryAttachCoverLetter(
            // `coverLetterField` is an untrusted `name` attribute value
            // scraped from the page's own DOM (via findCoverLetterField),
            // not signed-map data -- CSS.escape matches the same
            // defense-in-depth precedent fillCustomTextAnswer/
            // extractCustomQuestions already apply to every other
            // selector built from page-scraped content.
            `input[type="file"][name="${CSS.escape(coverLetterField)}"]`,
            tracked.coverLetter,
            forceRefillAll,
            filesInfo.coverLetterFetchError,
          );
          result.coverLetterAttached = coverLetterOutcome.attached;
          result.coverLetterWritten = coverLetterOutcome.written;
          result.coverLetterError = coverLetterOutcome.error;
        }

        result.unresolvedQuestions = extractCustomQuestions(document, verifiedLeverMap, coverLetterField).map((q) => ({
          fieldName: q.fieldName,
          label: q.label,
          kind: q.kind,
        }));
      } catch (e) {
        result.coverLetterAttached = false;
        result.coverLetterWritten = false;
        result.coverLetterError = null;
        result.unresolvedQuestions = [];
        result.fieldMapError = appendNote(
          result.fieldMapError,
          "This ATS's field map has an invalid selector or pattern -- refusing to use it. " +
            (e instanceof Error ? e.message : ""),
        );
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
    async function fillGreenhousePage(
      tracked: TrackedTabState,
      personalInfo: ExtensionPersonalInfo,
      forceRefillAll: boolean,
      filesInfo: FilesFetchInfo,
      result: FillResult,
    ): Promise<FillResult> {
      const fieldMap = tracked.fieldMap;
      const standardFields = [
        ...greenhouse.GENERIC_FIELD_DEFAULTS.standardFields,
        ...(fieldMap !== null && fieldMap.ats_type === "greenhouse" ? fieldMap.standard_fields : []),
      ];
      const { plan, invalidSelectors, skipped } = planStandardFieldFillsChecked(
        document,
        personalInfo,
        forceRefillAll,
        standardFields,
      );
      noteInvalidSelectors(result, invalidSelectors);
      noteSkipped(result, skipped);
      result.filledFields = applyReactControlledFillPlan(document, plan);

      // `#country` is a list, not a text box: its own path, which picks an option and checks
      // the choice took, or leaves the field alone and says why.
      const country = await greenhouse.fillCountryField(document, personalInfo.location.country, forceRefillAll);
      if (country.status === "filled") result.filledFields.push("#country");
      else if (country.status === "not_filled") noteSkipped(result, [{ field: "Country", reason: country.reason }]);

      const resumeOutcome = tryAttach(
        greenhouse.GENERIC_FIELD_DEFAULTS.resumeSelector,
        tracked.resume,
        forceRefillAll,
        filesInfo.resumeFetchError,
      );
      result.resumeAttached = resumeOutcome.attached;
      result.resumeWritten = resumeOutcome.written;
      result.resumeError = resumeOutcome.error;

      const coverLetterOutcome = tryAttachCoverLetter(
        greenhouse.GENERIC_FIELD_DEFAULTS.coverLetterSelector,
        tracked.coverLetter,
        forceRefillAll,
        filesInfo.coverLetterFetchError,
      );
      result.coverLetterAttached = coverLetterOutcome.attached;
      result.coverLetterWritten = coverLetterOutcome.written;
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
    function fillAshbyPage(
      tracked: TrackedTabState,
      personalInfo: ExtensionPersonalInfo,
      forceRefillAll: boolean,
      filesInfo: FilesFetchInfo,
      result: FillResult,
    ): FillResult {
      const fieldMap = tracked.fieldMap;
      const standardFields = [
        ...ashby.GENERIC_FIELD_DEFAULTS.standardFields,
        ...(fieldMap !== null && fieldMap.ats_type === "ashby" ? fieldMap.standard_fields : []),
      ];
      const { plan, invalidSelectors, skipped } = planStandardFieldFillsChecked(
        document,
        personalInfo,
        forceRefillAll,
        standardFields,
      );
      noteInvalidSelectors(result, invalidSelectors);
      noteSkipped(result, skipped);

      // The phone box has no id this build knows: it is found by what it is (type, autocomplete,
      // label), and left alone -- with the reason shown -- when that does not pick out one box.
      const phone = ashby.planPhoneFill(document, personalInfo.phone, forceRefillAll);
      if (phone.item !== null) plan.push(phone.item);
      if (phone.skipped !== null) noteSkipped(result, [{ field: "Phone", reason: phone.skipped }]);
      result.filledFields = applyReactControlledFillPlan(document, plan);

      const resumeOutcome = tryAttach(
        ashby.GENERIC_FIELD_DEFAULTS.resumeSelector,
        tracked.resume,
        forceRefillAll,
        filesInfo.resumeFetchError,
      );
      result.resumeAttached = resumeOutcome.attached;
      result.resumeWritten = resumeOutcome.written;
      result.resumeError = resumeOutcome.error;

      // The cover letter, found by what the upload is: a file input whose own question says
      // "cover letter". Said, never silent, whenever the person has a cover letter for this
      // application and it did not go in.
      const hasCoverLetter = Boolean(tracked.payload.prepare_result?.cover_letter) || tracked.coverLetter !== null;
      const coverSlot = ashby.findCoverLetterSlot(document);
      if (coverSlot.status === "found") {
        if (tracked.coverLetter === null) {
          result.coverLetterError =
            filesInfo.coverLetterFetchError ?? "No cover letter was generated for this application yet.";
        } else {
          const outcome = tryAttachToInput(coverSlot.input, tracked.coverLetter, forceRefillAll);
          result.coverLetterAttached = outcome.attached;
          result.coverLetterWritten = outcome.written;
          result.coverLetterError = outcome.error;
        }
      } else if (hasCoverLetter) {
        result.coverLetterError =
          coverSlot.status === "ambiguous"
            ? "This form has more than one upload that could be the cover letter, so none was attached -- attach it yourself."
            : "Couldn't find a cover-letter upload on this form, so none was attached. If it has one, attach it yourself.";
      }

      const handledFieldPaths = [
        ...(coverSlot.status === "found" ? [coverSlot.fieldPath] : []),
        ...(phone.fieldPath === null ? [] : [phone.fieldPath]),
      ];
      result.unresolvedQuestions = ashby.extractCustomQuestions(document, handledFieldPaths).map((q) => ({
        fieldName: q.fieldName,
        label: q.label,
        kind: q.kind,
      }));
      return result;
    }

    async function fillPage(tracked: TabState, forceRefillAll: boolean, filesInfo: FilesFetchInfo): Promise<FillResult> {
      const result: FillResult = {
        filledFields: [],
        skippedFields: [],
        resumeAttached: false,
        resumeError: null,
        coverLetterAttached: false,
        coverLetterError: null,
        resumeWritten: false,
        coverLetterWritten: false,
        unresolvedQuestions: [],
        fieldMapError: null,
        fillError: null,
      };
      if (atsType === null || tracked.status !== "tracked" || tracked.payload.personal_info === null) {
        return result;
      }
      const personalInfo = tracked.payload.personal_info;

      // Anything unexpected inside a fill is reported in the result
      // instead of thrown: an exception out of a message listener reaches
      // the side panel as "no reply", which it (correctly) can only render
      // as "still checking" -- forever. What was written before the
      // failure stays reported, since it really did happen.
      try {
        if (atsType === "lever") return fillLeverPage(tracked, personalInfo, forceRefillAll, filesInfo, result);
        if (atsType === "greenhouse") return await fillGreenhousePage(tracked, personalInfo, forceRefillAll, filesInfo, result);
        return fillAshbyPage(tracked, personalInfo, forceRefillAll, filesInfo, result);
      } catch (e) {
        result.fillError = e instanceof Error && e.message !== "" ? e.message : "The fill stopped unexpectedly.";
        return result;
      }
    }

    // GET_DETECTION_STATE: what the panel renders. Also where a form that
    // rendered after page load (or after a client-side route change) gets
    // picked up -- if there is a form, no usable state, and no lookup
    // already running for this URL, start one and wait for it.
    async function getDetectionState(): Promise<DetectionStateResponse> {
      if (!(await consentGranted())) {
        dropCachedState();
        return CONSENT_REQUIRED;
      }
      const formDetected = isFormPresent();
      let tabState = await currentTabState();
      if (formDetected && tabState === null && detectingUrl !== location.href) {
        await detect();
        tabState = await currentTabState();
      }
      return { formDetected, tabState };
    }

    // D3 (browser-extension.md) -- an explicit manual retrigger, shipped
    // from day one: even the best-resourced competitor (Simplify) maintains
    // a permanent "not supported" failure category, so automated detection
    // alone was never going to be sufficient on its own.
    async function recheck(): Promise<DetectionStateResponse> {
      if (!(await consentGranted())) {
        dropCachedState();
        return CONSENT_REQUIRED;
      }
      await detect();
      return { formDetected: isFormPresent(), tabState: await currentTabState() };
    }

    // `null` (not an all-empty FillResult) means "nothing usable for this
    // page yet" -- detection hasn't resolved, or what it resolved belonged
    // to a page or user that is gone (in which case a fresh lookup is
    // started, so the panel's retry finds it). An adversarially-confirmed
    // gap: returning a structurally valid empty result made this
    // indistinguishable from a genuine "nothing to fill."
    async function requestFill(forceRefillAll: boolean): Promise<FillResult | null> {
      if (!(await consentGranted())) {
        dropCachedState();
        return null;
      }
      const state = await currentTabState();
      if (state === null) {
        if (isFormPresent() && detectingUrl !== location.href) void detect();
        return null;
      }
      if (state.status !== "tracked") return await fillPage(state, forceRefillAll, NO_FILE_FETCH_ERRORS);

      // E6 continuation -- this is the moment the résumé/cover-letter PDFs
      // actually get fetched (see GeneratedFile's own doc comment): the
      // first REQUEST_FILL for this tracked application, not detection.
      const { tracked, resumeFetchError, coverLetterFetchError } = await ensureFilesFetched(state);
      // The download can take seconds. Read the flag again before anything is written into the
      // form: consent withdrawn while it ran means nothing is written. (A fill that has already
      // started writing finishes the page it is on -- the country list is asynchronous -- and
      // then stops; it reports nothing, see reportFillOutcome.)
      if (!(await consentGranted())) {
        dropCachedState();
        return null;
      }
      const result = await fillPage(tracked, forceRefillAll, { resumeFetchError, coverLetterFetchError });
      void reportFillOutcome(tracked.applicationId, result);
      return result;
    }

    // One count-only report per finished fill of a tracked application (lib/fillOutcome.ts).
    // Sent only while the person has consented -- re-checked here, after the fill, because
    // consent can be withdrawn while a fill is running -- and never awaited: whether it lands
    // has no bearing on the fill, and a failure of any kind is swallowed. No label, value,
    // URL or page text is in the message; it has no field that could hold one.
    async function reportFillOutcome(applicationId: string, result: FillResult): Promise<void> {
      try {
        if (atsType === null) return;
        const counts = summarizeFill(result);
        if (counts === null) return;
        if (!(await consentGranted())) return;
        const message: ReportFillOutcomeMessage = {
          type: "REPORT_FILL_OUTCOME",
          atsType,
          applicationId,
          fieldsAttempted: counts.fieldsAttempted,
          fieldsFilled: counts.fieldsFilled,
          outcome: counts.outcome,
        };
        await browser.runtime.sendMessage(message);
      } catch {
        // Telemetry must never surface an error.
      }
    }

    // E3b -- the one path a value chosen off-page (a saved answer, an LLM
    // draft) ever reaches the real DOM. Each ATS's own `fillCustomTextAnswer`
    // re-validates the target itself (type, namespace, D6 label, D5
    // emptiness), so this handler doesn't duplicate those checks -- it adds
    // the two that only it can make: the tab still shows the page this
    // was drafted for, and that page is a tracked application (all three
    // ATSs now, not just Lever). Lever additionally needs a verified field
    // map (E3c: its own `custom_question_prefix`, so no map means no fill,
    // D4's fail-closed scope); Greenhouse/Ashby's engines are self-contained.
    async function fillField(fieldName: string, value: string, force: boolean): Promise<FillFieldResult> {
      if (!(await consentGranted())) {
        dropCachedState();
        return { filled: false, reason: "refused" };
      }
      const state = await currentTabState();
      if (state === null) return { filled: false, reason: "page_changed" };
      if (state.status !== "tracked") return { filled: false, reason: "refused" };

      let outcome: FillAnswerOutcome = "refused";
      try {
        if (atsType === "lever") {
          if (state.fieldMap?.ats_type === "lever") {
            outcome = fillCustomTextAnswer(document, state.fieldMap, fieldName, value, force);
          }
        } else if (atsType === "greenhouse") {
          outcome = greenhouse.fillCustomTextAnswer(document, fieldName, value, force);
        } else if (atsType === "ashby") {
          outcome = ashby.fillCustomTextAnswer(document, fieldName, value, force);
        }
      } catch (e) {
        console.error("[between-jobs] filling an answer failed", e);
      }
      return outcome === "filled" ? { filled: true } : { filled: false, reason: outcome };
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
      switch (message.type) {
        case "GET_DETECTION_STATE":
          return getDetectionState();
        case "RECHECK":
          return recheck();
        case "REQUEST_FILL":
          return requestFill(message.forceRefillAll);
        case "FILL_FIELD":
          return fillField(message.fieldName, message.value, message.force ?? false);
      }
    });
  },
});
