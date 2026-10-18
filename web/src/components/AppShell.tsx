import type { ReactNode } from "react";
import { Link, NavLink } from "react-router-dom";
import { SETTINGS_ITEM } from "../lib/nav";
import { BottomTabBar } from "./BottomTabBar";
import { Icon } from "./icons";
import { LegalLinks } from "./LegalLinks";
import { PrimaryNav } from "./PrimaryNav";
import { AssistantDrawerSlot, CommandPaletteSlot } from "./ReservedSlots";
import { MAIN_CONTENT_ID, SkipLink } from "./SkipLink";
import { Wordmark } from "./Wordmark";

// The signed-in frame around every page. At 960px and wider: a 220px rail on the left (wordmark,
// the main navigation, then the address, Settings, Sign out and the legal links) and the page
// beside it. Under 960px: a slim top bar with the wordmark, the page, and
// a bottom tab bar with a More sheet. Both navigations are always in the markup; CSS shows one.
//
// The page is the one <main>, and the skip link's target. The tester-programme gate and the routes
// are passed in as children (App.tsx), so what the shell draws does not depend on them.

export interface AppShellProps {
  email: string | null;
  onSignOut: () => void;
  children: ReactNode;
}

export function AppShell({ email, onSignOut, children }: AppShellProps) {
  return (
    <div className="bj-shell">
      <SkipLink />
      <header className="bj-topbar">
        <Link to="/" className="bj-topbar-brand">
          <Wordmark />
        </Link>
      </header>
      <div className="bj-rail">
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
      </div>
      <main className="bj-main" id={MAIN_CONTENT_ID} tabIndex={-1}>
        {children}
      </main>
      <BottomTabBar email={email} onSignOut={onSignOut} />
      <AssistantDrawerSlot />
    </div>
  );
}
