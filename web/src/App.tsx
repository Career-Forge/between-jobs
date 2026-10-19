import { lazy } from "react";
import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import { useAuth } from "./auth";
import { AppShell } from "./components/AppShell";
import { EnrollmentGate } from "./components/EnrollmentGate";
import { LegacyIntegrationsRedirect } from "./components/LegacyIntegrationsRedirect";
import { PublicLayout } from "./components/PublicLayout";
import { RouteBoundary } from "./components/RouteBoundary";
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
import Enroll from "./pages/Enroll";
import Landing from "./pages/Landing";
import Login from "./pages/Login";
import NotFound from "./pages/NotFound";
import Privacy from "./pages/Privacy";
import Terms from "./pages/Terms";
import UpdatePassword from "./pages/UpdatePassword";

// The signed-in pages are fetched the first time they are opened, so a stranger who only reads the landing
// page, signs in or reads the legal pages downloads none of them. The sign-in page, the legal pages, the
// landing page, the password form and the enrollment page (the gate draws it) stay in the first download.
const Today = lazy(() => import("./pages/Today"));
const Discover = lazy(() => import("./pages/Discover"));
const HiringSignals = lazy(() => import("./pages/HiringSignals"));
const Applications = lazy(() => import("./pages/Applications"));
const ApplicationWorkspace = lazy(() => import("./pages/ApplicationWorkspace"));
const Practice = lazy(() => import("./pages/Practice"));
const Profile = lazy(() => import("./pages/Profile"));
const Settings = lazy(() => import("./pages/Settings"));

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
      <PublicLayout variant="landing">
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
    <AppShell email={session?.user.email ?? null} onSignOut={() => void signOut()}>
      {/* When the server requires the tester programme, a person who has not joined sees the
          enrollment page here instead of any page but the exempt ones (lib/enrollment.ts). */}
      <EnrollmentGate>
        <RouteBoundary>
          <Routes>
            <Route path="/" element={<Today />} />
            <Route path="/discover" element={<Discover />} />
            <Route path="/hiring-signals" element={<HiringSignals />} />
            <Route path="/applications" element={<Applications />} />
            <Route path="/applications/:id" element={<ApplicationWorkspace />} />
            <Route path="/practice" element={<Practice />} />
            <Route path="/profile" element={<Profile />} />
            <Route path="/settings" element={<Settings />} />
            <Route path="/profile/integrations" element={<LegacyIntegrationsRedirect />} />
            <Route path={UPDATE_PASSWORD_PATH} element={<UpdatePassword />} />
            <Route path={PRIVACY_PATH} element={<Privacy />} />
            <Route path={TERMS_PATH} element={<Terms />} />
            <Route path={ENROLL_PATH} element={<Enroll />} />
            <Route path="*" element={<NotFound />} />
          </Routes>
        </RouteBoundary>
      </EnrollmentGate>
    </AppShell>
  );
}
