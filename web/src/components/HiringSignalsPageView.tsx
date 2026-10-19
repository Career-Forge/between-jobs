import type { ReactNode } from "react";
import type { HiringStatus } from "../lib/hiringSignalsStatus";
import { TAB_INTRO } from "../lib/hiringSignalsTab";
import type { NextStepOutcome } from "../lib/nextStep";
import { SwitchedOffNote } from "./HiringSignalsTabView";
import { NextStepBand, type NextStepPending } from "./paper/NextStepBand";
import { PageHeader } from "./paper/PageHeader";
import { FailureState } from "./paper/StateViews";
import "../styles/hiring-signals.css";

// The page around the tab: its header, the next-step band, and what to show before the server has said
// the feature is on. `children` is the connected tab, mounted only when the status is "enabled" -- so
// a server with the feature off never gets a saves or searches request from this page, and a person who
// reaches /hiring-signals directly sees a calm explanation instead of a form that cannot work.
//
//   checking     the header and the band's "Loading..." only: no skeleton that later vanishes
//   enabled      the tab
//   disabled     the calm "switched off" note (the band says the same, unlit)
//   unavailable  the status read failed: a callout naming what could not be read, with no Retry of its
//                own -- the band holds "Try again"
//
// A refused "Save this search" (the 25-search cap) shows the server's own words directly under the band.
// Every state's band row is lib/nextStep/pages/hiring-signals.ts; the reading, the model and the
// handlers are pages/HiringSignals.tsx.

export const STATUS_UNAVAILABLE_MESSAGE = "Could not check whether hiring signals are available on this server.";

export interface HiringSignalsPageViewProps {
  status: HiringStatus;
  outcome: NextStepOutcome;
  onBandAction: (intent: string) => void;
  /** A request out for the band's step (Saving...). */
  pending?: NextStepPending | null;
  /** The server's own words for a refused "Save this search". */
  saveSearchError?: string | null;
  /** A form on the page was submitted (the band's Search submits the form from outside it). */
  onFormSubmit?: () => void;
  children: ReactNode;
}

export function HiringSignalsPageView({ status, outcome, onBandAction, pending = null, saveSearchError = null, onFormSubmit, children }: HiringSignalsPageViewProps) {
  return (
    <div className="bj-hs-page" onSubmitCapture={onFormSubmit}>
      <PageHeader title="Hiring signals" subtitle={TAB_INTRO} />
      <NextStepBand outcome={outcome} onAction={onBandAction} pending={pending} />
      {saveSearchError !== null && (
        <div className="bj-hs-save-error">
          <FailureState message={saveSearchError} />
        </div>
      )}
      {status.kind === "enabled" && children}
      {status.kind === "disabled" && <SwitchedOffNote />}
      {status.kind === "unavailable" && <FailureState message={STATUS_UNAVAILABLE_MESSAGE} />}
    </div>
  );
}
