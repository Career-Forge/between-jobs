import { describe, expect, it, vi } from "vitest";
import { ApiError } from "./api";
import {
  CONFIRMATION_PHRASE,
  accountCardReducer,
  deletionErrorMessage,
  initialAccountCardState,
  isConfirmed,
  runAccountDeletion,
} from "./accountDeletion";

describe("isConfirmed", () => {
  it("accepts the exact phrase", () => {
    expect(CONFIRMATION_PHRASE).toBe("delete my account");
    expect(isConfirmed("delete my account")).toBe(true);
  });

  it("ignores case, outer whitespace and repeated inner whitespace", () => {
    expect(isConfirmed("  Delete   My\tAccount  ")).toBe(true);
    expect(isConfirmed("DELETE MY ACCOUNT")).toBe(true);
  });

  it("rejects anything else", () => {
    for (const typed of ["", "   ", "delete", "delete my acount", "delete my account!", "delete-my-account", "delete my account please"]) {
      expect(isConfirmed(typed)).toBe(false);
    }
  });
});

describe("deletionErrorMessage", () => {
  it("explains a wrong phrase (422) and says nothing was deleted", () => {
    const msg = deletionErrorMessage(new ApiError(422, "bad", "INVALID_INPUT"));
    expect(msg).toContain(CONFIRMATION_PHRASE);
    expect(msg).toContain("Nothing was deleted");
  });

  it("says nothing was deleted and a retry is safe on a retryable 503", () => {
    const msg = deletionErrorMessage(new ApiError(503, "down", "PROVIDER_UNAVAILABLE", true));
    expect(msg).toContain("Nothing was deleted");
    expect(msg).toContain("safe to try again");
  });

  it("tells the person to sign in again on 401, without claiming anything about the data", () => {
    const msg = deletionErrorMessage(new ApiError(401, "no", "AUTH_REQUIRED"));
    expect(msg).toContain("Sign in again");
    expect(msg).not.toMatch(/nothing was deleted|not deleted/i);
  });

  it("warns that some data may already be removed for any other API error", () => {
    for (const err of [new ApiError(500, "boom"), new ApiError(503, "down", "PROVIDER_UNAVAILABLE", false)]) {
      const msg = deletionErrorMessage(err);
      expect(msg).toContain("Some of your data may already be removed");
      expect(msg).not.toContain("safe to try again");
      expect(msg).not.toContain("Nothing was deleted");
    }
  });

  it("does not claim certainty or an automatic sign-out when the request got no reply", () => {
    const msg = deletionErrorMessage(new TypeError("Failed to fetch"));
    expect(msg).toContain("could not confirm whether the deletion finished");
    expect(msg).toContain("Reload the page");
    expect(msg).not.toContain("Nothing was deleted");
  });
});

describe("runAccountDeletion", () => {
  it("signs out exactly once after a successful post", async () => {
    const post = vi.fn().mockResolvedValue(undefined);
    const signOut = vi.fn().mockResolvedValue(undefined);
    expect(await runAccountDeletion({ post, signOut })).toEqual({ ok: true });
    expect(post).toHaveBeenCalledTimes(1);
    expect(signOut).toHaveBeenCalledTimes(1);
  });

  it("swallows a signOut rejection and still reports success", async () => {
    const post = vi.fn().mockResolvedValue(undefined);
    const signOut = vi.fn().mockRejectedValue(new Error("session already gone"));
    expect(await runAccountDeletion({ post, signOut })).toEqual({ ok: true });
    expect(signOut).toHaveBeenCalledTimes(1);
  });

  it("returns the mapped message and does not sign out when the post fails", async () => {
    const error = new ApiError(503, "down", "PROVIDER_UNAVAILABLE", true);
    const post = vi.fn().mockRejectedValue(error);
    const signOut = vi.fn();
    expect(await runAccountDeletion({ post, signOut })).toEqual({
      ok: false,
      message: deletionErrorMessage(error),
    });
    expect(signOut).not.toHaveBeenCalled();
  });

  it("maps a non-ApiError failure too", async () => {
    const post = vi.fn().mockRejectedValue(new TypeError("Failed to fetch"));
    const result = await runAccountDeletion({ post, signOut: vi.fn() });
    expect(result).toEqual({ ok: false, message: deletionErrorMessage(new TypeError("x")) });
  });
});

describe("accountCardReducer", () => {
  it("typed updates only the text", () => {
    const before = { typed: "", busy: false, error: "old" };
    expect(accountCardReducer(before, { type: "typed", value: "abc" })).toEqual({
      typed: "abc",
      busy: false,
      error: "old",
    });
  });

  it("submitted sets busy and clears the error, keeping the text", () => {
    const before = { typed: "delete my account", busy: false, error: "old" };
    expect(accountCardReducer(before, { type: "submitted" })).toEqual({
      typed: "delete my account",
      busy: true,
      error: null,
    });
  });

  it("failed clears busy and sets the message, keeping the text", () => {
    const before = { typed: "delete my account", busy: true, error: null };
    expect(accountCardReducer(before, { type: "failed", message: "nope" })).toEqual({
      typed: "delete my account",
      busy: false,
      error: "nope",
    });
  });

  it("starts empty, idle and without an error", () => {
    expect(initialAccountCardState).toEqual({ typed: "", busy: false, error: null });
  });
});
