// The Practice page's one row. The page is a pointer: practice sessions are grounded in a specific
// job and its resume evidence, so they live inside each application, and this page makes no request
// of its own. The global rows still run first (a signed-in page is held until the profile read has
// answered), so a person with no profile is sent there before being pointed anywhere.
//
//   R1  always        "Practice lives inside each application."   Go to Applications

import { definePage, type NextStepRow } from "../types";

const R1: NextStepRow = {
  id: "R1",
  match: () => ({
    sentence: "Practice lives inside each application.",
    action: { kind: "link", label: "Go to Applications", to: "/applications" },
    why: ["practice questions cite each company's interview process when one was found"],
  }),
};

export const rules = definePage("practice", [R1]);
