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

// The text between the braces of the first block whose header (the part before the `{`, such as
// "@media (max-width: 959px)") is exactly `header`. `rulesOf` cannot tell which @media a rule is
// in, so a test that cares reads the block first and then the rules inside it. Braces inside the
// block are counted, so nested rules do not end it early. null when there is no such block.
export function blockOf(css: string, header: string): string | null {
  const text = css.replace(/\/\*[\s\S]*?\*\//g, "");
  const at = text.search(new RegExp(`(^|[}\\s;])${header.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\s*\\{`));
  if (at === -1) return null;
  const start = text.indexOf("{", at) + 1;
  let depth = 1;
  for (let i = start; i < text.length; i++) {
    if (text[i] === "{") depth += 1;
    if (text[i] === "}") depth -= 1;
    if (depth === 0) return text.slice(start, i);
  }
  return null;
}

// The stylesheet with every @media block removed: what applies at all widths, before any query.
export function withoutMedia(css: string): string {
  let text = css.replace(/\/\*[\s\S]*?\*\//g, "");
  for (;;) {
    const at = text.indexOf("@media");
    if (at === -1) return text;
    let depth = 0;
    let end = text.indexOf("{", at);
    for (let i = end; i < text.length; i++) {
      if (text[i] === "{") depth += 1;
      if (text[i] === "}") depth -= 1;
      if (depth === 0) {
        end = i;
        break;
      }
    }
    text = text.slice(0, at) + text.slice(end + 1);
  }
}

// The custom properties a stylesheet's first `:root` rule declares, with `var(--x)` values resolved
// through the others, so a test can ask what `var(--bj-text-badge)` comes to.
export function tokenResolver(css: string): (value: string) => string {
  const root = blockOf(css, ":root") ?? "";
  const tokens: Record<string, string> = Object.fromEntries(
    [...root.matchAll(/(--[\w-]+)\s*:\s*([^;]+);/g)].map((match) => [match[1], match[2].replace(/\s+/g, " ").trim()]),
  );
  const resolve = (value: string): string => {
    const reference = /^var\((--[\w-]+)\)$/.exec(value.trim());
    return reference === null ? value.trim() : resolve(tokens[reference[1]]);
  };
  return resolve;
}
