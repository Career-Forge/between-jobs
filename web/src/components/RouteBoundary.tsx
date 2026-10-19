import { Component, Suspense, type ReactNode } from "react";
import { useLocation } from "react-router-dom";
import { pageHasBand } from "./AppShell";
import { NextStepBand } from "./paper/NextStepBand";
import { FailureState, LoadingState } from "./paper/StateViews";

// What stands where a signed-in page goes while its file is on the way, and if it cannot come. The pages are
// loaded the first time they are opened (App.tsx), so the public pages and the shell are all that the first
// download holds.
//
// While it loads: the page's own frame in miniature, the band's eyebrow and "Loading..." (which also holds
// the element the shell's "Skip to next step" points at) over the placeholder rows, so the page is not a
// blank column and nothing jumps when it arrives.
//
// If it cannot come (the person went offline, or this tab was opened before a deploy and the file it asks for
// is gone): said plainly, with a Retry that reloads the site, which fetches the new build. Nothing was lost:
// no request had been made. Moving to another page clears it, so one page failing does not take the rest.

function Waiting() {
  const { pathname } = useLocation();
  const band = pageHasBand(pathname);
  return (
    <>
      {band && <NextStepBand outcome={{ status: "loading" }} />}
      <LoadingState rows={3} announce={!band} />
    </>
  );
}

interface CatchProps {
  children: ReactNode;
  /** The failure is cleared when this changes (the address). */
  resetKey: string;
}

class Catch extends Component<CatchProps, { failed: boolean; resetKey: string }> {
  state = { failed: false, resetKey: this.props.resetKey };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  static getDerivedStateFromProps(props: CatchProps, state: { failed: boolean; resetKey: string }) {
    return props.resetKey === state.resetKey ? null : { failed: false, resetKey: props.resetKey };
  }

  render() {
    if (!this.state.failed) return this.props.children;
    return <FailureState message="This page could not be loaded. Check your connection, then try again." onRetry={() => window.location.reload()} />;
  }
}

export function RouteBoundary({ children }: { children: ReactNode }) {
  const { pathname } = useLocation();
  return (
    <Catch resetKey={pathname}>
      <Suspense fallback={<Waiting />}>{children}</Suspense>
    </Catch>
  );
}
