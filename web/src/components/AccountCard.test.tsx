import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../lib/api";
import type { AccountCardActions } from "./AccountCardView";

// Wiring only, same split as PersonalDetailsCard.test.tsx: the view is
// mocked to capture what the container hands it. Static rendering never
// re-renders, so busy/error are checked through apiFetch and signOut calls.

const captured = vi.hoisted(() => ({
  props: null as null | { typed: string; busy: boolean; error: string | null; actions: AccountCardActions },
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
  it("starts empty, idle and without an error", () => {
    const props = mount();
    expect(props.typed).toBe("");
    expect(props.busy).toBe(false);
    expect(props.error).toBeNull();
  });

  it("posts the exact confirmation body, then signs out", async () => {
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

  it("does not sign out on a retryable 503, and allows trying again", async () => {
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

  it("still ends after a failed signOut without reporting a false failure", async () => {
    apiFetch.mockResolvedValue(undefined);
    signOut.mockRejectedValue(new Error("session already gone"));
    mount().actions.confirm();
    await flush();
    expect(signOut).toHaveBeenCalledTimes(1);
  });
});
