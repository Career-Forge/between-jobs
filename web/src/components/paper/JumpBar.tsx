import { useId, useRef, useState, type KeyboardEvent, type MouseEvent } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { useScrollSpy, useScrolledPast } from "../../lib/useScrollSpy";
import { useScrollToHash } from "../../lib/useScrollToHash";
import { useDocumentMode } from "./DocumentLayout";
import "../../styles/paper.css";
import "../../styles/paper-doc.css";

// The section index of a long document page (the application workspace): where each part is, and
// which one is being read.
//
//   <JumpBar groups={[
//     { label: "Resume", links: [{ id: "ch-sources", label: "Tailor" }, { id: "generate", label: "Generate" }] },
//     { label: "Research", links: [{ id: "company-intel", label: "Company intel" }] },
//   ]} ready={workspaceLoaded} />
//
// A group is a label and a run of real links (`<a href="#id">`), outlined as one group; the section in
// view takes the selection fill with aria-current="location". The labels are not targets. At 1280px and
// up it is one row that sticks to the top of the page. From 960 to 1279 the groups wrap whole and it
// scrolls away with the page; once it has, a 44px "Jump to" row docks at the top in its place. On a
// phone it is only the "Jump to" select, in a sticky row, with the groups as its option groups and the
// bar's own labels as its options.
//
// A choice made with the pointer or from the open list goes at once. On a closed select the keyboard
// would fire a change per Arrow press, so Arrow/Home/End/type-ahead steps wait for Enter (Tab or Esc
// drops them): a keyboard user can reach any option, not just the next one. Until a section has been
// reached the select reads "Choose a section", so picking the first section is a change too.
//
// Following a link (or choosing an option) moves to its hash and useScrollToHash does the rest: scroll
// (instant under prefers-reduced-motion) and focus on the section's heading. The bar owns that hook, so
// the page does not call it again; `ready` is whether the sections are on the page yet. The marked
// section is only a mark: the bar never moves focus and never scrolls on its own. The sticky offset the
// hash scroll leaves under the bar is paper-doc.css's (scroll-padding on the page).

export interface JumpLink {
  /** The id of the section's anchor, without the "#". */
  id: string;
  label: string;
}

export interface JumpGroup {
  label: string;
  links: readonly JumpLink[];
}

export interface JumpBarProps {
  groups: readonly JumpGroup[];
  /** Whether the page has rendered the sections the links point at. */
  ready?: boolean;
  /** The navigation landmark's name. */
  label?: string;
}

const PLAIN_CLICK = (event: MouseEvent) => event.button === 0 && !event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey;

export function JumpBar({ groups, ready = true, label = "Sections on this page" }: JumpBarProps) {
  const mode = useDocumentMode();
  const location = useLocation();
  const navigate = useNavigate();
  const [presses, setPresses] = useState(0);
  const [bar, setBar] = useState<HTMLElement | null>(null);

  const shown = groups.filter((group) => group.links.length > 0);
  const ids = shown.flatMap((group) => group.links.map((link) => link.id));

  // A press on the section already in the address changes no hash, so the count is what makes the
  // scroll run again; a press on another section changes the hash and needs no help.
  useScrollToHash(ready, location.hash, presses);
  const active = useScrollSpy(ids, ready);
  const docked = useScrolledPast(bar, mode === "inline" && ready);

  if (shown.length === 0) return null;

  const go = (id: string) => {
    const hash = `#${id}`;
    if (location.hash === hash) setPresses((count) => count + 1);
    else navigate({ pathname: location.pathname, search: location.search, hash });
  };

  if (mode === "sheet") {
    return (
      <nav aria-label={label} className="bj-jump bj-jump--select">
        <JumpSelect groups={shown} active={active} onChoose={go} variant="row" />
      </nav>
    );
  }

  return (
    <>
      <nav ref={setBar} aria-label={label} className={mode === "margin" ? "bj-jump bj-jump--bar bj-jump--sticky" : "bj-jump bj-jump--bar"}>
        {shown.map((group) => (
          <JumpGroupView key={group.label} group={group} active={active} onChoose={go} />
        ))}
      </nav>
      {docked && (
        <div className="bj-jump bj-jump--dock">
          <JumpSelect groups={shown} active={active} onChoose={go} variant="dock" />
        </div>
      )}
    </>
  );
}

function JumpGroupView({ group, active, onChoose }: { group: JumpGroup; active: string | null; onChoose: (id: string) => void }) {
  const labelId = useId();
  return (
    <div role="group" aria-labelledby={labelId} className="bj-jump-group">
      <span id={labelId} className="bj-jump-label">
        {group.label}
      </span>
      <div className="bj-segmented bj-jump-segments">
        {group.links.map((link) => (
          <a
            key={link.id}
            href={`#${link.id}`}
            className="bj-selectable bj-jump-link"
            data-label={link.label}
            aria-current={active === link.id ? "location" : undefined}
            onClick={(event) => {
              if (!PLAIN_CLICK(event)) return;
              event.preventDefault();
              onChoose(link.id);
            }}
          >
            {link.label}
          </a>
        ))}
      </div>
    </div>
  );
}

// The keys that move a closed select's value without opening it (and so fire `change` one step at a time).
const STEP_KEYS: ReadonlySet<string> = new Set(["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "Home", "End", "PageUp", "PageDown"]);

function JumpSelect({
  groups,
  active,
  onChoose,
  variant,
}: {
  groups: readonly JumpGroup[];
  active: string | null;
  onChoose: (id: string) => void;
  variant: "row" | "dock";
}) {
  const selectId = useId();
  // Before any section has been reached the select shows a prompt, not the first section: choosing
  // the first section must be a change, or picking it would do nothing.
  const current = active ?? "";
  // A choice made with the keyboard on a closed select is held here until Enter. Browsers fire
  // `change` on every Arrow, Home, End or type-ahead step; navigating on each would move focus to a
  // heading on the first press and make every option past the next one unreachable.
  const [pending, setPending] = useState<string | null>(null);
  const stepping = useRef(false);

  const onKeyDown = (event: KeyboardEvent<HTMLSelectElement>) => {
    if (event.key === "Enter" && pending !== null) {
      event.preventDefault();
      const id = pending;
      setPending(null);
      onChoose(id);
      return;
    }
    if (event.key === "Escape") setPending(null);
    const modified = event.altKey || event.ctrlKey || event.metaKey;
    stepping.current = !modified && (STEP_KEYS.has(event.key) || (event.key.length === 1 && event.key !== " "));
  };

  return (
    <div className={`bj-jump-select bj-jump-select--${variant}`}>
      <label htmlFor={selectId} className="bj-jump-select-label">
        Jump to
      </label>
      <select
        id={selectId}
        className="bj-jump-select-control"
        value={pending ?? current}
        onKeyDown={onKeyDown}
        onKeyUp={() => {
          stepping.current = false;
        }}
        onBlur={() => setPending(null)}
        onChange={(event) => {
          const id = event.target.value;
          const viaKeyboardStep = stepping.current;
          stepping.current = false;
          if (id === "") return;
          if (viaKeyboardStep) setPending(id);
          else {
            setPending(null);
            onChoose(id);
          }
        }}
      >
        {active === null && (
          <option value="" disabled>
            Choose a section
          </option>
        )}
        {groups.map((group) => (
          <optgroup key={group.label} label={group.label}>
            {group.links.map((link) => (
              <option key={link.id} value={link.id} aria-current={active === link.id ? "location" : undefined}>
                {link.label}
              </option>
            ))}
          </optgroup>
        ))}
      </select>
    </div>
  );
}
