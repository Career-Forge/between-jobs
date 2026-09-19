import { ApiError, apiFetch, apiFetchBlob } from "@/lib/api";
import { verifyAndParseFieldMap } from "@/lib/ats-field-map";
import type { AtsFieldMap, SignedFieldMapResponse } from "@/lib/ats-field-map";
import { canonicalLookupUrl, isAtsType } from "@/lib/atsHosts";
import { getSupabaseClient } from "@/lib/supabase";
import type {
  AtsType,
  BackgroundMessage,
  DraftAnswerResult,
  ExtensionPayload,
  GeneratedFile,
  MarkAppliedResult,
  MatchAnswerResult,
  TabState,
  VerifySessionResult,
} from "@/lib/types";

// The service worker owns authentication and every backend call
// (browser-extension.md's architecture summary) -- the content script
// never talks to the backend directly. Deliberately stateless: the
// content script that sent PAGE_DETECTED (E4/E5: generalized from the
// Lever-only LEVER_PAGE_DETECTED, now carrying its own detected
// `atsType`) caches the resolved TabState itself (it's the one the side
// panel asks for status/fill), so background doesn't need its own
// per-tab map -- an MV3 worker gets killed and restarted constantly
// anyway, so anything it "remembered" would be unreliable regardless.

async function blobToBase64(blob: Blob): Promise<string> {
  const buffer = await blob.arrayBuffer();
  let binary = "";
  for (const byte of new Uint8Array(buffer)) binary += String.fromCharCode(byte);
  return btoa(binary);
}

async function fetchGeneratedFile(
  applicationId: string,
  kind: "resume" | "cover-letter",
): Promise<GeneratedFile> {
  const blob = await apiFetchBlob(`/applications/${applicationId}/${kind}.pdf`);
  return { base64: await blobToBase64(blob), filename: `${kind}.pdf` };
}

const FIELD_MAP_VERSION_STORAGE_PREFIX = "fieldMapVersion:";

// Adversarially-confirmed gap: Ed25519 verification alone proves a
// payload was genuinely signed at SOME point, not that it's the CURRENT
// version -- publishing is append-only (scripts/sign_and_publish_ats_
// field_map.py never updates/deletes a row), so every past version
// stays validly signed forever. A compromised or buggy intermediary
// with only READ access to the ats_field_maps table -- no access to the
// private key at all -- could replay an old, since-corrected row
// indefinitely and verifyAndParseFieldMap would accept it: it only
// checks the payload's own internal ats_type/version/schema binding,
// never "is this the version I've seen before." Remembering the highest
// version ever accepted per ats_type (chrome.storage.local, not
// `.session` -- this needs to survive a full browser restart to be a
// real defense, and unlike the Supabase auth token it's a plain version
// number with no content-script-exposure risk) and refusing anything
// lower closes this the same way TUF's own monotonic version check
// defends against a rollback attack.
async function getMinimumAcceptableFieldMapVersion(atsType: string): Promise<number> {
  const key = `${FIELD_MAP_VERSION_STORAGE_PREFIX}${atsType}`;
  const stored = (await chrome.storage.local.get(key)) as Record<string, unknown>;
  return typeof stored[key] === "number" ? stored[key] : 0;
}

async function recordAcceptedFieldMapVersion(atsType: string, version: number): Promise<void> {
  const key = `${FIELD_MAP_VERSION_STORAGE_PREFIX}${atsType}`;
  await chrome.storage.local.set({ [key]: version });
}

// The ratchet is a read-check-write, and two detections can overlap (a
// RECHECK during the initial one, two ATS tabs opening together). Without
// serializing them, each reads the same stored minimum, both pass, and
// whichever writes last wins -- so a LOWER version accepted second could
// re-lower the ratchet after a higher one raised it. Chained through one
// promise so the check and the write for one map finish before the next
// map's check starts.
let ratchetQueue: Promise<unknown> = Promise.resolve();
function withRatchetLock<T>(work: () => Promise<T>): Promise<T> {
  const run = ratchetQueue.then(work);
  ratchetQueue = run.catch(() => undefined);
  return run;
}

