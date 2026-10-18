import { NavLink } from "react-router-dom";
import { navItemsFor } from "../lib/nav";
import { useHiringSignalsEnabled } from "../lib/useHiringSignalsStatus";
import { Icon } from "./icons";

// The rail's main navigation. The entries are data (lib/nav.ts); which ones show depends on
// whether the server has Hiring signals switched on, which is asked once for the whole app
// (lib/useHiringSignalsStatus.ts). This component only mounts inside the signed-in shell, so that
// ask is never made for a signed-out visitor. It lives in its own file so a test can render it and
// see which links a person really gets while the answer is unknown, off, or on.
//
// The page you are on is the selection primitive: NavLink sets aria-current="page" on its link and
// tokens.css turns that into the maroon fill. Nothing here styles the active state itself.

export function PrimaryNav() {
  const hiringSignalsEnabled = useHiringSignalsEnabled();
  return (
    <nav aria-label="Main" className="bj-rail-nav">
      {navItemsFor(hiringSignalsEnabled).map((item) => (
        <NavLink key={item.to} to={item.to} end={item.end} className="bj-nav-item bj-selectable">
          <Icon name={item.icon} />
          {item.label}
        </NavLink>
      ))}
    </nav>
  );
}
