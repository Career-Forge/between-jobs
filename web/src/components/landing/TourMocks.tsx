import type { ReactElement } from "react";
import { FIT_LEVELS } from "../../lib/discoverRow";
import { StaticTrailLine, GlyphMark } from "../paper/TrailLine";
import { ChecklistGroup } from "../paper/ChecklistRow";
import { ClaimList, ClaimRow } from "../paper/ClaimRow";
import { FitLadder } from "../paper/ScoreBlock";
import { TOUR, TOUR_TABS, type TourTabId } from "../../content/tour";
import "../../styles/paper-doc.css";
import "../../styles/tour.css";

// The five drawings of the product tour, one per tab (ProductTour.tsx). Each is a picture of a screen
// built from the one fictional sample role, so nothing here takes focus and nothing fetches or stores:
// a depicted button is an outlined span that is hidden from assistive technology and said in words
// beside it, and a drawn bar is decoration next to the word that carries its meaning. Every panel ends
// in the trail line for what it shows (who made it, what it is based on), drawn with the same primitive the
// real screens use, and a plain note of what nobody checked.

function Chip({ children }: { children: string }) {
  return <span className="bj-tour-chip">{children}</span>;
}

// A button drawn as a picture: outlined, never filled, never focusable. Its words are read out once,
// by the sentence beside it, so the drawing itself is hidden.
function MockButton({ children }: { children: string }) {
  return (
    <span className="bj-tour-mock-button" aria-hidden="true">
      {children}
    </span>
  );
}

function Cells({ filled, total }: { filled: number; total: number }) {
  return (
    <span className="bj-segbar-cells" aria-hidden="true">
      {Array.from({ length: total }, (_, index) => (
        <span key={index} className={index < filled ? "bj-segbar-cell bj-segbar-cell--filled" : "bj-segbar-cell"} />
      ))}
    </span>
  );
}

// The panel's foot: the trail line (who made it, what it is based on) and, under it in plain words, what
// nobody checked. The second is an ordinary note, as on the real screens, not a slot of the trail line.
function PanelTrail({ id }: { id: TourTabId }) {
  const tab = TOUR_TABS.find((candidate) => candidate.id === id);
  if (tab === undefined) return null;
  return (
    <div className="bj-tour-foot">
      <StaticTrailLine trail={tab.trail} className="bj-tour-trail" />
      <p className="bj-tour-note bj-tour-not-checked">
        {TOUR.notCheckedLabel}: {tab.notChecked}
      </p>
    </div>
  );
}

export function DiscoverMock() {
  const copy = TOUR.discover;
  return (
    <div className="bj-tour-grid">
      <article className="bj-tour-card" aria-label="A sample Discover result">
        <p className="bj-tour-kicker">
          {copy.kicker} <Chip>{TOUR.sampleTag}</Chip>
        </p>
        <p className="bj-tour-role">{TOUR.role}</p>
        <p className="bj-tour-meta">
          {TOUR.company} {"·"} {TOUR.place}
        </p>
        <p className="bj-tour-quote">{copy.posting}</p>
        <p className="bj-tour-note">{copy.postingNote}</p>
      </article>

      <div className="bj-tour-column">
        <p className="bj-tour-kicker">{copy.fitKicker}</p>
        <FitLadder levels={FIT_LEVELS} level={copy.fitLevel} />
        <p className="bj-tour-sentence">{copy.fitOneLiner}</p>
        <ul className="bj-tour-parts" aria-label={copy.partsLabel}>
          {copy.parts.map((part) => (
            <li key={part.label}>
              <span className="bj-tour-part-label">{part.label}</span>
              <Cells filled={part.filled} total={8} />
            </li>
          ))}
        </ul>
        <p className="bj-tour-note">{copy.lowest}</p>
      </div>
      <PanelTrail id="discover" />
    </div>
  );
}

export function PrepareMock() {
  const copy = TOUR.prepare;
  return (
    <div className="bj-tour-grid">
      <div className="bj-tour-sheet" role="group" aria-label={copy.pageLabel}>
        <p className="bj-tour-sheet-name">{copy.name}</p>
        <p className="bj-tour-sheet-contact">{copy.contact}</p>
        <p className="bj-tour-sheet-heading">{copy.sectionExperience}</p>
        <p className="bj-tour-sheet-job">{copy.job}</p>
        <p className="bj-tour-sheet-text">
          <mark className="bj-tour-flag">{copy.flagged}</mark>
          <span className="bj-visually-hidden"> ({copy.flaggedSrNote})</span>
          {copy.after}
        </p>
        <span className="bj-tour-sheet-bars" aria-hidden="true">
          <span className="bj-tour-sheet-bar bj-tour-sheet-bar--full" />
          <span className="bj-tour-sheet-bar bj-tour-sheet-bar--wide" />
          <span className="bj-tour-sheet-bar bj-tour-sheet-bar--mid" />
        </span>
        <p className="bj-tour-sheet-heading">{copy.sectionSkills}</p>
        <p className="bj-tour-sheet-text">{copy.skills}</p>
      </div>

      <div className="bj-tour-column">
        <ClaimList label={copy.claimsLabel}>
          <ClaimRow status="unverifiable" claim={copy.claim} reason={copy.claimReason} />
        </ClaimList>
        <ChecklistGroup
          title={copy.checksTitle}
          level={3}
          rows={copy.checks.map((check) => ({ id: check.id, state: "not_checked" as const, label: check.label, hint: check.hint }))}
        />
      </div>
      <PanelTrail id="prepare" />
    </div>
  );
}

