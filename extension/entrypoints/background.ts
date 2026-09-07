import { ApiError, apiFetch, apiFetchBlob } from "@/lib/api";
import type { BackgroundMessage, ExtensionPayload, TabState } from "@/lib/types";

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
    let resume = null;
    if (payload.prepare_result?.resume) {
      const blob = await apiFetchBlob(`/applications/${applicationId}/resume.pdf`);
      resume = { base64: await blobToBase64(blob), filename: "resume.pdf" };
    }
    return { status: "tracked", applicationId, payload, resume };
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

export default defineBackground(() => {
  // Clicking the toolbar icon opens the side panel directly, rather than
  // needing a separate click handler per Chrome's own current API.
  chrome.sidePanel?.setPanelBehavior({ openPanelOnActionClick: true }).catch(() => {});

  browser.runtime.onMessage.addListener((message: BackgroundMessage) => {
    if (message.type !== "LEVER_PAGE_DETECTED") return;
    return resolveTabState(message.url);
  });
});
