import { useEffect, useMemo, useRef } from "react";
import { NEXT_STEP_FOCUS_ID, NextStepBand } from "../components/paper/NextStepBand";
import { PageHeader } from "../components/paper/PageHeader";
import { EmptyState } from "../components/paper/StateViews";
import { nextStep, type NextStepInput } from "../lib/nextStep";
import { setupChecks } from "../lib/setupReads";
import { useSetupReads } from "../lib/useSetupReads";
import "../styles/applications.css";

// The Practice address. Interview practice is grounded in one specific job and the resume evidence
// for it, so there is no company-blind practice here: the sessions live in each application's
// workspace, and this page says so and points there.
//
// It has no data of its own to load, fail or be missing, so none of the page's states can occur (what
// a practice session goes through is drawn in the workspace's Prepare block). The next-step band is
// the exception: its global rows need to know whether a profile and a model key exist, so the page
// asks for those two (lib/useSetupReads.ts) and the band's row R1 follows them
// (lib/nextStep/pages/practice.ts).

export default function Practice() {
  const { reads, at, retry } = useSetupReads();
  const outcome = useMemo(() => {
    const checks = setupChecks(reads, at, { modelKey: true });
    const input: NextStepInput = { page: "practice", signedIn: true, profile: checks.profile, credentials: checks.credentials };
    return nextStep(input);
  }, [reads, at]);

  // "Try again" goes with the failure it answered; once the reads have a different answer, focus
  // goes to the band's step rather than being lost to the page.
  const pressedOn = useRef<typeof reads | null>(null);
  useEffect(() => {
    const before = pressedOn.current;
    if (before === null || reads === before || reads.profile.kind === "loading" || reads.credentials.kind === "loading") return;
    pressedOn.current = null;
    const active = document.activeElement;
    if (active === null || active === document.body) document.getElementById(NEXT_STEP_FOCUS_ID)?.focus();
  }, [reads]);

  return (
    <div className="bj-practice">
      <PageHeader title="Practice" subtitle="Interview practice for a specific job, built from your resume evidence for it." />
      <NextStepBand
        outcome={outcome}
        onAction={(intent) => {
          if (intent !== "retry-setup") return;
          pressedOn.current = reads;
          retry();
        }}
      />
      <EmptyState heading="How a practice session works">
        <p>
          A practice session asks you five questions about one job, grounded in your resume evidence for it and in the company{"’"}s interview
          process when one was found. There is no company-blind practice here, so a session starts from an application: open one and use its
          Interview practice section.
        </p>
      </EmptyState>
    </div>
  );
}
