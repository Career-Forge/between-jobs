import type { ReactNode } from "react";
import "../../styles/paper-doc.css";

// The heading block of a section of a document page: an optional eyebrow, the serif heading, an
// optional slot under it (a trail line, a count) and, if the section has one, a short intro.
//
//   <SectionChapter id="ch-draft" eyebrow="Resume" title="Draft" trail={<StaticTrailLine .../>}>
//     <p>Header, structure and settings for this draft.</p>
//   </SectionChapter>
//
// `id` is the anchor a link (the jump bar, the band, a checklist row) points at. The heading is
// focusable with tabIndex -1 and is the first heading inside the anchored element, which is what
// useScrollToHash looks for: arriving by a link leaves keyboard focus on the heading, not behind it.
// The level is the page's to choose -- the page's own <h1> is the PageHeader, so chapters are h2 and
// a block's title inside one is h3 -- and there is no numeral: nothing on the page counts chapters.
// The eyebrow is not part of the heading, so the heading reads as the title alone.

export type ChapterLevel = 2 | 3 | 4 | 5 | 6;

export interface SectionChapterProps {
  id?: string;
  title: ReactNode;
  /** h2 by default. */
  level?: ChapterLevel;
  /** A small uppercase mono line above the heading ("Resume"): the group a section belongs to. */
  eyebrow?: string;
  /** Under the heading, before the intro: a trail line, a count. */
  trail?: ReactNode;
  /** An intro under the heading. */
  children?: ReactNode;
  className?: string;
}

export function SectionChapter({ id, title, level = 2, eyebrow, trail, children, className }: SectionChapterProps) {
  const Heading = `h${level}` as const;
  return (
    <div id={id} className={className === undefined ? "bj-chapter" : `bj-chapter ${className}`}>
      {eyebrow !== undefined && eyebrow !== "" && <p className="bj-chapter-eyebrow">{eyebrow}</p>}
      <Heading tabIndex={-1} className={`bj-chapter-title bj-chapter-title--h${level}`}>
        {title}
      </Heading>
      {trail !== undefined && trail !== null && <div className="bj-chapter-trail">{trail}</div>}
      {children !== undefined && children !== null && <div className="bj-chapter-intro">{children}</div>}
    </div>
  );
}
