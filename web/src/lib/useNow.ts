import { useState } from "react";

// The moment a trail's "this year" is read from: fixed when the component mounts, so a date does not
// change between renders. A test passes its own.
export function useNow(override?: Date): Date {
  const [mounted] = useState(() => new Date());
  return override ?? mounted;
}
