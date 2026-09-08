import { ApiError, apiFetch, apiFetchBlob } from "@/lib/api";
import type { BackgroundMessage, ExtensionPayload, GeneratedFile, MarkAppliedResult, TabState } from "@/lib/types";

// The service worker owns authentication and every backend call
// (browser-extension.md's architecture summary) -- the content script
// never talks to the backend directly. Deliberately stateless: the
// content script that sent LEVER_PAGE_DETECTED caches the resolved
// TabState itself (it's the one the side panel asks for status/fill),
// so background doesn't need its own per-tab map -- an MV3 worker gets
// killed and restarted constantly anyway, so anything it "remembered"
// would be unreliable regardless.

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

async function resolveTabState(url: string): Promise<TabState> {
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
    const payload = await apiFetch<ExtensionPayload>(
      `/applications/${applicationId}/extension-payload`,
    );
    const resume = payload.prepare_result?.resume
      ? await fetchGeneratedFile(applicationId, "resume")
      : null;
    const coverLetter = payload.prepare_result?.cover_letter
      ? await fetchGeneratedFile(applicationId, "cover-letter")
      : null;
    return { status: "tracked", applicationId, payload, resume, coverLetter };
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

export default defineBackground(() => {
  // Clicking the toolbar icon opens the side panel directly, rather than
  // needing a separate click handler per Chrome's own current API.
  chrome.sidePanel?.setPanelBehavior({ openPanelOnActionClick: true }).catch(() => {});

  browser.runtime.onMessage.addListener((message: BackgroundMessage) => {
    if (message.type === "LEVER_PAGE_DETECTED") {
      return resolveTabState(message.url);
    }
    if (message.type === "MARK_APPLIED") {
      return markApplied(message.applicationId, message.idempotencyKey);
    }
  });
});
