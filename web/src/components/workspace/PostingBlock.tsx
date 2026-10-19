import { SectionChapter } from "../paper/SectionChapter";
import { DocumentBlock } from "../paper/DocumentLayout";
import "../../styles/workspace.css";

// The job description as the application has it: the text tailoring and the ATS score read. It is
// shown whole in a well that scrolls inside itself, with the length it has, so a person can see what
// the model was given. A short text is said to be short rather than made to look complete.

export function PostingBlock({ text }: { text: string | undefined | null }) {
  const body = typeof text === "string" ? text.trim() : "";
  return (
    <DocumentBlock>
      <SectionChapter level={3} title="The posting" />
      {body === "" ? (
        <p className="bj-muted bj-small">No description text was saved for this posting, so tailoring has nothing to read.</p>
      ) : (
        <>
          <div className="bj-ws-well" role="region" aria-label="Job description text" tabIndex={0}>
            {body}
          </div>
          <p className="bj-ws-caption">Tailoring reads only this text ({body.length.toLocaleString("en-US")} characters).</p>
        </>
      )}
    </DocumentBlock>
  );
}
