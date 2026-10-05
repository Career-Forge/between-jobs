import { describe, expect, it } from "vitest";
import { ApiError } from "./api";
import { CONFIRMATION_PHRASE, deletionErrorMessage, isConfirmed } from "./accountDeletion";

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
  it("explains a wrong phrase (422)", () => {
    const msg = deletionErrorMessage(new ApiError(422, "bad", "INVALID_INPUT"));
    expect(msg).toContain(CONFIRMATION_PHRASE);
    expect(msg).toContain("Nothing was deleted");
  });

  it("tells the person to sign in again on 401", () => {
    const msg = deletionErrorMessage(new ApiError(401, "no", "AUTH_REQUIRED"));
    expect(msg).toContain("Sign in again");
    expect(msg).toContain("Nothing was deleted");
  });

  it("says nothing was deleted and a retry is safe on a retryable 503", () => {
    const msg = deletionErrorMessage(new ApiError(503, "down", "PROVIDER_UNAVAILABLE", true));
    expect(msg).toContain("Nothing was deleted");
    expect(msg).toContain("safe to try again");
  });

  it("treats a non-retryable 503 as a generic failure", () => {
    const msg = deletionErrorMessage(new ApiError(503, "down", "PROVIDER_UNAVAILABLE", false));
    expect(msg).not.toContain("safe to try again");
    expect(msg).toContain("not deleted");
  });

  it("falls back to a generic message for other API errors", () => {
    expect(deletionErrorMessage(new ApiError(500, "boom"))).toContain("not deleted");
  });

  it("does not claim certainty when the request never got a reply", () => {
    const msg = deletionErrorMessage(new TypeError("Failed to fetch"));
    expect(msg).toContain("could not confirm");
  });
});
