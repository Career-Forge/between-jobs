// Small helpers for tests that render a component to a string (renderToStaticMarkup) and then
// read the result: the package has no DOM environment. Deliberately naive -- they read the
// markup React itself produced, which is regular -- and only used by tests.

// The visible text of a piece of markup: tags dropped (a block-level element's end counts as a
// space, an inline one as nothing, so "<a>Terms</a>." reads "Terms."), whitespace collapsed, and
// the few entities React escapes in running text turned back.
export function textOfMarkup(markup: string): string {
  return markup
    .replace(/<\/(p|li|h[1-6]|dd|dt|section|div|header|footer|nav|main|ul|ol|dl|article)>|<br\s*\/?>/g, " ")
    .replace(/<[^>]+>/g, "")
    .replace(/&#x27;/g, "'")
    .replace(/&quot;/g, '"')
    .replace(/&amp;/g, "&")
    .replace(/\s+/g, " ")
    .trim();
}

// Every opening tag of one element name, as its attribute map.
export function tagsOf(markup: string, name: string): Record<string, string>[] {
  const tags: Record<string, string>[] = [];
  const open = new RegExp(`<${name}(\\s[^>]*)?>`, "g");
  for (const match of markup.matchAll(open)) {
    const attributes: Record<string, string> = {};
    for (const attribute of (match[1] ?? "").matchAll(/([a-zA-Z-]+)(?:="([^"]*)")?/g)) {
      attributes[attribute[1]] = attribute[2] ?? "";
    }
    tags.push(attributes);
  }
  return tags;
}

// The heading levels in document order: "<h1>...<h2>...<h3>" -> [1, 2, 3].
export function headingLevels(markup: string): number[] {
  return [...markup.matchAll(/<h([1-6])[\s>]/g)].map((match) => Number(match[1]));
}

// Every `<a ...>text</a>` as { attributes, text }, text being the visible text (a screen
// reader's `aria-label` is a separate attribute).
export function anchorsOf(markup: string): { attributes: Record<string, string>; text: string }[] {
  const found: { attributes: Record<string, string>; text: string }[] = [];
  for (const match of markup.matchAll(/<a(\s[^>]*)?>([\s\S]*?)<\/a>/g)) {
    const attributes: Record<string, string> = {};
    for (const attribute of (match[1] ?? "").matchAll(/([a-zA-Z-]+)(?:="([^"]*)")?/g)) {
      attributes[attribute[1]] = attribute[2] ?? "";
    }
    found.push({ attributes, text: textOfMarkup(match[2]) });
  }
  return found;
}
