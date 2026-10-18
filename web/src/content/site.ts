// Facts the public pages share: where the source lives and the two ways to reach us. One place,
// so the landing page, the footers and the legal text cannot drift apart. The content test
// (legal.test.ts) pins that these two addresses are the only ones written in the legal text.

// The full text of the licenses of the self-hosted typefaces, served from public/licenses/ on the
// site's own origin (src/fonts.test.ts checks it holds each family's license).
export const FONT_LICENSES_PATH = "/licenses/fonts.txt";

export const REPO_URL = "https://github.com/Career-Forge/between-jobs";

export const PRIVACY_EMAIL = "privacy@between-jobs.tech";
export const SUPPORT_EMAIL = "support@between-jobs.tech";

// The license of the source code in the repository (its LICENSE file).
export const SOURCE_LICENSE = "AGPL-3.0";
