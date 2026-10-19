import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import "../../styles/research.css";

// What a research section says before anything has been run: what will appear here, named with real item
// kinds, and what it needs. The section's heading is the chapter's h2, so this one is an h3. It is
// StateViews' EmptyState at one level down; it never shows an example row, an illustration or a
// celebration. The link is a text link to where the thing it needs is set up, never a second button.
export function EmptyBlock({ heading, children, link }: { heading: string; children: ReactNode; link?: { to: string; label: string } }) {
  return (
    <div className="bj-state bj-state--empty bj-rs-empty">
      <h3 className="bj-state-heading">{heading}</h3>
      <div className="bj-state-body">{children}</div>
      {link !== undefined && (
        <p className="bj-state-link">
          <Link to={link.to}>{link.label}</Link>
        </p>
      )}
    </div>
  );
}
