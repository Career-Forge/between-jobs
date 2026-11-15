import { describe, expect, it } from "vitest";
import applicationsRoutes from "../../../../src/between_jobs/api/applications_routes.py?raw";
import discoveryRoutes from "../../../../src/between_jobs/api/discovery_routes.py?raw";
import { postingBody } from "./PostingBlock";

// The page's rule for "this posting has text" (postingBody) is written against a sentence the API stores in two
// places, for a tracked Discover result or registry posting that arrived with none. If the API's wording changes and the
// page's does not, the rule stops matching and a posting with no text is offered the model-reading controls again with
// every other test green, so the sentences the API writes are read from its source here. (A node-environment file: the
// jsdom environment cannot import files outside web/ as text.)

const stored = (source: string) =>
  [...source.matchAll(/description_text=.*?"(\(no description available\))"|description_text = "(\(no description available\))"/g)].map((match) => match[1] ?? match[2]);

describe("the API's stand-in for a missing description, read from the API's source", () => {
  it("is written once by the Discover tracker and once by the registry tracker, and reads as no text on the page", () => {
    expect(stored(discoveryRoutes)).toHaveLength(1);
    expect(stored(applicationsRoutes)).toHaveLength(1);
    for (const literal of [...stored(discoveryRoutes), ...stored(applicationsRoutes)]) expect(postingBody(literal)).toBe("");
  });

  it("is the only stand-in either file stores for a missing description", () => {
    for (const source of [discoveryRoutes, applicationsRoutes]) expect(source.match(/no description/gi)).toHaveLength(1);
  });
});
