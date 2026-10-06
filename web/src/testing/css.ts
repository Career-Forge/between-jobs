// Reads a stylesheet that a test loaded as text (vite's `?raw`; the package has no DOM, so no
// computed styles). Deliberately naive: it handles the plain `selector, selector { property:
// value; }` rules this project's stylesheets are written in, and only used by tests.

export interface CssRule {
  selectors: string[];
  declarations: Record<string, string>;
}

export function rulesOf(css: string): CssRule[] {
  const withoutComments = css.replace(/\/\*[\s\S]*?\*\//g, "");
  const rules: CssRule[] = [];
  for (const match of withoutComments.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
    const declarations: Record<string, string> = {};
    for (const declaration of match[2].split(";")) {
      const colon = declaration.indexOf(":");
      if (colon === -1) continue;
      declarations[declaration.slice(0, colon).trim()] = declaration.slice(colon + 1).trim();
    }
    rules.push({
      selectors: match[1]
        .split(",")
        .map((selector) => selector.trim())
        .filter((selector) => selector.length > 0),
      declarations,
    });
  }
  return rules;
}

// Every declaration of every rule that names exactly this selector (alone or in a list), the
// later rule winning a property both set.
export function declarationsFor(css: string, selector: string): Record<string, string> {
  const merged: Record<string, string> = {};
  for (const rule of rulesOf(css)) {
    if (rule.selectors.includes(selector)) Object.assign(merged, rule.declarations);
  }
  return merged;
}

// The left and right padding, in px, that a rule's declarations give: `padding` as one to four
// values, with `padding-left` / `padding-right` overriding it. null where a side is unset.
export function horizontalPaddingPx(declarations: Record<string, string>): [number | null, number | null] {
  let left: number | null = null;
  let right: number | null = null;
  const shorthand = declarations.padding?.split(/\s+/).map((part) => Number.parseFloat(part));
  if (shorthand !== undefined && shorthand.length > 0) {
    const [top, second = top, third = top, fourth = second] = shorthand;
    void third;
    right = second;
    left = fourth;
    void top;
  }
  if (declarations["padding-left"] !== undefined) left = Number.parseFloat(declarations["padding-left"]);
  if (declarations["padding-right"] !== undefined) right = Number.parseFloat(declarations["padding-right"]);
  return [left, right];
}
