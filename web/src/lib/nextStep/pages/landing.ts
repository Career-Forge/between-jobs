// The landing page's row (L0). Signed out is the only state the page can read, so the why line
// says that and nothing about an account, a profile or a key the visitor may or may not have.
// Signed in, the landing address is Today and this page's rows never run.

import { REGISTER_PATH } from "../../publicRoutes";
import { SIGNED_OUT_WHY } from "../global";
import { definePage, type NextStepRow } from "../types";

const L0: NextStepRow = {
  id: "L0",
  match: (input) => {
    if (input.signedIn) return null;
    return {
      sentence: "Create an account, or sign in if you have one.",
      action: { kind: "link", label: "Create an account", to: REGISTER_PATH },
      why: [SIGNED_OUT_WHY],
    };
  },
};

// Every file in pages/ exports its page as `rules`; index.ts collects them.
export const rules = definePage("landing", [L0]);
