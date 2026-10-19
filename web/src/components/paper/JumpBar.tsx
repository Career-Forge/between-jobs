import { useId, useState, type MouseEvent } from "react";
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
  // The select always shows something; before any section has been reached that is the first one.
  const current = active ?? groups[0].links[0].id;
  return (
    <div className={`bj-jump-select bj-jump-select--${variant}`}>
      <label htmlFor={selectId} className="bj-jump-select-label">
        Jump to
      </label>
      <select id={selectId} className="bj-jump-select-control" value={current} onChange={(event) => onChoose(event.target.value)}>
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
