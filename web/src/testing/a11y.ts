// Accessibility check for component tests: runs axe-core over a rendered container (vitest with the
// jsdom environment, see render.tsx) and fails on any serious or critical violation.
//
// WHAT THIS CANNOT SEE. jsdom does no layout and paints nothing, so axe's `color-contrast` rule
// cannot run here and is switched off. Contrast is pinned a different way: tokens.test.ts computes
// the ratio of every color pair the paper stylesheet draws, and the components only use those pairs. The
// same goes for target size and anything that depends on a stylesheet: this checks the markup's
// roles, names, ARIA attributes and structure, not how it looks.
//
// Pass the element to check. A dialog rendered through a portal is outside the test's container,
// so check `document.body` for those.

import axe from "axe-core";

const BLOCKING = new Set(["serious", "critical"]);

export interface A11yViolation {
  id: string;
  impact: string;
  help: string;
  targets: string[];
}

export async function seriousA11yViolations(root: Element): Promise<A11yViolation[]> {
  const results = await axe.run(root, {
    // Needs layout, which jsdom does not have.
    rules: { "color-contrast": { enabled: false } },
    resultTypes: ["violations"],
  });
  return results.violations
    .filter((violation) => BLOCKING.has(violation.impact ?? ""))
    .map((violation) => ({
      id: violation.id,
      impact: violation.impact ?? "",
      help: violation.help,
      targets: violation.nodes.map((node) => node.target.join(" ")),
    }));
}

// Throws, naming each violation and where it is, when the markup has a serious or critical one.
export async function expectNoSeriousA11yViolations(root: Element): Promise<void> {
  const violations = await seriousA11yViolations(root);
  if (violations.length === 0) return;
  const lines = violations.map(
    (violation) => `${violation.impact} ${violation.id}: ${violation.help} (${violation.targets.join("; ")})`,
  );
  throw new Error(`Accessibility violations:\n${lines.join("\n")}`);
}

// The nodes where one named axe rule finds a violation, whatever its impact: for a rule the page must keep
// (such as landmark-unique, which is only "moderate") that the serious-and-critical check above lets pass.
export async function violationsOfRule(root: Element, ruleId: string): Promise<string[]> {
  const results = await axe.run(root, { runOnly: [ruleId], resultTypes: ["violations"] });
  return results.violations.flatMap((violation) => violation.nodes.map((node) => node.target.join(" ")));
}
