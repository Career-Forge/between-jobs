import { useLocation } from "react-router-dom";
import { LegalDocumentView } from "../components/LegalDocumentView";
import { TERMS } from "../content/legal";
import { useScrollToHash } from "../lib/useScrollToHash";

// /terms -- reachable signed out (inside the public layout) and signed in (inside the shell);
// the text is content/legal.ts. It scrolls to the section a fragment in the address names, for
// the reason Privacy.tsx gives.
export default function Terms() {
  const { hash } = useLocation();
  useScrollToHash(true, hash);
  return <LegalDocumentView doc={TERMS} />;
}
