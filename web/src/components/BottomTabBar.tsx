import { useEffect, useRef } from "react";
import { NavLink, useLocation } from "react-router-dom";
import { phoneMoreItemsFor, phoneTabsFor } from "../lib/nav";
import { useHiringSignalsEnabled } from "../lib/useHiringSignalsStatus";
import { Icon } from "./icons";
import { MoreSheet } from "./MoreSheet";

// The phone's navigation, under 960px (CSS decides; this always renders and is display:none above
// that, where the rail takes over): four tabs and a More button, in a five-column grid, each at
// least 44px tall with a 12px label. The selected tab is the selection primitive -- NavLink's
// aria-current="page" plus the maroon fill from tokens.css.
//
// More opens a sheet with the entries that have no tab. It is a button with a popup, not a link,
// and stays unselected: it leads to several pages, so none of them is "this" one.

export function BottomTabBar({ email, onSignOut }: { email: string | null; onSignOut: () => void }) {
  const hiringSignalsEnabled = useHiringSignalsEnabled();
  const sheet = useRef<HTMLDialogElement>(null);
  const { pathname } = useLocation();

  // A page change leaves the sheet behind, and so does a screen that grows past the phone layout
  // (a rotated tablet), where the sheet would otherwise stay open but hidden.
  useEffect(() => {
    if (sheet.current?.open) sheet.current.close();
  }, [pathname]);
  useEffect(() => {
    const wide = window.matchMedia("(min-width: 960px)");
    const closeWhenWide = () => {
      if (wide.matches && sheet.current?.open) sheet.current.close();
    };
    wide.addEventListener("change", closeWhenWide);
    return () => wide.removeEventListener("change", closeWhenWide);
  }, []);

  return (
    <>
      <nav aria-label="Main" className="bj-tabbar">
        {phoneTabsFor(hiringSignalsEnabled).map((item) => (
          <NavLink key={item.to} to={item.to} end={item.end} className="bj-tab bj-selectable">
            <Icon name={item.icon} />
            <span>{item.label}</span>
          </NavLink>
        ))}
        <button type="button" className="bj-tab" aria-haspopup="dialog" onClick={() => sheet.current?.showModal()}>
          <Icon name="more" />
          <span>More</span>
        </button>
      </nav>
      <MoreSheet dialogRef={sheet} items={phoneMoreItemsFor(hiringSignalsEnabled)} email={email} onSignOut={onSignOut} />
    </>
  );
}
