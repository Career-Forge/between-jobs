import { isValidElement, type ReactElement } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { FirstRunChecklist } from "./FirstRunChecklist";

// The one decision the connected checklist makes: it is keyed by the signed-in person, so a
// second account on the same browser gets a fresh card (useState initialisers do not re-run
// for the same component instance, and the dismissed flag is read in one) rather than the
// first account's. The hook's own reads and writes are tested in lib/useFirstRun.test.tsx.

const auth = vi.hoisted(() => ({ session: null as { user: { id: string } } | null }));

// The real auth module pulls in the Supabase client, which throws at import without env vars.
vi.mock("../auth", () => ({ useAuth: () => ({ session: auth.session }) }));
vi.mock("../lib/useFirstRun", () => ({ useFirstRun: () => ({ view: { visible: false }, dismiss: () => {} }) }));

function render(): ReactElement | null {
  const node = FirstRunChecklist();
  if (node === null) return null;
  if (!isValidElement(node)) throw new Error("expected an element");
  return node as ReactElement<{ userId: string }>;
}

beforeEach(() => {
  auth.session = null;
});

describe("FirstRunChecklist", () => {
  it("shows nothing for a signed-out visitor", () => {
    expect(render()).toBeNull();
  });

  it("is keyed by the person's id, and hands that id down", () => {
    auth.session = { user: { id: "user-1" } };
    const element = render() as ReactElement<{ userId: string }>;
    expect(element.key).toBe("user-1");
    expect(element.props.userId).toBe("user-1");
  });

  it("gets a different key for a different person, so React starts the card afresh", () => {
    auth.session = { user: { id: "user-1" } };
    const first = (render() as ReactElement).key;
    auth.session = { user: { id: "user-2" } };
    const second = (render() as ReactElement).key;
    expect(first).not.toBe(second);
  });
});
