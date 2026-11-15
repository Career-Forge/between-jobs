// @vitest-environment jsdom
import { StrictMode } from "react";
import { describe, expect, it } from "vitest";
import { render } from "../testing/render";
import { job, response } from "../testing/discoverFixtures";
import { EMPTY_REQUEST } from "./discoverFacts";
import type { DiscoverHold, DiscoverHoldEntry } from "./discoverHold";
import { DiscoverHoldProvider, useDiscoverHold } from "./useDiscoverHold";

// The provider and the hook that hand the page its store: one store per provider mount, shared by every
// reader below it and kept across their remounts, dropped when the provider is keyed to someone else, and a
// private empty one for a reader with no provider above it. The store's own rules are discoverHold.test.ts.

function entry(): DiscoverHoldEntry {
  return { result: response({ scored: [job()] }), request: EMPTY_REQUEST, tracked: {}, at: Date.now() };
}

// A reader that reports the store it was given on every render.
function Reader({ seen }: { seen: DiscoverHold[] }) {
  seen.push(useDiscoverHold());
  return null;
}

describe("DiscoverHoldProvider", () => {
  it("gives every reader below it the same store", () => {
    const seen: DiscoverHold[] = [];
    render(
      <DiscoverHoldProvider>
        <Reader seen={seen} />
        <Reader seen={seen} />
      </DiscoverHoldProvider>,
    );
    expect(seen.length).toBe(2);
    expect(seen[0]).toBe(seen[1]);
  });

  it("keeps what was written across a reader leaving and another arriving, which is the page's own round trip", () => {
    const first: DiscoverHold[] = [];
    const second: DiscoverHold[] = [];
    const { rerender } = render(
      <DiscoverHoldProvider>
        <Reader seen={first} />
      </DiscoverHoldProvider>,
    );
    const written = entry();
    first[0].write(written);
    rerender(<DiscoverHoldProvider>{null}</DiscoverHoldProvider>);
    rerender(
      <DiscoverHoldProvider>
        <Reader seen={second} />
      </DiscoverHoldProvider>,
    );
    expect(second[0]).toBe(first[0]);
    expect(second[0].read()).toBe(written);
  });

  it("keeps one store across its own re-renders, and under StrictMode's double mount", () => {
    const seen: DiscoverHold[] = [];
    const tree = (
      <StrictMode>
        <DiscoverHoldProvider>
          <Reader seen={seen} />
        </DiscoverHoldProvider>
      </StrictMode>
    );
    const { rerender } = render(tree);
    seen[0].write(entry());
    rerender(tree);
    expect(seen.length).toBeGreaterThan(1);
    expect(new Set(seen).size).toBe(1);
    expect(seen[seen.length - 1].read()).not.toBeNull();
  });

  it("starts empty again for a provider keyed to someone else, and the first person's answer is not reachable from it", () => {
    const one: DiscoverHold[] = [];
    const other: DiscoverHold[] = [];
    const { rerender } = render(
      <DiscoverHoldProvider key="user-1">
        <Reader seen={one} />
      </DiscoverHoldProvider>,
    );
    one[0].write(entry());
    expect(one[0].read()).not.toBeNull();
    rerender(
      <DiscoverHoldProvider key="user-2">
        <Reader seen={other} />
      </DiscoverHoldProvider>,
    );
    expect(other[0]).not.toBe(one[0]);
    expect(other[other.length - 1].read()).toBeNull();
  });

  it("keeps the store while the key stays the same", () => {
    const seen: DiscoverHold[] = [];
    const { rerender } = render(
      <DiscoverHoldProvider key="user-1">
        <Reader seen={seen} />
      </DiscoverHoldProvider>,
    );
    const written = entry();
    seen[0].write(written);
    rerender(
      <DiscoverHoldProvider key="user-1">
        <Reader seen={seen} />
      </DiscoverHoldProvider>,
    );
    expect(seen[seen.length - 1].read()).toBe(written);
  });
});

describe("useDiscoverHold with no provider above it", () => {
  it("starts empty", () => {
    const seen: DiscoverHold[] = [];
    render(<Reader seen={seen} />);
    expect(seen[0].read()).toBeNull();
  });

  it("is private to its mount: a later mount does not find what an earlier one wrote", () => {
    const earlier: DiscoverHold[] = [];
    const later: DiscoverHold[] = [];
    const first = render(<Reader seen={earlier} />);
    earlier[0].write(entry());
    first.unmount();
    render(<Reader seen={later} />);
    expect(later[0]).not.toBe(earlier[0]);
    expect(later[0].read()).toBeNull();
  });

  it("keeps the same store for as long as it stays mounted", () => {
    const seen: DiscoverHold[] = [];
    const { rerender } = render(<Reader seen={seen} />);
    rerender(<Reader seen={seen} />);
    expect(new Set(seen).size).toBe(1);
  });

  it("is two separate stores for two readers", () => {
    const seen: DiscoverHold[] = [];
    render(
      <>
        <Reader seen={seen} />
        <Reader seen={seen} />
      </>,
    );
    expect(seen[0]).not.toBe(seen[1]);
  });
});
