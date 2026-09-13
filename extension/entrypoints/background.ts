import { ApiError, apiFetch, apiFetchBlob } from "@/lib/api";
import { verifyAndParseFieldMap } from "@/lib/ats-field-map";
import type { AtsFieldMap, SignedFieldMapResponse } from "@/lib/ats-field-map";
import type {
  AtsType,
  BackgroundMessage,
  DraftAnswerResult,
  ExtensionPayload,
  GeneratedFile,
  MarkAppliedResult,
  MatchAnswerResult,
  TabState,
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
    const minimumVersion = await getMinimumAcceptableFieldMapVersion(atsType);
    if (map.version < minimumVersion) {
      return {
        map: null,
        error: "This ATS's field map is an older version than one already seen -- refusing to use it.",
      };
    }
    await recordAcceptedFieldMapVersion(atsType, map.version);
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

async function resolveTabState(url: string, atsType: AtsType): Promise<TabState> {
  let applicationId: string;
  try {
    const lookup = await apiFetch<{ application_id: string | null }>(
      `/extension/lookup?url=${encodeURIComponent(url)}`,
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

export default defineBackground(() => {
  // Clicking the toolbar icon opens the side panel directly, rather than
  // needing a separate click handler per Chrome's own current API.
  chrome.sidePanel?.setPanelBehavior({ openPanelOnActionClick: true }).catch(() => {});

  browser.runtime.onMessage.addListener((message: BackgroundMessage) => {
    if (message.type === "PAGE_DETECTED") {
      return resolveTabState(message.url, message.atsType);
    }
    if (message.type === "MARK_APPLIED") {
      return markApplied(message.applicationId, message.idempotencyKey);
    }
    if (message.type === "MATCH_ANSWER") {
      return matchAnswer(message.normalizedQuestion, message.canonicalIntent);
    }
    if (message.type === "SAVE_ANSWER") {
      return saveAnswer(message.normalizedQuestion, message.answerText);
    }
    if (message.type === "DRAFT_ANSWER") {
      return draftAnswer(message.applicationId, message.questionText);
    }
  });
});
