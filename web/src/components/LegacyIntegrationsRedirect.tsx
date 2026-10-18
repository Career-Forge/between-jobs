import { Navigate, useLocation } from "react-router-dom";
import { SETTINGS_PATH } from "../lib/settingsPaths";

// The old address of the Settings page. The API still names it in setup errors, and a bookmark or a
// link in an old message may too. The query (?capability=...) and the fragment travel with it, so a
// link that said where on the page to look still does.
export function LegacyIntegrationsRedirect() {
  const { search, hash } = useLocation();
  return <Navigate to={{ pathname: SETTINGS_PATH, search, hash }} replace />;
}
