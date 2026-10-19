import { createContext, useContext, useLayoutEffect } from "react";

// The shell draws "Skip to next step" for every address that normally has a next-step band
// (AppShell.tsx: pageHasBand), but it does not know what the page inside it is drawing. The
// tester-programme gate can put its own page, or a bare "Loading...", at ANY address, and none of
// them has a band: the link would point at nothing. The gate says so through this, for as long as
// it is the one drawing, and the shell leaves the link out.

type Report = (drawsNoBand: boolean) => void;

export const ShellBandContext = createContext<Report | null>(null);

/** Tells the shell that what this component stands in for draws no next-step band while `drawsNoBand` is true. */
export function useDrawsNoBand(drawsNoBand: boolean): void {
  const report = useContext(ShellBandContext);
  // Before paint, so the link is never offered for a frame at a page that has nothing to skip to.
  useLayoutEffect(() => {
    if (!drawsNoBand || report === null) return;
    report(true);
    return () => report(false);
  }, [drawsNoBand, report]);
}
