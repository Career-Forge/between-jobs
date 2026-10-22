import { useId, useRef, useState, type KeyboardEvent } from "react";
import { TOUR, TOUR_TABS } from "../../content/tour";
import { TOUR_MOCKS } from "./TourMocks";
import "../../styles/tour.css";

// The home page's product tour: five tabs, each a drawing of one screen of the product built from one
// fictional sample role, with a sample next step under them that rewrites for the tab in view.
//
// Real tabs (WAI-ARIA tabs pattern): a tablist of five buttons, each controlling its own tabpanel, a
// roving tabindex so the strip is one Tab stop, and Left, Right, Home and End to move between tabs.
// Moving to a tab selects it (the drawings are cheap and nothing is fetched, so there is nothing to
// wait for). One panel shows at a time; the other four are `hidden` and draw nothing. The selected tab
// uses the one selection style (maroon fill, ivory text, weight 600, aria-selected), never an underline.
//
// Everything inside a panel is a picture. The depicted buttons are outlined spans, not controls, so the
// page's one cyan fill stays the hero's, and the only things in the tour that take focus are the tabs
// and the selected panel itself (it has no focusable content, so it is a stop of its own, and a reader
// can scroll it). The sample next step is a polite live region: choosing a tab announces which tab and
// what its sample step says, the tab's name first so a listener knows what changed. On a phone the strip
// scrolls sideways inside its own box and never widens the page. Under prefers-reduced-motion nothing
// fades (tour.css).

export function ProductTour() {
  const [active, setActive] = useState(0);
  const base = useId();
  const tabs = useRef<(HTMLButtonElement | null)[]>([]);
  const current = TOUR_TABS[active] ?? TOUR_TABS[0];

  function choose(index: number, focus: boolean) {
    setActive(index);
    const tab = tabs.current[index];
    if (tab === null || tab === undefined) return;
    if (focus) tab.focus();
    // A tab partly outside the scrolling strip is brought into it, without moving the page.
    if (typeof tab.scrollIntoView === "function") tab.scrollIntoView({ block: "nearest", inline: "nearest" });
  }

  function onKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    // A chord is the browser's or the system's, not the tab strip's: Alt+Left and Cmd+Left are Back, Cmd+Right
    // is Forward, Ctrl+Home and Cmd+End move through the page. Only the bare key moves between tabs.
    if (event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;
    const last = TOUR_TABS.length - 1;
    let next: number | null = null;
    if (event.key === "ArrowRight") next = active === last ? 0 : active + 1;
    if (event.key === "ArrowLeft") next = active === 0 ? last : active - 1;
    if (event.key === "Home") next = 0;
    if (event.key === "End") next = last;
    if (next === null) return;
    event.preventDefault();
    choose(next, true);
  }

  return (
    <div className="bj-tour">
      <p className="bj-tour-sample-label">
        <span className="bj-tour-chip">{TOUR.sampleTag}</span> {TOUR.sampleLabel}
      </p>

      <div role="tablist" aria-label={TOUR.tablistLabel} className="bj-tour-tabs" onKeyDown={onKeyDown}>
        {TOUR_TABS.map((tab, index) => {
          const selected = index === active;
          return (
            <button
              key={tab.id}
              ref={(element) => {
                tabs.current[index] = element;
              }}
              type="button"
              role="tab"
              id={`${base}-tab-${tab.id}`}
              className="bj-tour-tab bj-selectable"
              aria-selected={selected}
              aria-controls={`${base}-panel-${tab.id}`}
              tabIndex={selected ? 0 : -1}
              onClick={() => choose(index, false)}
            >
              <span className="bj-tour-tab-number" aria-hidden="true">
                {`0${index + 1}`}
              </span>
              {tab.label}
            </button>
          );
        })}
      </div>

      {TOUR_TABS.map((tab) => {
        const selected = tab.id === current.id;
        const Mock = TOUR_MOCKS[tab.id];
        return (
          <div
            key={tab.id}
            role="tabpanel"
            id={`${base}-panel-${tab.id}`}
            className="bj-tour-panel"
            aria-labelledby={`${base}-tab-${tab.id}`}
            tabIndex={0}
            hidden={!selected}
          >
            {selected && <Mock />}
          </div>
        );
      })}

      <section className="bj-tour-next" aria-label={TOUR.nextLabel}>
        <p className="bj-tour-next-tag">{TOUR.nextLabel}</p>
        <div className="bj-tour-next-live" role="status" aria-live="polite" aria-atomic="true">
          <div key={current.id} className="bj-tour-next-text">
            <span className="bj-visually-hidden">{`${current.label}, screen ${TOUR_TABS.indexOf(current) + 1} of ${TOUR_TABS.length}. `}</span>
            <p className="bj-tour-next-sentence">{current.next.sentence}</p>
            <p className="bj-tour-next-why">why: {current.next.why.join(" · ")}</p>
          </div>
        </div>
      </section>
    </div>
  );
}
