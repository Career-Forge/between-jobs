import { Fragment } from "react";
import { Link } from "react-router-dom";
import {
  LEGAL_EFFECTIVE_DATE,
  LEGAL_VERSION,
  LEGAL_WHAT_CHANGED,
  type Block,
  type Inline,
  type LegalDocument,
} from "../content/legal";

// Draws one legal document (content/legal.ts): a title, which version it is and when it took
// effect, a short table of contents, and the sections. A pure function of its props, with no
// hooks, so a test can render it directly (the page components, pages/Privacy.tsx and Terms.tsx,
// add the scroll to a fragment in the address). The caller supplies the page's <main> (the
// signed-out layout, or the signed-in shell), so this renders an <article> and exactly one <h1>.

function sectionId(id: string): string {
  return `bj-legal-${id}`;
}

// `newTabLinks` makes a link to a page of the app open in a new tab instead of leaving this one.
// The Tester Agreement is read in the middle of a form (components/EnrollmentView.tsx): a link that
// replaced the page would throw away what the person had already chosen and ticked, and the form
// lives only in that page's state. The legal pages themselves keep the in-app link.
function InlineView({ part, newTabLinks }: { part: Inline; newTabLinks: boolean }) {
  if (typeof part === "string") return <>{part}</>;
  if ("to" in part) {
    if (!newTabLinks) return <Link to={part.to}>{part.text}</Link>;
    return (
      <a href={part.to} target="_blank" rel="noopener noreferrer">
        {part.text}
        <span className="bj-visually-hidden"> (opens in a new tab)</span>
      </a>
    );
  }
  if ("href" in part) {
    return (
      <a href={part.href} target="_blank" rel="noopener noreferrer">
        {part.text}
        <span className="bj-visually-hidden"> (opens in a new tab)</span>
      </a>
    );
  }
  return <a href={`mailto:${part.email}`}>{part.email}</a>;
}

function InlineList({ parts, newTabLinks }: { parts: readonly Inline[]; newTabLinks: boolean }) {
  return (
    <>
      {parts.map((part, index) => (
        <InlineView key={index} part={part} newTabLinks={newTabLinks} />
      ))}
    </>
  );
}

// Exported for the Tester Agreement, which is drawn from the same kind of typed blocks
// (components/EnrollmentView.tsx).
export function BlockView({ block, newTabLinks = false }: { block: Block; newTabLinks?: boolean }) {
  switch (block.kind) {
    case "p":
      return (
        <p>
          <InlineList parts={block.inline} newTabLinks={newTabLinks} />
        </p>
      );
    case "h3":
      return <h3>{block.text}</h3>;
    case "ul":
      return (
        <ul>
          {block.items.map((item, index) => (
            <li key={index}>
              <InlineList parts={item} newTabLinks={newTabLinks} />
            </li>
          ))}
        </ul>
      );
    case "defs":
      return (
        <dl className="bj-legal-defs">
          {block.items.map((item) => (
            <Fragment key={item.term}>
              <dt>{item.term}</dt>
              <dd>
                <InlineList parts={item.detail} newTabLinks={newTabLinks} />
              </dd>
            </Fragment>
          ))}
        </dl>
      );
  }
}

export function LegalDocumentView({ doc }: { doc: LegalDocument }) {
  return (
    <article className="bj-legal">
      <h1>{doc.title}</h1>
      <p className="bj-muted bj-small">
        Version {LEGAL_VERSION}, effective{" "}
        <time dateTime={LEGAL_EFFECTIVE_DATE.iso}>{LEGAL_EFFECTIVE_DATE.label}</time>.
      </p>
      <p className="bj-muted bj-small">What changed: {LEGAL_WHAT_CHANGED}</p>
      <nav className="bj-legal-toc" aria-label={`${doc.title}, on this page`}>
        <ol>
          {doc.sections.map((section) => (
            <li key={section.id}>
              <a href={`#${sectionId(section.id)}`}>{section.heading}</a>
            </li>
          ))}
        </ol>
      </nav>
      {doc.sections.map((section) => (
        <section
          key={section.id}
          id={sectionId(section.id)}
          aria-labelledby={`${sectionId(section.id)}-heading`}
        >
          <h2 id={`${sectionId(section.id)}-heading`}>{section.heading}</h2>
          {section.blocks.map((block, index) => (
            <BlockView key={index} block={block} />
          ))}
        </section>
      ))}
    </article>
  );
}
