// The public site's tabs, in header order. Product is the home page. Terms is a page of the site but is
// not a tab: it is reached from the footer and from the sign-in page, like the Privacy Policy used to be.
// The labels are the pages' own names (lib/publicRoutes.ts) where they have one.

import { HOW_IT_WORKS_PAGE_NAME, HOW_IT_WORKS_PATH, LANDING_PATH, OPEN_SOURCE_PAGE_NAME, OPEN_SOURCE_PATH, PRIVACY_PATH } from "../lib/publicRoutes";

export interface SiteTab {
  label: string;
  path: string;
}

export const SITE_TABS: readonly SiteTab[] = [
  { label: "Product", path: LANDING_PATH },
  { label: HOW_IT_WORKS_PAGE_NAME, path: HOW_IT_WORKS_PATH },
  { label: OPEN_SOURCE_PAGE_NAME, path: OPEN_SOURCE_PATH },
  { label: "Privacy", path: PRIVACY_PATH },
];
