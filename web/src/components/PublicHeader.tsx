import { useEffect, useId, useRef, useState, type KeyboardEvent } from "react";
import { Link, useLocation } from "react-router-dom";
import { SITE_TABS } from "../content/siteNav";
import { LANDING_PATH, LOGIN_PATH, REGISTER_PATH, normalizePath } from "../lib/publicRoutes";
import { MAIN_CONTENT_ID } from "./SkipLink";
import { Wordmark } from "./Wordmark";
import "../styles/public.css";

// The signed-out site's header: the wordmark, the site's four tabs (Product, How it works, Open source,
// Privacy), and the way in. The tab for the page you are on carries aria-current="page" and the one
// selection style (maroon fill, ivory text, weight 600), never an underline.
//
// THE ONE CYAN BUTTON. Create account is the page's one cyan fill on every public page that has no
// hero of its own. The home page's hero holds the page's one (its next-step band), so there the header's
// is outlined (`filled` is false). Sign in is always a plain text link.
//
// UNDER 960PX the tabs and Sign in fold into a menu: a disclosure button ("Menu") that opens a panel
// right after it. Create account stays in the header's row at every width, so a phone visitor on any
// public page can always start; Sign in is a plain link in the panel there, and beside Create account
// from 960px. Escape closes the panel and puts focus back on the button; choosing a link in it closes
// it and moves focus to the page's <main>, since the page has changed under the person's finger;
// growing the window to the wide layout closes it, because the wide layout draws the same links itself.
// Any other way the location changes closes it too (the wordmark, a link in the page or the footer, the
// browser's Back and Forward), so a visitor never lands on a new page under an open menu. The panel is
// drawn only while it is open, so there is never a second copy of the links for a reader to meet. The
// button and the panel's links are 44px tall or more.

const WIDE_QUERY = "(min-width: 960px)";

export function PublicHeader({ filled }: { filled: boolean }) {
  const { pathname, key: locationKey } = useLocation();
  const here = normalizePath(pathname);
  const [open, setOpen] = useState(false);
  const button = useRef<HTMLButtonElement>(null);
  const panelId = useId();

  // Every navigation gets a new location key (a link to the page you are already on, Back and Forward
  // included), so the panel closes whichever link or button moved the person. Focus is left where the
  // navigation put it: only a choice made inside the panel moves it (onChoose).
  useEffect(() => {
    setOpen(false);
  }, [locationKey]);

  // The wide layout shows the tabs itself, so an open panel has nothing left to do.
  useEffect(() => {
    if (!open || typeof window.matchMedia !== "function") return;
    const query = window.matchMedia(WIDE_QUERY);
    const onChange = () => {
      if (query.matches) setOpen(false);
    };
    query.addEventListener("change", onChange);
    return () => query.removeEventListener("change", onChange);
  }, [open]);

  function onKeyDown(event: KeyboardEvent<HTMLElement>) {
    if (event.key !== "Escape" || !open) return;
    event.preventDefault();
    setOpen(false);
    button.current?.focus();
  }

  // A link in the panel was chosen: the panel goes, and focus lands on the page it opened.
  function onChoose() {
    setOpen(false);
    document.getElementById(MAIN_CONTENT_ID)?.focus({ preventScroll: true });
  }

  const createClass = filled ? "bj-button-link bj-button-link-primary" : "bj-button-link";

  return (
    <header className="bj-public-header" onKeyDown={onKeyDown}>
      <Link to={LANDING_PATH} className="bj-public-wordmark">
        <Wordmark />
      </Link>

      <nav className="bj-public-tabs" aria-label="Main">
        {SITE_TABS.map((tab) => (
          <Link key={tab.path} to={tab.path} className="bj-selectable" aria-current={here === tab.path ? "page" : undefined}>
            {tab.label}
          </Link>
        ))}
      </nav>

      <nav className="bj-public-actions" aria-label="Account">
        <Link to={LOGIN_PATH} className="bj-public-signin">
          Sign in
        </Link>
        <Link to={REGISTER_PATH} className={createClass}>
          Create account
        </Link>
      </nav>

      <button
        ref={button}
        type="button"
        className="bj-public-menu-button"
        aria-expanded={open}
        aria-controls={open ? panelId : undefined}
        onClick={() => setOpen((value) => !value)}
      >
        Menu
      </button>

      {open && (
        <nav id={panelId} className="bj-public-menu" aria-label="Menu">
          {SITE_TABS.map((tab) => (
            <Link key={tab.path} to={tab.path} className="bj-selectable" aria-current={here === tab.path ? "page" : undefined} onClick={onChoose}>
              {tab.label}
            </Link>
          ))}
          <Link to={LOGIN_PATH} className="bj-public-menu-signin" onClick={onChoose}>
            Sign in
          </Link>
        </nav>
      )}
    </header>
  );
}