// E3c (generalized in E4/E5 -- see lib/types.ts's own TabState note for
// why Greenhouse/Ashby always get `map: null` here today, harmlessly) --
// deliberately its OWN try/catch, not folded into the payload/résumé
// fetch below: a field-map outage must degrade to "the ATS-idiosyncratic
// behavior is unavailable," never to "the whole tab state resolution
// failed," since the open-source GENERIC_FIELD_DEFAULTS fields (name/
// email/phone/résumé) have nothing to do with this fetch and should keep
// working regardless (D4's fail-closed scope is the signed data
// specifically, not the whole extension). Every failure mode -- network
// error, no map published yet (404), a bad signature, an unrecognized
// signing key, a rollback attempt -- collapses to the same `map: null`
// outcome; `error` just carries a human-readable reason for the side
// panel, never a distinction content.ts needs to act on differently.
async function fetchFieldMap(atsType: AtsType): Promise<{ map: AtsFieldMap | null; error: string | null }> {
  try {
    const response = await apiFetch<SignedFieldMapResponse>(`/extension/field-maps/${atsType}`);
    const map = await verifyAndParseFieldMap(response);
    if (map === null) {
      return {
        map: null,
        error: "This ATS's field map failed signature verification -- refusing to use it.",
      };
    }
    // The signature and the payload's own ats_type/version/schema binding
    // are checked against the (unsigned) response metadata, which whoever
    // serves the response also controls -- so that check can't tell a
    // validly-signed Lever map served under /greenhouse from the real
    // thing. What this extension actually ASKED for is the only trustworthy
    // reference. Checked before the ratchet is touched: a wrong-type map
    // must never advance (poison) the version floor of the ATS it claims
    // to be, or a genuine map for that ATS would be refused as "older."
    if (map.ats_type !== atsType) {
      return {
        map: null,
        error: "This ATS's field map is for a different ATS -- refusing to use it.",
      };
    }
    const accepted = await withRatchetLock(async () => {
      const minimumVersion = await getMinimumAcceptableFieldMapVersion(atsType);
      if (map.version < minimumVersion) return false;
      await recordAcceptedFieldMapVersion(atsType, map.version);
      return true;
    });
    if (!accepted) {
      return {
        map: null,
        error: "This ATS's field map is an older version than one already seen -- refusing to use it.",
      };
    }
    return { map, error: null };
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) {
      return { map: null, error: "No field map has been published for this ATS yet." };
    }
    return {
      map: null,
      error: e instanceof Error ? e.message : "Failed to fetch this ATS's field map.",
    };
  }
}

async function currentUserId(): Promise<string | null> {
  const { data } = await getSupabaseClient().auth.getSession();
  return data.session?.user.id ?? null;
}

// Answers a content script's "is this still the signed-in user?" with a
// boolean only. Any failure (no session, refresh failed, storage error)
// is "no" -- fail closed.
async function verifySession(userId: string): Promise<VerifySessionResult> {
  try {
    return { valid: (await currentUserId()) === userId };
  } catch {
    return { valid: false };
  }
}

