import { useEffect } from "react";

// Moves keyboard focus where a model asked (see the FOCUS notes in the model
// files), once, and tells the model so. Generic over the model's own target type:
// the model owns which controls focus can go to, and `resolveId` (a stable,
// module-level function) turns one into an element id.
export function useFocusRequests<T>(
  request: { id: number; target: T } | null,
  consume: (id: number) => void,
  resolveId: (target: T) => string,
): void {
  useEffect(() => {
    if (request === null) return;
    document.getElementById(resolveId(request.target))?.focus();
    consume(request.id);
  }, [request, consume, resolveId]);
}
