import type { RefObject } from "react";
import { NavLink } from "react-router-dom";
import { SETTINGS_ITEM, type NavItem } from "../lib/nav";
import { Icon } from "./icons";
import { LegalLinks } from "./LegalLinks";

// The phone's "More" sheet: what the five tabs have no room for -- Hiring signals (while the
// server has it on), Practice, Settings -- plus the signed-in address and Sign out. A native modal
// <dialog>, so the browser traps focus inside it, closes it on Esc and hands focus back to the
// button that opened it. A press on the dimmed area outside the panel closes it too.

export interface MoreSheetProps {
  dialogRef: RefObject<HTMLDialogElement | null>;
  items: readonly NavItem[];
  email: string | null;
  onSignOut: () => void;
}

export function MoreSheet({ dialogRef, items, email, onSignOut }: MoreSheetProps) {
  const close = () => dialogRef.current?.close();
  return (
    <dialog
      ref={dialogRef}
      className="bj-more-sheet"
      aria-labelledby="bj-more-title"
      onClick={(event) => {
        if (event.target === event.currentTarget) close();
      }}
    >
      <div className="bj-more-body">
        <div className="bj-more-head">
          <h2 id="bj-more-title">More</h2>
          <button type="button" className="bj-quiet bj-more-close" aria-label="Close" onClick={close}>
            <Icon name="close" />
          </button>
        </div>
        {email !== null && <p className="bj-rail-email">{email}</p>}
        <nav aria-label="More">
          {[...items, SETTINGS_ITEM].map((item) => (
            <NavLink key={item.to} to={item.to} end={item.end} className="bj-more-row bj-selectable" onClick={close}>
              <Icon name={item.icon} />
              {item.label}
            </NavLink>
          ))}
        </nav>
        <button
          type="button"
          className="bj-more-row bj-more-signout"
          onClick={() => {
            close();
            onSignOut();
          }}
        >
          Sign out
        </button>
        <LegalLinks />
      </div>
    </dialog>
  );
}
