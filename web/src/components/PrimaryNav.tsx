import { NavLink } from "react-router-dom";
import { navItemsFor } from "../lib/nav";
import { useHiringSignalsEnabled } from "../lib/useHiringSignalsStatus";

// The primary navigation is data (lib/nav.ts); which entries show depends on
// whether the server has Hiring signals switched on, which is asked once for the
// whole app (lib/useHiringSignalsStatus.ts). This component only mounts inside the
// signed-in shell, so that ask is never made for a signed-out visitor. It lives in
// its own file so a test can render it and see which links a person really gets
// while the answer is unknown, off, or on.

export function PrimaryNav() {
  const hiringSignalsEnabled = useHiringSignalsEnabled();
  return (
    <nav>
      {navItemsFor(hiringSignalsEnabled).map((item) => (
        <NavLink
          key={item.to}
          to={item.to}
          end={item.end}
          className={({ isActive }) => (isActive ? "bj-nav-item active" : "bj-nav-item")}
        >
          {item.label}
        </NavLink>
      ))}
    </nav>
  );
}
