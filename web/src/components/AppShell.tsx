import { useState, type ReactNode } from "react";
import { Link, NavLink, useLocation } from "react-router-dom";
import { SETTINGS_ITEM } from "../lib/nav";
import { SETTINGS_PATH } from "../lib/settingsPaths";
import { BottomTabBar } from "./BottomTabBar";
import { Icon } from "./icons";
import { LegalLinks } from "./LegalLinks";
import { PrimaryNav } from "./PrimaryNav";
import { AssistantDrawerSlot, CommandPaletteSlot } from "./ReservedSlots";
import { NextStepSkipLink } from "./NextStepSkipLink";
import { ShellBandContext } from "./ShellBandContext";
import { MAIN_CONTENT_ID, SkipLink } from "./SkipLink";
import { Wordmark } from "./Wordmark";

// The signed-in frame around every page. At 960px and wider: a 220px rail on the left (wordmark,
// the main navigation, then the address, Settings, Sign out and the legal links) and the page
// beside it. The rail is an <aside> named "Sidebar", so everything in it sits inside a landmark. Under 960px: a slim top bar with the wordmark, the page, and
// a bottom tab bar with a More sheet. Both navigations are always in the markup; CSS shows one.
//
// The page is the one <main>, and the skip link's target (the band's button is the other). The tester-programme gate and the routes
// are passed in as children (App.tsx), so what the shell draws does not depend on them.

// The signed-in pages that draw a next-step band (Today at "/", Discover, Applications, Practice, Hiring signals, Profile
// and Settings; an application's workspace too, see pageHasBand): on these the first tab stop is "Skip to next step",
// ahead of "Skip to content". A page not listed has no band to skip to.
// AppShell.test.tsx reads the sources and fails when a signed-in page draws a band that is not here.
export const PATHS_WITH_A_BAND: readonly string[] = ["/", "/discover", "/applications", "/practice", "/hiring-signals", "/profile", SETTINGS_PATH];

// A page whose address carries an id cannot be listed by one string: an application's workspace
// (/applications/<id>) draws a band too.
const WORKSPACE_PATH = /^\/applications\/[^/]+\/?$/;

export function pageHasBand(pathname: string): boolean {
  return PATHS_WITH_A_BAND.includes(pathname) || WORKSPACE_PATH.test(pathname);
}

export interface AppShellProps {
  email: string | null;
  onSignOut: () => void;
  children: ReactNode;
}

export function AppShell({ email, onSignOut, children }: AppShellProps) {
  const { pathname } = useLocation();
  // Set by whatever stands in for the page without a band of its own (the tester-programme gate).
  const [drawsNoBand, setDrawsNoBand] = useState(false);
  return (
    <div className="bj-shell">
      {pageHasBand(pathname) && !drawsNoBand && <NextStepSkipLink />}
      <SkipLink />
      <header className="bj-topbar">
        <Link to="/" className="bj-topbar-brand">
          <Wordmark />
        </Link>
      </header>
      <aside className="bj-rail" aria-label="Sidebar">
        <Link to="/" className="bj-rail-brand">
          <Wordmark />
        </Link>
        <CommandPaletteSlot />
        <PrimaryNav />
        <div className="bj-nav-footer">
          {email !== null && <p className="bj-rail-email">{email}</p>}
          <NavLink to={SETTINGS_ITEM.to} end={SETTINGS_ITEM.end} className="bj-nav-item bj-selectable">
            <Icon name={SETTINGS_ITEM.icon} />
            {SETTINGS_ITEM.label}
          </NavLink>
          <button type="button" className="bj-rail-signout" onClick={onSignOut}>
            Sign out
          </button>
          <LegalLinks />
        </div>
      </aside>
      <main className="bj-main" id={MAIN_CONTENT_ID} tabIndex={-1}>
        <ShellBandContext.Provider value={setDrawsNoBand}>{children}</ShellBandContext.Provider>
      </main>
      <BottomTabBar email={email} onSignOut={onSignOut} />
      <AssistantDrawerSlot />
    </div>
  );
}
