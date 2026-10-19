import { useEffect, useState } from "react";

// The elapsed counter of a running search ("Searching... 7s"): cyan, mono, `role="timer"` with its
// live region off. A timer that announced every second would drown the page, so it is read only when
// a person goes to it; the page announces once when the results or an error arrive. The seconds are
// worked out from the clock each tick, so a tab that was throttled in the background catches up.

export function SearchTimer({ startedAt }: { startedAt: number }) {
  const [seconds, setSeconds] = useState(0);
  useEffect(() => {
    setSeconds(0);
    const tick = window.setInterval(() => setSeconds(Math.max(0, Math.floor((Date.now() - startedAt) / 1000))), 1000);
    return () => window.clearInterval(tick);
  }, [startedAt]);
  return (
    <p className="bj-disc-timer">
      <span className="bj-visually-hidden">Time spent searching: </span>
      <span role="timer" aria-live="off">
        {seconds}s
      </span>
    </p>
  );
}