async function resolveTabState(url: string, atsType: AtsType): Promise<TabState> {
  // Only ever ask the backend about the tab's posting URL on the ATS's own
  // host -- no query string or fragment (tracking parameters, and on some
  // ATS links per-candidate tokens, don't belong in a GET query string that
  // lands in access logs) and the ATS's form-route suffix removed so it
  // matches the URL the job was tracked under. Null means the URL isn't
  // one this ATS legitimately serves.
  const lookupUrl = canonicalLookupUrl(url, atsType);
  if (lookupUrl === null) {
    return { status: "error", message: "This page's address isn't one this extension can look up." };
  }

  let userId: string | null;
  try {
    userId = await currentUserId();
  } catch (e) {
    return { status: "error", message: e instanceof Error ? e.message : "Couldn't read the signed-in session." };
  }
  if (userId === null) return { status: "signed_out" };

  let applicationId: string;
  try {
    const lookup = await apiFetch<{ application_id: string | null }>(
      `/extension/lookup?url=${encodeURIComponent(lookupUrl)}`,
    );
    if (lookup.application_id === null) {
      return { status: "untracked" };
    }
    applicationId = lookup.application_id;
  } catch (e) {
    if (e instanceof ApiError && e.status === 401) {
      return { status: "signed_out" };
    }
    return { status: "error", message: e instanceof Error ? e.message : "Lookup failed" };
  }

  try {
    // The field-map fetch never throws (fetchFieldMap catches
    // everything itself) and doesn't depend on `payload`, so it runs
    // concurrently with it rather than after -- a free latency win, not
    // a correctness-sensitive ordering.
    const [payload, fieldMapResult] = await Promise.all([
      apiFetch<ExtensionPayload>(`/applications/${applicationId}/extension-payload`),
      fetchFieldMap(atsType),
    ]);
    const resume = payload.prepare_result?.resume
      ? await fetchGeneratedFile(applicationId, "resume")
      : null;
    const coverLetter = payload.prepare_result?.cover_letter
      ? await fetchGeneratedFile(applicationId, "cover-letter")
      : null;
    return {
      status: "tracked",
      applicationId,
      userId,
      payload,
      resume,
      coverLetter,
      fieldMap: fieldMapResult.map,
      fieldMapError: fieldMapResult.error,
    };
  } catch (e) {
    if (e instanceof ApiError && e.status === 401) {
      return { status: "signed_out" };
    }
    return {
      status: "error",
      message: e instanceof Error ? e.message : "Failed to fetch prepared payload",
    };
  }
}

// Tracking confirmation (browser-extension.md's "concrete design" note):
// a new caller of the same `change_application_stage` RPC K1 already
// built, via the existing `POST /applications/{id}/stage` route --
// nothing new on the backend. The human clicks "Mark as applied" in the
// side panel; this never fires on its own.
async function markApplied(applicationId: string, idempotencyKey: string): Promise<MarkAppliedResult> {
  try {
    const result = await apiFetch<{ status: string }>(`/applications/${applicationId}/stage`, {
      method: "POST",
      body: JSON.stringify({ new_status: "applied", idempotency_key: idempotencyKey }),
    });
    return { ok: true, status: result.status };
  } catch (e) {
    return { ok: false, message: e instanceof Error ? e.message : "Failed to update status" };
  }
}

// E3b's known-question-memory calls -- both routes already existed
// (E1); this is the first time anything in the extension actually calls
// them. `answer` errors fail open to "no match" rather than surfacing an
// error state, since the side panel's own fallback (offer to draft) is
// always a reasonable next step either way.
async function matchAnswer(
  normalizedQuestion: string,
  canonicalIntent: string | undefined,
): Promise<MatchAnswerResult> {
  try {
    return await apiFetch<MatchAnswerResult>("/extension/match-answer", {
      method: "POST",
      body: JSON.stringify({ normalized_question: normalizedQuestion, canonical_intent: canonicalIntent }),
    });
  } catch {
    return { answer: null };
  }
}

async function saveAnswer(normalizedQuestion: string, answerText: string): Promise<void> {
  await apiFetch("/extension/approved-answers", {
    method: "POST",
    body: JSON.stringify({ normalized_question: normalizedQuestion, answer_text: answerText }),
  });
}

// E3b's LLM-fallback path -- MASTER_PLAN's "CoverForge-lite." Only ever
// invoked by an explicit side-panel click on a `kind: "text"` question
// that already missed the known-question-memory check above; drafts
// only, never fills or saves anything itself.
async function draftAnswer(applicationId: string, questionText: string): Promise<DraftAnswerResult> {
  return apiFetch<DraftAnswerResult>("/extension/draft-answer", {
    method: "POST",
    body: JSON.stringify({ application_id: applicationId, question_text: questionText }),
  });
}

