import { useLocation } from "react-router-dom";
import { LegalDocumentView } from "../components/LegalDocumentView";
import { PRIVACY } from "../content/legal";
import { useScrollToHash } from "../lib/useScrollToHash";

// /privacy -- reachable signed out (inside the public layout) and signed in (inside the shell);
// the text is content/legal.ts.
//
// The document is only drawn once the session has been looked up (App.tsx shows a blank page
// until then), which is after the browser's one attempt to scroll to a fragment. So a shared
// address such as /privacy#bj-legal-gmail, or a reload of it, would show the top of the page
// without this: it scrolls to the section the address names once the section is on screen.
export default function Privacy() {
  const { hash } = useLocation();
  useScrollToHash(true, hash);
  return <LegalDocumentView doc={PRIVACY} />;
}
