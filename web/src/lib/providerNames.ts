// How a search or research provider is named for a person, in one place. The Discover rows and
// warnings, Hiring signals' lines, a research run's "providers used" and the Settings cards all say
// the same provider's name, so a rename is one edit here. An id this table does not know is shown as
// the server sent it: nothing is guessed about a provider the page has not heard of.
//
// Model providers (OpenAI, Anthropic, ...) are named by the server's catalogue, not here
// (lib/settings/llmProviders.ts). Pure module: no React, no network.

export const PROVIDER_NAMES = {
  you_com: "You.com",
  firecrawl: "Firecrawl",
  brave: "Brave Search",
  serper: "Serper",
  github: "GitHub",
  remoteok: "RemoteOK",
  arbeitnow: "Arbeitnow",
  jsearch: "JSearch",
  adzuna: "Adzuna",
  usajobs: "USAJobs",
  apollo: "Apollo",
  hunter: "Hunter",
  exa: "Exa",
} as const;

export function providerDisplayName(id: string): string {
  return Object.prototype.hasOwnProperty.call(PROVIDER_NAMES, id) ? PROVIDER_NAMES[id as keyof typeof PROVIDER_NAMES] : id;
}
