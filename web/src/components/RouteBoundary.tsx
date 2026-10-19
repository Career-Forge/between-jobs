import { Component, Suspense, type ReactNode } from "react";
import { useLocation } from "react-router-dom";
import { appPageNameFor } from "../lib/publicRoutes";
import { pageHasBand } from "./AppShell";
import { NextStepBand } from "./paper/NextStepBand";
import { PageHeader } from "./paper/PageHeader";
import { FailureState, LoadingState } from "./paper/StateViews";
import { useDrawsNoBand } from "./ShellBandContext";

// What stands where a signed-in page goes while its file is on the way, and if it cannot come. The pages are
// loaded the first time they are opened (App.tsx), so the public pages and the shell are all that the first
// download holds.
//
// While it loads: the page's heading, then the band's eyebrow and "Loading..." (which also holds the element the
// shell's "Skip to next step" points at) over placeholder rows, so the page is not a blank column and has its one
// heading from the first frame. The heading and the band stand where the page puts them; the page's subtitle and
// its own placeholder rows are not known here, so those still change when the page arrives.
//
// If it cannot come (the person went offline, or this tab was opened before a deploy and the file it asks for
// is gone): said plainly, with a Retry that reloads the site, which fetches the new build. Nothing was lost:
// no request had been made. The failure has the page's heading and no band, so the shell does not offer a skip
// link to one. Moving to another page clears it, so one page failing does not take the rest. Coming back to the
// same page without a reload fails again at once (React keeps the rejected import); Retry is the way out.
//
// This boundary also stands over the pages that are not loaded lazily, so an error thrown while one of them
// draws lands here too. That is not a connection problem and is not described as one.

/** Whether an error is a page's file that could not be fetched, as the browsers word it (Chrome, Firefox, Safari, Vite's CSS preload). */
export function isFileFetchFailure(error: unknown): boolean {
  if (!(error instanceof Error)) return false;
  if (error.name === "ChunkLoadError") return true;
  return /dynamically imported module|importing a module script failed|unable to preload|loading (css )?chunk/i.test(error.message);
}

function Waiting() {
  const { pathname } = useLocation();
  const band = pageHasBand(pathname);
  const name = appPageNameFor(pathname);
  return (
    <>
      {name !== null && <PageHeader title={name} />}
      {band && <NextStepBand outcome={{ status: "loading" }} />}
      <LoadingState rows={3} announce={!band} />
    </>
  );
}

type Failure = "file" | "render";

const MESSAGES: Record<Failure, string> = {
  file: "This page could not be loaded. Check your connection, then try again.",
  render: "Something went wrong on this page. Try again.",
};

function Failed({ failure }: { failure: Failure }) {
  const { pathname } = useLocation();
  // Nothing here is a next step: the shell leaves its "Skip to next step" link out while this is drawn.
  useDrawsNoBand(true);
  return (
    <>
      <PageHeader title={appPageNameFor(pathname) ?? "Something went wrong"} />
      <FailureState message={MESSAGES[failure]} onRetry={() => window.location.reload()} />
    </>
  );
}

interface CatchProps {
  children: ReactNode;
  /** The failure is cleared when this changes (the address). */
  resetKey: string;
}

interface CatchState {
  failure: Failure | null;
  resetKey: string;
}

class Catch extends Component<CatchProps, CatchState> {
  state: CatchState = { failure: null, resetKey: this.props.resetKey };

  static getDerivedStateFromError(error: unknown): Partial<CatchState> {
    return { failure: isFileFetchFailure(error) ? "file" : "render" };
  }

  static getDerivedStateFromProps(props: CatchProps, state: CatchState): Partial<CatchState> | null {
    return props.resetKey === state.resetKey ? null : { failure: null, resetKey: props.resetKey };
  }

  render() {
    if (this.state.failure === null) return this.props.children;
    return <Failed failure={this.state.failure} />;
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
