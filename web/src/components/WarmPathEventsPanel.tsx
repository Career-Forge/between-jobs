import { countWords, runDate } from "../lib/research";
import { eventsTrail, failedTrail } from "../lib/researchTrails";
import { SETTINGS_PATH } from "../lib/settingsPaths";
import { useNow } from "../lib/useNow";
import { useResearchRun } from "../lib/useResearchRun";
import { BlockTrail } from "./paper/DocumentLayout";
import { FailureState, LoadingState } from "./paper/StateViews";
import { EmptyBlock } from "./research/EmptyBlock";
import { Chip, EvidenceItem, EvidenceList, type ChipTone } from "./research/Evidence";
import { RunButton, RunProblem, RunWarnings } from "./research/RunParts";
import type { TrailGlyphName } from "../lib/trail";
import "../styles/research.css";

// Events: public conference, meetup and sponsor listings near the person that name this company, each
// labeled by how certain the tie is. Three-state honesty is the point, and the three are never merged
// into one list of equals:
//
//   Confirmed speaker       the source's own text names a speaker from the company
//   Likely-staffed sponsor  the company sponsors, so a booth is probably staffed; no one is named
//   Company-adjacent        the event is connected to the company in some other way
//
// A speaker is named only when the source's own text names them (the server drops the name from any row
// that fails that check and lists the event as company-adjacent), and no one's attendance is ever
// guessed. The certainty word is a model's reading, so it is a gold outline with a mark and the word,
// never green. Events are listed firmest first; within a certainty, in the order the server sent them.
//
// A run searches with the person's own You.com or Firecrawl key and makes one call to their model. States
// are the research runs' (see CompanyIntelPanel).

type Certainty = "confirmed_speaker" | "likely_staffed_sponsor" | "company_adjacent";

interface WarmPathEvent {
  id: string;
  event_name: string;
  event_url: string;
  event_date: string | null;
  location: string | null;
  certainty: Certainty;
  speaker_name: string | null;
  speaker_title: string | null;
  talk_topic: string | null;
  source_title: string;
  source_snippet: string;
}

interface Run {
  id: string;
  company_name: string;
  providers_used: string[];
  warnings: string[];
  created_at: string;
}

interface WarmPathEventsResponse {
  run: Run | null;
  events: WarmPathEvent[];
}

const CERTAINTY: Record<Certainty, { label: string; tone: ChipTone; glyph: TrailGlyphName; rank: number }> = {
  confirmed_speaker: { label: "Confirmed speaker", tone: "solid", glyph: "evidence", rank: 0 },
  likely_staffed_sponsor: { label: "Likely-staffed sponsor", tone: "dashed", glyph: "evidence", rank: 1 },
  company_adjacent: { label: "Company-adjacent", tone: "muted", glyph: "code", rank: 2 },
};

function readingOf(certainty: string): { label: string; tone: ChipTone; glyph: TrailGlyphName; rank: number } {
  return Object.prototype.hasOwnProperty.call(CERTAINTY, certainty) ? CERTAINTY[certainty as Certainty] : { label: certainty, tone: "muted", glyph: "code", rank: 3 };
}

export function WarmPathEventsPanel({ applicationId, now }: { applicationId: string; now?: Date }) {
  const clock = useNow(now);
  const { read, running, problem, load, run, runButton, focusRunButton } = useResearchRun<WarmPathEventsResponse>(`/applications/${applicationId}/warm-path-events`, {
    read: "Failed to load",
    run: "Failed to generate",
  });

  const ready = read.kind === "ready" ? read.data : null;
  const data = ready !== null && ready.run !== null ? { run: ready.run, events: ready.events } : null;
  const ordered = data === null ? [] : data.events.map((event, index) => ({ event, index })).sort((a, b) => readingOf(a.event.certainty).rank - readingOf(b.event.certainty).rank || a.index - b.index).map((entry) => entry.event);
  const researched = data === null ? null : runDate(data.run, clock);
  const meta = data === null ? [] : [researched === null ? null : `Researched ${researched}`, countWords(data.events.length, "event", "events")].filter((part): part is string => part !== null);

  const trail =
    data !== null
      ? eventsTrail(data.run, data.events.length, clock)
      : read.kind === "failed"
        ? failedTrail("Events couldn't be read")
        : problem?.kind === "error"
          ? failedTrail("That event search didn't finish")
          : null;

  return (
    <div className="bj-rs">
      <p className="bj-ws-intro">
        Public conference, meetup and sponsor listings near you that name this company, each labeled by how certain the tie is. A speaker is named only when the source's own text names them, and no one's
        attendance is guessed. A run searches with your You.com or Firecrawl key and makes one call to your model, so it uses your own keys each time.
      </p>
      <RunButton
        label={data !== null ? "Refresh" : "Find events"}
        busyLabel="Searching..."
        busy={running}
        held={read.kind === "loading"}
        buttonRef={runButton}
        onRun={() => {
          if (read.kind !== "loading") void run();
        }}
      />

      <div role="status" className="bj-rs-live">
        {meta.length > 0 && <p className="bj-rs-meta">{meta.join(" · ")}</p>}
      </div>

      {read.kind === "loading" && <LoadingState rows={3} rowHeight={88} label="Loading events..." />}
      {read.kind === "failed" && <FailureState message={read.message} onRetry={() => void load()} restoreFocusTo={focusRunButton} />}
      <RunProblem problem={problem} onRetry={() => void run()} restoreFocusTo={focusRunButton} />

      {ready !== null && data === null && (
        <EmptyBlock heading="No events found yet" link={{ to: SETTINGS_PATH, label: "Keys are added in Settings" }}>
          <p>
            A run looks for events near you that involve this company: a talk by one of its people (a confirmed speaker), a sponsor booth that is likely staffed, or an event only connected to the company.
          </p>
          <p>It needs a model key and a You.com or Firecrawl key.</p>
        </EmptyBlock>
      )}

      {data !== null && (
        <>
          <RunWarnings run={data.run} />
          {data.events.length === 0 ? (
            <p className="bj-muted bj-small">No nearby events turned up in that search pass. Refresh to try again.</p>
          ) : (
            <ul className="bj-rs-list" aria-label="Events">
              {ordered.map((event) => {
                const reading = readingOf(event.certainty);
                const where = [event.event_date, event.location].filter((part): part is string => typeof part === "string" && part.trim() !== "");
                return (
                  <li key={event.id} className="bj-rs-item">
                    <h3 className="bj-rs-title">{event.event_name}</h3>
                    {where.length > 0 && <p className="bj-rs-sub">{where.join(" · ")}</p>}
                    <p className="bj-rs-chips">
                      <Chip tone={reading.tone} glyph={reading.glyph}>
                        {reading.label}
                      </Chip>
                    </p>
                    {event.speaker_name !== null && event.speaker_name !== "" && (
                      <p className="bj-rs-text">
                        {event.speaker_name}
                        {event.speaker_title ? ` -- ${event.speaker_title}` : ""}
                        {event.talk_topic ? `: "${event.talk_topic}"` : ""}
                      </p>
                    )}
                    <EvidenceList label="Source">
                      <EvidenceItem url={event.event_url} title={event.source_title} />
                    </EvidenceList>
                  </li>
                );
              })}
            </ul>
          )}
        </>
      )}

      {trail !== null && <BlockTrail trail={trail.trail} receipt={trail.receipt} receiptTitle={trail.receiptTitle} />}
    </div>
  );
}
