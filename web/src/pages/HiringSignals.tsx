import { HiringSignalsTab } from "../components/HiringSignalsTab";
import { HiringSignalsPageShell } from "../components/HiringSignalsTabView";
import { refreshHiringSignalsStatus, useHiringSignalsStatus } from "../lib/useHiringSignalsStatus";

// Hiring signals (Hiring Signals P4) -- the standalone tab: recent public hiring
// posts for a role, with no company, for the hidden market (a founder or hiring
// manager posting "we're hiring" with no formal requisition anywhere).
//
// The page is only the gate. The server may have the feature switched off, and
// the shared status (GET /hiring-signals/status, read once for the whole app) says
// so before anything else is asked: enabled mounts the tab, disabled shows a calm
// "switched off" note, and a status request that itself failed offers a retry.
// A person who reaches /hiring-signals directly on a server with the feature off
// gets an explanation, not a form that cannot work.

export default function HiringSignals() {
  const status = useHiringSignalsStatus();
  return (
    <HiringSignalsPageShell status={status} onRetryStatus={refreshHiringSignalsStatus}>
      <HiringSignalsTab />
    </HiringSignalsPageShell>
  );
}
