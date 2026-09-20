import { useEffect } from "react";

// Scrolls to the element a URL hash names, once the page it is on can have it.
// Applications Kanban K3's cross-nav (Generate Docs / Research Company / ...) jumps
// to a panel of the application workspace this way.
//
// `ready` is whether the page has rendered the thing the hash points at (the
// targeted element does not exist while the workspace is loading). It depends on
// `hash` too, because a hash-only navigation does not remount the page. And
// `alsoWhen` lists whatever ELSE decides whether the target is visible: a panel
// that mounts only after an asynchronous answer (the Hiring posts panel, gated on
// the server's feature status) sits in a slot that is hidden while it is empty, so
// scrolling to it before it exists is a silent no-op, and nothing else would
// scroll again when it arrived.
export function useScrollToHash(
  ready: boolean,
  hash: string,
  ...alsoWhen: readonly unknown[]
): void {
  useEffect(() => {
    if (!ready) return;
    const targetId = hash.replace(/^#/, "");
    if (!targetId) return;
    document.getElementById(targetId)?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [ready, hash, ...alsoWhen]);
}
