import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import { useAuth } from "./auth";
import { PrimaryNav } from "./components/PrimaryNav";
import { UPDATE_PASSWORD_PATH, recoveryRedirect } from "./lib/passwordReset";
import Applications from "./pages/Applications";
import ApplicationWorkspace from "./pages/ApplicationWorkspace";
import Discover from "./pages/Discover";
import HiringSignals from "./pages/HiringSignals";
import Integrations from "./pages/Integrations";
import Login from "./pages/Login";
import Practice from "./pages/Practice";
import Profile from "./pages/Profile";
import Today from "./pages/Today";
import UpdatePassword from "./pages/UpdatePassword";

export default function App() {
  const { session, loading, signOut, recovery } = useAuth();
  const location = useLocation();

  if (loading) {
    return <div className="bj-boot" />;
  }

  if (!session) {
    return <Login />;
  }

  // Someone who followed a password-reset link is in a recovery session: whichever page the
  // link landed them on, they set the new password first.
  const recoveryTarget = recoveryRedirect(recovery, location.pathname);
  if (recoveryTarget !== null) {
    return <Navigate to={recoveryTarget} replace />;
  }

  return (
    <div className="bj-shell">
      <aside className="bj-nav">
        <div className="bj-wordmark">Between Jobs</div>
        <PrimaryNav />
        <div className="bj-nav-footer">
          <button onClick={() => void signOut()}>Sign out</button>
        </div>
      </aside>
      <main className="bj-main">
        <Routes>
          <Route path="/" element={<Today />} />
          <Route path="/discover" element={<Discover />} />
          <Route path="/hiring-signals" element={<HiringSignals />} />
          <Route path="/applications" element={<Applications />} />
          <Route path="/applications/:id" element={<ApplicationWorkspace />} />
          <Route path="/practice" element={<Practice />} />
          <Route path="/profile" element={<Profile />} />
          <Route path="/profile/integrations" element={<Integrations />} />
          <Route path={UPDATE_PASSWORD_PATH} element={<UpdatePassword />} />
        </Routes>
      </main>
    </div>
  );
}
