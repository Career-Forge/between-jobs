import { NavLink, Route, Routes } from "react-router-dom";
import { useAuth } from "./auth";
import Applications from "./pages/Applications";
import ApplicationWorkspace from "./pages/ApplicationWorkspace";
import Discover from "./pages/Discover";
import Integrations from "./pages/Integrations";
import Login from "./pages/Login";
import Practice from "./pages/Practice";
import Profile from "./pages/Profile";
import Today from "./pages/Today";

// Primary navigation per the design book's information architecture:
// Today / Discover / Applications / Practice / Profile. Every section
// exists from day one with an HONEST empty state (what it will be, which
// stage delivers it) -- no fake data, no placeholder screenshots.

const NAV = [
  { to: "/", label: "Today", end: true },
  { to: "/discover", label: "Discover", end: false },
  { to: "/applications", label: "Applications", end: false },
  { to: "/practice", label: "Practice", end: false },
  { to: "/profile", label: "Profile", end: false },
];

export default function App() {
  const { session, loading, signOut } = useAuth();

  if (loading) {
    return <div className="bj-boot" />;
  }

  if (!session) {
    return <Login />;
  }

  return (
    <div className="bj-shell">
      <aside className="bj-nav">
        <div className="bj-wordmark">Between Jobs</div>
        <nav>
          {NAV.map((item) => (
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
        <div className="bj-nav-footer">
          <button onClick={() => void signOut()}>Sign out</button>
        </div>
      </aside>
      <main className="bj-main">
        <Routes>
          <Route path="/" element={<Today />} />
          <Route path="/discover" element={<Discover />} />
          <Route path="/applications" element={<Applications />} />
          <Route path="/applications/:id" element={<ApplicationWorkspace />} />
          <Route path="/practice" element={<Practice />} />
          <Route path="/profile" element={<Profile />} />
          <Route path="/profile/integrations" element={<Integrations />} />
        </Routes>
      </main>
    </div>
  );
}
