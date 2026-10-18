// The primary navigation, as data, so which entries a given state shows is a pure
// function a test can pin.
//
// Today / Discover / Applications / Practice / Profile. Every section exists from day one with
// an HONEST empty state (what it will be) -- no fake data, no placeholder screenshots.
// "Hiring signals" sits between Discover and Applications -- it is discovery, but of posts
// rather than listings -- and, unlike the others, exists only while the server has the feature
// on (`requiresHiringSignals`): a nav item that leads to "switched off" is a dead link.
//
// The same list feeds both layouts: the left rail on a wide screen shows every entry, and on a
// phone the bottom tab bar shows the four marked `tab` while the rest sit in its "More" sheet
// (`phone`), so nothing in the rail is out of reach on a phone. Profile matches its own address
// exactly (`end`), so that Integrations, at /profile/integrations, is the only entry marked there.

import type { IconName } from "../components/icons";
import { INTEGRATIONS_PATH } from "./enrollment";

export interface NavItem {
  to: string;
  label: string;
  end: boolean;
  icon: IconName;
  /** Where the entry sits on a phone: a tab of its own, or in the "More" sheet. */
  phone: "tab" | "more";
  requiresHiringSignals?: boolean;
}

export const NAV: readonly NavItem[] = [
  { to: "/", label: "Today", end: true, icon: "today", phone: "tab" },
  { to: "/discover", label: "Discover", end: false, icon: "discover", phone: "tab" },
  {
    to: "/hiring-signals",
    label: "Hiring signals",
    end: false,
    icon: "hiring-signals",
    phone: "more",
    requiresHiringSignals: true,
  },
  { to: "/applications", label: "Applications", end: false, icon: "applications", phone: "tab" },
  { to: "/practice", label: "Practice", end: false, icon: "practice", phone: "more" },
  { to: "/profile", label: "Profile", end: true, icon: "profile", phone: "tab" },
];

// Integrations is the page for the keys and the Gmail and Telegram connections. It keeps its own name
// until a real Settings page exists, so the label matches the page heading and the copy that sends people
// here. It sits in the rail's footer and in the More sheet rather than in the main list.
export const INTEGRATIONS_ITEM: Omit<NavItem, "phone"> = {
  to: INTEGRATIONS_PATH,
  label: "Integrations",
  end: true,
  icon: "settings",
};

export function navItemsFor(hiringSignalsEnabled: boolean): readonly NavItem[] {
  return NAV.filter((item) => hiringSignalsEnabled || item.requiresHiringSignals !== true);
}

export function phoneTabsFor(hiringSignalsEnabled: boolean): readonly NavItem[] {
  return navItemsFor(hiringSignalsEnabled).filter((item) => item.phone === "tab");
}

export function phoneMoreItemsFor(hiringSignalsEnabled: boolean): readonly NavItem[] {
  return navItemsFor(hiringSignalsEnabled).filter((item) => item.phone === "more");
}