// ---- message validation ---------------------------------------------------
//
// The service worker is the only context that holds the session and talks
// to the backend, and it hears from two very different senders: the side
// panel (an extension page), and content scripts -- which run next to
// attacker-controllable page code and are the least-trusted extension
// context. Each message type is accepted only from the sender kind that
// legitimately sends it, and every field that ends up in a request path or
// body is type- and size-checked. Nothing reachable by web page script
// today (no `externally_connectable`, no page-facing bridge), so this is
// defense in depth against a future content-script bug or a renderer
// compromise, not a fix for a live exploit.

type SenderKind = "extension_page" | "content_script" | "other";

function classifySender(sender: Browser.runtime.MessageSender): SenderKind {
  if (sender.id !== browser.runtime.id) return "other";
  // A content script's `sender.url`/`origin` are the PAGE's; an extension
  // page's are the extension's own.
  const extensionUrl = browser.runtime.getURL("");
  const extensionOrigin = extensionUrl.replace(/\/$/, "");
  if ((sender.url !== undefined && sender.url.startsWith(extensionUrl)) || sender.origin === extensionOrigin) {
    return "extension_page";
  }
  return sender.tab?.id !== undefined ? "content_script" : "other";
}

const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const MAX_QUESTION_LENGTH = 500;
const MAX_ANSWER_LENGTH = 5000;
const MAX_KEY_LENGTH = 200;

function isBoundedString(value: unknown, max: number): value is string {
  return typeof value === "string" && value.length > 0 && value.length <= max;
}

function invalidRequest(): Promise<never> {
  return Promise.reject(new Error("Invalid request."));
}

export default defineBackground(() => {
  // Clicking the toolbar icon opens the side panel directly, rather than
  // needing a separate click handler per Chrome's own current API.
  chrome.sidePanel?.setPanelBehavior({ openPanelOnActionClick: true }).catch(() => {});

  browser.runtime.onMessage.addListener((message: BackgroundMessage, sender) => {
    if (typeof message !== "object" || message === null) return;
    const kind = classifySender(sender);

    switch (message.type) {
      case "PAGE_DETECTED":
        if (kind !== "content_script") return;
        if (!isAtsType(message.atsType) || typeof message.url !== "string") return invalidRequest();
        return resolveTabState(message.url, message.atsType);
      case "VERIFY_SESSION":
        if (kind !== "content_script") return;
        if (!isBoundedString(message.userId, MAX_KEY_LENGTH)) return invalidRequest();
        return verifySession(message.userId);
      case "MARK_APPLIED":
        if (kind !== "extension_page") return;
        if (!UUID_PATTERN.test(message.applicationId) || !isBoundedString(message.idempotencyKey, MAX_KEY_LENGTH)) {
          return invalidRequest();
        }
        return markApplied(message.applicationId, message.idempotencyKey);
      case "MATCH_ANSWER":
        if (kind !== "extension_page") return;
        if (
          !isBoundedString(message.normalizedQuestion, MAX_QUESTION_LENGTH) ||
          (message.canonicalIntent !== undefined && !isBoundedString(message.canonicalIntent, MAX_KEY_LENGTH))
        ) {
          return invalidRequest();
        }
        return matchAnswer(message.normalizedQuestion, message.canonicalIntent);
      case "SAVE_ANSWER":
        if (kind !== "extension_page") return;
        if (
          !isBoundedString(message.normalizedQuestion, MAX_QUESTION_LENGTH) ||
          !isBoundedString(message.answerText, MAX_ANSWER_LENGTH)
        ) {
          return invalidRequest();
        }
        return saveAnswer(message.normalizedQuestion, message.answerText);
      case "DRAFT_ANSWER":
        if (kind !== "extension_page") return;
        if (!UUID_PATTERN.test(message.applicationId) || !isBoundedString(message.questionText, MAX_QUESTION_LENGTH)) {
          return invalidRequest();
        }
        return draftAnswer(message.applicationId, message.questionText);
    }
  });
});
