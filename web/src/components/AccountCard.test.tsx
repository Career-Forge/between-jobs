import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../lib/api";
import type { AccountCardActions } from "./AccountCardView";

// Wiring only, same split as PersonalDetailsCard.test.tsx: the view is
// mocked to capture what the container hands it. Static rendering never
// re-renders, so this file only proves what is observable through apiFetch and
// signOut calls: the request body and the in-flight guard. The outcome logic
// (signOut once, swallowed signOut failure, messages) and the busy/error
// state transitions are tested as pure functions in lib/accountDeletion.test.ts.

const captured = vi.hoisted(() => ({
  props: null as null | { typed: string; busy: boolean; error: string | null; errorRef?: unknown; actions: AccountCardActions },
}));
const apiFetch = vi.hoisted(() => vi.fn());
const signOut = vi.hoisted(() => vi.fn());

vi.mock("./AccountCardView", () => ({
  AccountCardView: (props: typeof captured.props) => {
    captured.props = props;
    return null;
  },
}));
vi.mock("../lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../lib/api")>()),
  apiFetch,
}));
vi.mock("../auth", () => ({ useAuth: () => ({ signOut }) }));

import { AccountCard } from "./AccountCard";

function mount() {
  renderToStaticMarkup(<AccountCard />);
  if (captured.props === null) throw new Error("the view was not rendered");
  return captured.props;
}

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

beforeEach(() => {
  apiFetch.mockReset();
  signOut.mockReset();
  signOut.mockResolvedValue(undefined);
  captured.props = null;
});

describe("AccountCard wiring", () => {
  it("hands the view an empty, idle initial state", () => {
    const props = mount();
    expect(props.typed).toBe("");
    expect(props.busy).toBe(false);
    expect(props.error).toBeNull();
  });

  it("posts the exact confirmation body", async () => {
    apiFetch.mockResolvedValue(undefined);
    mount().actions.confirm();
    await flush();
    expect(apiFetch).toHaveBeenCalledTimes(1);
    expect(apiFetch).toHaveBeenCalledWith("/account/delete", {
      method: "POST",
      body: JSON.stringify({ confirm: "delete my account" }),
    });
    expect(signOut).toHaveBeenCalledTimes(1);
  });

  it("releases the double-submit guard after a failure, so a retry sends a second request", async () => {
    apiFetch.mockRejectedValueOnce(new ApiError(503, "down", "PROVIDER_UNAVAILABLE", true));
    const { actions } = mount();
    actions.confirm();
    await flush();
    expect(signOut).not.toHaveBeenCalled();

    apiFetch.mockResolvedValueOnce(undefined);
    actions.confirm();
    await flush();
    expect(apiFetch).toHaveBeenCalledTimes(2);
    expect(signOut).toHaveBeenCalledTimes(1);
  });

  it("sends one request for a second click while the first is in flight", async () => {
    let finish: () => void = () => {};
    apiFetch.mockReturnValue(new Promise<void>((resolve) => (finish = resolve)));
    const { actions } = mount();
    actions.confirm();
    actions.confirm();
    await flush();
    expect(apiFetch).toHaveBeenCalledTimes(1);
    finish();
    await flush();
    expect(signOut).toHaveBeenCalledTimes(1);
  });
});
