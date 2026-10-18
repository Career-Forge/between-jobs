// Two places the shell keeps free for the assistant, so that adding it later moves nothing:
//
//   the command palette -- a row in the rail, between the wordmark and the navigation, opened
//     by a button on a phone's top bar;
//   the assistant drawer -- a 360px panel on the right edge, over the page's margin.
//
// Neither exists yet and neither does anything: no key is bound, no handler is attached, nothing
// can take focus, and a screen reader meets neither. The rail row is drawn (dashed, labelled
// "reserved") so the layout is honest about the space; the drawer is `hidden`, so it takes none.
// Whoever builds them replaces these two components, and nothing else in the shell changes.

export function CommandPaletteSlot() {
  return (
    <div className="bj-palette-slot" data-reserved="command-palette" aria-hidden="true" inert>
      Command palette -- reserved
    </div>
  );
}

export function AssistantDrawerSlot() {
  return <div className="bj-assistant-slot" data-reserved="assistant-drawer" hidden aria-hidden="true" inert />;
}
