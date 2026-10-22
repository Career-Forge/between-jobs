import { GlyphMark } from "../paper/TrailLine";
import "../../styles/settings.css";

// A side-note about a provider (lib/providerNotes.ts decides whether it has one and what it says).
// Plain muted text with a small mono "Note" label and the gold diamond the trail line uses for
// something to know. It is not a warning: no red, no alert role, nothing to accept, and it is ordinary
// text, so a field can name it in aria-describedby and a screen reader reads it as such. The diamond is
// decoration (the words carry the meaning); the label is drawn in capitals by the stylesheet.

export function ProviderNote({ id, text }: { id?: string; text: string }) {
  return (
    <p id={id} className="bj-set-sidenote">
      <GlyphMark name="evidence" /> <span className="bj-set-sidenote-label">Note</span> {text}
    </p>
  );
}
