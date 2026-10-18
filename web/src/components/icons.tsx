import type { ReactNode } from "react";

// The shell's icons, in one place: 24-unit grid, 1.6 stroke, round caps and joins, drawn in the
// text color so they follow hover, selection and the dark/maroon backgrounds. They are decoration:
// every use sits beside a visible label, so the SVG is hidden from assistive technology.

export type IconName =
  | "today"
  | "discover"
  | "hiring-signals"
  | "applications"
  | "practice"
  | "profile"
  | "settings"
  | "more"
  | "close";

const SHAPES: Record<IconName, ReactNode> = {
  today: <path d="M4 11l8-7 8 7v9a1 1 0 0 1-1 1h-4v-6H9v6H5a1 1 0 0 1-1-1z" />,
  discover: (
    <>
      <circle cx="11" cy="11" r="6.5" />
      <path d="M16 16l4.5 4.5" />
    </>
  ),
  "hiring-signals": (
    <>
      <circle cx="12" cy="12" r="1.8" />
      <path d="M8.4 8.4a5.1 5.1 0 0 0 0 7.2M15.6 8.4a5.1 5.1 0 0 1 0 7.2M5.6 5.6a9 9 0 0 0 0 12.8M18.4 5.6a9 9 0 0 1 0 12.8" />
    </>
  ),
  applications: (
    <>
      <rect x="3.5" y="5" width="17" height="15" rx="2" />
      <path d="M8 3.5v3M16 3.5v3M3.5 10h17" />
    </>
  ),
  practice: <path d="M4 5.5h16v10H10l-5 4v-4H4z" />,
  profile: (
    <>
      <circle cx="12" cy="8.5" r="3.5" />
      <path d="M5 20c.8-4 3.6-6 7-6s6.2 2 7 6" />
    </>
  ),
  settings: (
    <>
      <circle cx="12" cy="12" r="3" />
      <path d="M12 3v2.5M12 18.5V21M3 12h2.5M18.5 12H21M5.6 5.6l1.8 1.8M16.6 16.6l1.8 1.8M18.4 5.6l-1.8 1.8M7.4 16.6l-1.8 1.8" />
    </>
  ),
  more: (
    <>
      <circle cx="5.5" cy="12" r="1.2" />
      <circle cx="12" cy="12" r="1.2" />
      <circle cx="18.5" cy="12" r="1.2" />
    </>
  ),
  close: <path d="M6 6l12 12M18 6L6 18" />,
};

export function Icon({ name }: { name: IconName }) {
  return (
    <svg
      className="bj-icon"
      width="18"
      height="18"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      {SHAPES[name]}
    </svg>
  );
}
