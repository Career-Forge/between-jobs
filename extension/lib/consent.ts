// The in-product consent flag, and the ONE place that decides whether it is
// good enough. The side panel shows the disclosure and writes the flag; the
// content script (reads a job page) and the service worker (sends addresses
// and questions to the API) each ask this module before they do anything that
// collects or handles the person's data, so none of the three can drift into
// its own idea of "has the person agreed".
//
// The flag carries a VERSION, not a bare boolean, so a release that changes
// what the extension collects can force a fresh agreement by bumping
// CONSENT_VERSION. The comparison is equality: a stored version that is newer
// than this build's is not a match either, because this build cannot know what
// that newer text promised.

export const CONSENT_STORAGE_KEY = "disclosureConsent";
// 2: the count-only fill report was added, which the first wording ("no analytics") did not
// cover, so everyone who agreed to version 1 is asked again. Version 2's screen also lists what
// goes with it: the application's id on the report, the saved-answer use report, and the
// question's kind and the job's country that travel with a question's text.
export const CONSENT_VERSION = 2;

export interface StoredConsent {
  version: number;
}

/** What the service worker refuses with when a request arrives while the flag is
 * not valid. The side panel recognises it and re-reads the flag. */
export const CONSENT_REQUIRED_MESSAGE = "Consent is required before the extension can do this.";

/** `granted` only for a stored flag whose version is exactly this build's. */
export type ConsentDecision = "granted" | "needed";

export function isStoredConsent(value: unknown): value is StoredConsent {
  return typeof value === "object" && value !== null && typeof (value as { version?: unknown }).version === "number";
}

/**
 * The single decision point. Pure: whatever was read out of storage (nothing,
 * a malformed value, an older or newer version) goes in, and anything that is
 * not an exact match for `currentVersion` comes out as `needed`. Fail closed.
 */
export function consentDecision(stored: unknown, currentVersion: number = CONSENT_VERSION): ConsentDecision {
  return isStoredConsent(stored) && stored.version === currentVersion ? "granted" : "needed";
}

/** The slice of `chrome.storage.local` this module touches. */
export interface ConsentStorageArea {
  get(key: string): Promise<Record<string, unknown>>;
}

/**
 * Reads the stored flag and decides. Reads fresh on every call -- the answer is
 * never cached by the caller, so a withdrawal or a version change takes effect
 * on the very next thing the content script or the service worker is asked to
 * do, in every tab that is already open. A missing storage API or a failed read
 * is `needed`: if the flag cannot be read, nothing is collected.
 */
export async function readConsentDecision(
  storage?: ConsentStorageArea,
  currentVersion: number = CONSENT_VERSION,
): Promise<ConsentDecision> {
  try {
    const area = storage ?? chrome.storage.local;
    const result = await area.get(CONSENT_STORAGE_KEY);
    return consentDecision(result[CONSENT_STORAGE_KEY], currentVersion);
  } catch {
    return "needed";
  }
}

/** One entry of a `chrome.storage.onChanged` payload. */
interface StorageChange {
  newValue?: unknown;
}

/**
 * What a `chrome.storage.onChanged` event says about consent: the new decision
 * when the flag itself changed in the local area, `null` for anything else.
 * Used to drop cached personal data the moment consent stops being valid; the
 * per-use read above is still what gates each action.
 */
export function consentDecisionFromChange(
  changes: Record<string, StorageChange>,
  areaName: string,
  currentVersion: number = CONSENT_VERSION,
): ConsentDecision | null {
  if (areaName !== "local") return null;
  const change = changes[CONSENT_STORAGE_KEY];
  if (change === undefined) return null;
  return consentDecision(change.newValue, currentVersion);
}
