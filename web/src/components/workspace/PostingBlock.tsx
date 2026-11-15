import { SectionChapter } from "../paper/SectionChapter";
import { DocumentBlock } from "../paper/DocumentLayout";
import "../../styles/workspace.css";

// The job description as the application has it: the text tailoring and the ATS score read. It is
// shown whole in a well that scrolls inside itself, with the length it has, so a person can see what
// the model was given. A short text is said to be short rather than made to look complete.

// The sentence the API stores as a posting's description when a tracked Discover result or registry
// posting arrived with no text at all, so that the row is never empty. It is a stand-in for "nothing", not
// text a person or a model should read, so the page treats it as no text: with or without its parentheses
// and a closing full stop, in any case, once trimmed.
const NO_DESCRIPTION_PLACEHOLDER = /^(?:\(no\s+description\s+available\.?\)|no\s+description\s+available)\.?$/i;

// The posting's text as the page reads it: trimmed, and "" when it has none (nothing, only blanks, or the
// API's placeholder above). This block and the resume part both ask whether there is text, so the page's
// rule lives here once. It is the page's own trim, not the built-in engine's check, which also drops
// invisible characters (zero-width spaces, soft hyphens) before it decides a text is empty: text made only
// of those passes here and is refused there.
export function postingBody(text: string | undefined | null): string {
  const body = typeof text === "string" ? text.trim() : "";
  return NO_DESCRIPTION_PLACEHOLDER.test(body) ? "" : body;
}

export function PostingBlock({ text }: { text: string | undefined | null }) {
  const body = postingBody(text);
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
