import { useSyncExternalStore } from "react";

// Whether a CSS media query matches right now, re-read when it changes. Server rendering and
// browsers without matchMedia read as "does not match", so the wide layout is the default.
export function useMediaQuery(query: string): boolean {
  return useSyncExternalStore(
    (notify) => {
      if (typeof window === "undefined" || typeof window.matchMedia !== "function") return () => undefined;
      const list = window.matchMedia(query);
      list.addEventListener("change", notify);
      return () => list.removeEventListener("change", notify);
    },
    () => typeof window !== "undefined" && typeof window.matchMedia === "function" && window.matchMedia(query).matches,
    () => false,
  );
}

// The shell's own breakpoint: under 960px the rail is replaced by the top bar and tab bar.
export const PHONE_QUERY = "(max-width: 959px)";

export function useIsPhone(): boolean {
  return useMediaQuery(PHONE_QUERY);
}
