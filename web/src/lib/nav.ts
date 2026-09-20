// The primary navigation, as data, so which entries a given state shows is a pure
// function a test can pin.
//
// Per the design book's information architecture: Today / Discover / Applications
// / Practice / Profile. Every section exists from day one with an HONEST empty
// state (what it will be, which stage delivers it) -- no fake data, no
// placeholder screenshots. "Hiring signals" (Hiring Signals P4) sits between
// Discover and Applications -- it is discovery, but of posts rather than
// listings -- and, unlike the others, exists only while the server has the
// feature on (`requiresHiringSignals`): a nav item that leads to "switched off"
// is a dead link.

export interface NavItem {
  to: string;
  label: string;
  end: boolean;
  requiresHiringSignals?: boolean;
}

export const NAV: readonly NavItem[] = [
  { to: "/", label: "Today", end: true },
  { to: "/discover", label: "Discover", end: false },
  { to: "/hiring-signals", label: "Hiring signals", end: false, requiresHiringSignals: true },
  { to: "/applications", label: "Applications", end: false },
  { to: "/practice", label: "Practice", end: false },
  { to: "/profile", label: "Profile", end: false },
];

export function navItemsFor(hiringSignalsEnabled: boolean): readonly NavItem[] {
  return NAV.filter((item) => hiringSignalsEnabled || item.requiresHiringSignals !== true);
}
