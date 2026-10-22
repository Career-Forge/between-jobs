// Data-location side-notes for model providers: a plain sentence about where what you send ends up,
// for the providers whose service runs somewhere a person may want to know about before they connect.
//
// It is a SIDE-NOTE, not a warning. It is shown as ordinary muted text next to the key form and under
// the provider's connected row, with nothing to accept and no colour of its own. It says where the
// provider runs and what goes there; it does not say whether that is acceptable.
//
// ONE ENTRY PER PROVIDER ID (the API registry's ids, src/between_jobs/api/provider_registry.py: a
// test holds the keys to it). A provider with no entry has no note. Adding a provider's note is adding
// one entry here: nothing in the page changes. There is no entry for Moonshot (Kimi) because the
// registry has no such provider.
//
// Each sentence says only as much as its source supports, and the source is named so it can be
// checked again:
//
//   Qwen Cloud  Alibaba Cloud Model Studio's region list, as the registry has it
//               (`_MODEL_STUDIO_REGIONS` in provider_registry.py). A Model Studio key is bound to one
//               region, and one of the choices, China (Beijing), is in mainland China. China (Hong
//               Kong) is not mainland, so it gets the general sentence only.
//   DeepSeek    DeepSeek's Privacy Policy, last updated Feb 10, 2026
//               (https://cdn.deepseek.com/policies/en-US/deepseek-privacy-policy.html, read
//               2026-10-22). It names Hangzhou DeepSeek Artificial Intelligence Co., Ltd., with a
//               registered address in China, as the party that provides and controls the services, and
//               says the personal data it collects is processed and stored in the People's Republic of
//               China. Its stated scope is DeepSeek's apps, websites, software and related services,
//               and it covers the platform's payment data; it does not say in so many words that an API
//               request is a "service" under it, so the sentence below says who operates DeepSeek and
//               what the policy says about its processing, and no more.
//
// Pure module: no React, no network.

import { QWEN_ID } from "./settings/llmProviders";

interface NoteSpec {
  /** Shown for every case of the provider. */
  text: string;
  /** Region ids (the registry's) that are in mainland China; the note then says so of the one picked. */
  mainlandRegions?: readonly string[];
}

const MAINLAND_SENTENCE = "The one you picked is in mainland China.";

const NOTES: Readonly<Record<string, NoteSpec>> = {
  [QWEN_ID]: {
    text: "Qwen Cloud runs on Alibaba Cloud. What you send goes to the region you pick, and some regions are in mainland China.",
    mainlandRegions: ["cn-beijing"],
  },
  deepseek: {
    text: "DeepSeek is operated by a company based in China. What you send goes to its servers, and its privacy policy says it collects, processes and stores personal data in the People's Republic of China.",
  },
};

/** The ids that have a note, for the test that holds them to the registry. */
export const NOTED_PROVIDERS: readonly string[] = Object.keys(NOTES);

/** The mainland-China region ids a note names, for the same test. */
export function mainlandRegionIds(providerId: string): readonly string[] {
  return Object.hasOwn(NOTES, providerId) ? (NOTES[providerId].mainlandRegions ?? []) : [];
}

/**
 * The side-note for a provider, or null when it has none. `regionId` is the region picked (Qwen's);
 * null or an id nothing lists is "no region chosen yet", which gets the general sentence.
 */
export function providerNote(providerId: string, regionId: string | null = null): string | null {
  if (!Object.hasOwn(NOTES, providerId)) return null;
  const spec = NOTES[providerId];
  const mainland = regionId !== null && (spec.mainlandRegions ?? []).includes(regionId);
  return mainland ? `${spec.text} ${MAINLAND_SENTENCE}` : spec.text;
}
