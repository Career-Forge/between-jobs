import { Link } from "react-router-dom";
import { PRIVACY_PATH, TERMS_PATH } from "../lib/publicRoutes";

// The two links every place that asks for trust carries: the signed-in shell's nav footer, the
// sign-in page, the landing page's footer. `newTab` is for a page with a form in it (sign-in):
// opening the policy in the same tab would throw away what the person has typed. The accessible
// name then says so; it still begins with the visible text.

const LINKS = [
  { to: PRIVACY_PATH, text: "Privacy Policy" },
  { to: TERMS_PATH, text: "Terms of Service" },
] as const;

export function LegalLinks({ newTab = false }: { newTab?: boolean }) {
  return (
    <nav className="bj-legal-links" aria-label="Legal">
      {LINKS.map((link) => (
        <Link
          key={link.to}
          to={link.to}
          {...(newTab
            ? {
                target: "_blank",
                rel: "noopener noreferrer",
                "aria-label": `${link.text} (opens in a new tab)`,
              }
            : {})}
        >
          {link.text}
        </Link>
      ))}
    </nav>
  );
}