export function ReachMock() {
  const copy = TOUR.reach;
  return (
    <div className="bj-tour-grid">
      <article className="bj-tour-card bj-tour-draft" aria-label="A sample Gmail draft">
        <p className="bj-tour-draft-top">
          <span className="bj-tour-kicker">{copy.kicker}</span>
          <Chip>{copy.badge}</Chip>
        </p>
        <dl className="bj-tour-draft-head">
          <div>
            <dt>To</dt>
            <dd>{copy.to}</dd>
          </div>
          <div>
            <dt>Subject</dt>
            <dd>{copy.subject}</dd>
          </div>
        </dl>
        <div className="bj-tour-draft-body">
          <p>{copy.greeting}</p>
          <p>{copy.body}</p>
        </div>
      </article>
      <div className="bj-tour-column">
        <p className="bj-tour-statement">{copy.waiting}</p>
        <p className="bj-tour-note">{copy.availability}</p>
      </div>
      <PanelTrail id="reach" />
    </div>
  );
}

export function PracticeMock() {
  const copy = TOUR.practice;
  return (
    <div className="bj-tour-grid">
      <div className="bj-tour-column">
        <p className="bj-tour-kicker">{copy.questionKicker}</p>
        <p className="bj-tour-question">{copy.question}</p>
        <p className="bj-tour-kicker">{copy.answerLabel}</p>
        <p className="bj-tour-answer">{copy.answer}</p>
      </div>
      <div className="bj-tour-column bj-tour-column--ruled">
        <p className="bj-tour-kicker">{copy.feedbackKicker}</p>
        <ul className="bj-tour-star" aria-label={copy.coverageLabel}>
          {copy.coverage.map((item) => (
            <li key={item.part} data-present={item.present}>
              <span className="bj-tour-star-cell" aria-hidden="true" />
              <span>
                {item.part}
                <span className="bj-visually-hidden">: {item.present ? copy.present : copy.absent}</span>
              </span>
            </li>
          ))}
        </ul>
        <p className="bj-tour-note">
          <GlyphMark name="model" /> {copy.coverageLabel}
        </p>
        <ul className="bj-tour-points">
          {copy.points.map((point) => (
            <li key={point}>{point}</li>
          ))}
        </ul>
        <p className="bj-tour-note">{copy.note}</p>
      </div>
      <PanelTrail id="practice" />
    </div>
  );
}

export function ApplyMock() {
  const copy = TOUR.apply;
  return (
    <div className="bj-tour-grid bj-tour-grid--apply">
      <div className="bj-tour-card" role="group" aria-label={copy.formLabel}>
        <p className="bj-tour-kicker">{copy.pageKicker}</p>
        <p className="bj-tour-address">{copy.pageAddress}</p>
        <ul className="bj-tour-fields">
          {copy.fields.map((field) => (
            <li key={field.label} data-state={field.state}>
              <span className="bj-tour-field-label">{field.label}</span>
              <span className="bj-tour-field-state">{copy[field.state]}</span>
              <span className="bj-tour-field-box" aria-hidden="true" />
            </li>
          ))}
        </ul>
        <p className="bj-tour-note">{copy.untouchedNote}</p>
        <p className="bj-tour-submit">
          <MockButton>{copy.submit}</MockButton>
          <span className="bj-tour-note">{copy.submitNote}</span>
        </p>
      </div>

      <div className="bj-tour-column">
        <div className="bj-tour-card bj-tour-sidepanel" role="group" aria-label={copy.panelLabel}>
          <p className="bj-tour-kicker">{copy.panelKicker}</p>
          <p className="bj-tour-sentence">{copy.panelFound}</p>
          <MockButton>{copy.panelButton}</MockButton>
          <span className="bj-visually-hidden">The side panel shows a {copy.panelButton} button.</span>
          <p className="bj-tour-note">{copy.panelAfter}</p>
          <p className="bj-tour-kicker">{copy.panelLeftLabel}</p>
          <p className="bj-tour-meta">{copy.panelLeft}</p>
          <p className="bj-tour-note">{copy.panelStops}</p>
        </div>
        <p className="bj-tour-note">{copy.availability}</p>
      </div>
      <PanelTrail id="apply" />
    </div>
  );
}

export const TOUR_MOCKS: Readonly<Record<TourTabId, () => ReactElement>> = {
  discover: DiscoverMock,
  prepare: PrepareMock,
  reach: ReachMock,
  practice: PracticeMock,
  apply: ApplyMock,
};
