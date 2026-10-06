import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import { useAuth } from "./auth";
import { EnrollmentGate } from "./components/EnrollmentGate";
import { LegalLinks } from "./components/LegalLinks";
import { PrimaryNav } from "./components/PrimaryNav";
import { PublicLayout } from "./components/PublicLayout";
import { UPDATE_PASSWORD_PATH, recoveryRedirect } from "./lib/passwordReset";
import {
  ENROLL_PATH,
  PRIVACY_PATH,
  TERMS_PATH,
  appView,
  documentTitleFor,
} from "./lib/publicRoutes";
import { useDocumentTitle } from "./lib/useDocumentTitle";
import { useScrollToTopOnNavigate } from "./lib/useScrollToTopOnNavigate";
import Applications from "./pages/Applications";
import ApplicationWorkspace from "./pages/ApplicationWorkspace";
import Discover from "./pages/Discover";
import Enroll from "./pages/Enroll";
import HiringSignals from "./pages/HiringSignals";
import Integrations from "./pages/Integrations";
import Landing from "./pages/Landing";
import Login from "./pages/Login";
import Practice from "./pages/Practice";
import Privacy from "./pages/Privacy";
import Profile from "./pages/Profile";
import Terms from "./pages/Terms";
import Today from "./pages/Today";
import UpdatePassword from "./pages/UpdatePassword";

export default function App() {
  const { session, loading, signOut, recovery } = useAuth();
  const location = useLocation();

  // Which screen this visitor gets is decided by one pure function (lib/publicRoutes.ts); this
  // component draws the answer. A signed-out visitor reaches the landing page, the two legal
  // pages and the sign-in page; any other address shows sign-in, so a deep link such as
  // /applications/123 still lands there and, once signed in, opens that same page.
  const view = appView({
    signedIn: session !== null,
    loading,
    pathname: location.pathname,
    search: location.search,
    hash: location.hash,
  });

  // Both hooks sit above every early return, so the hook order is the same on every screen.
  // A change of page starts at the top of it (the app has no scroll restoration of its own), and
  // each public screen has a title of its own; the sign-in page names itself. The signed-in shell
  // does not: its tester-programme gate can draw the enrollment page at ANY address, so the gate
  // alone knows what is on screen and titles it (components/EnrollmentGate.tsx). Were this hook to
  // write the shell's title too, it would overwrite the gate's whenever the address changed.
  useScrollToTopOnNavigate();
  useDocumentTitle(view === "app" ? null : documentTitleFor(view, location.pathname));

  if (view === "boot") {
    return <div className="bj-boot" />;
  }
  if (view === "login") {
    return <Login />;
  }
  if (view === "landing") {
    return (
      <PublicLayout>
        <Landing />
      </PublicLayout>
    );
  }
  if (view === "privacy") {
    return (
      <PublicLayout>
        <Privacy />
      </PublicLayout>
    );
  }
  if (view === "terms") {
    return (
      <PublicLayout>
        <Terms />
      </PublicLayout>
    );
  }

  // From here on the visitor is signed in: the view is "app" or "home_redirect".

  // Someone who followed a password-reset link is in a recovery session: whichever page the
  // link landed them on, they set the new password first.
  const recoveryTarget = recoveryRedirect(recovery, location.pathname);
  if (recoveryTarget !== null) {
    return <Navigate to={recoveryTarget} replace />;
  }
  // A signed-in person has no use for the sign-in form: "/" is Today for them.
  if (view === "home_redirect") {
    return <Navigate to="/" replace />;
  }

  return (
    <div className="bj-shell">
      <aside className="bj-nav">
        <div className="bj-wordmark">Between Jobs</div>
        <PrimaryNav />
        <div className="bj-nav-footer">
          <button onClick={() => void signOut()}>Sign out</button>
          <LegalLinks />
        </div>
      </aside>
      <main className="bj-main">
        {/* When the server requires the tester programme, a person who has not joined sees the
            enrollment page here instead of any page but the exempt ones (lib/enrollment.ts). */}
        <EnrollmentGate>
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
            <Route path={PRIVACY_PATH} element={<Privacy />} />
            <Route path={TERMS_PATH} element={<Terms />} />
            <Route path={ENROLL_PATH} element={<Enroll />} />
          </Routes>
        </EnrollmentGate>
      </main>
    </div>
  );
}
