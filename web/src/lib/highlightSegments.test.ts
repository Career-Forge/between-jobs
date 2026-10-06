import { describe, expect, it } from "vitest";
import { highlightSegments, type Segment } from "./highlightSegments";

// What the pieces say when put back together: always the text itself.
function joined(segments: readonly Segment[]): string {
  return segments.map((segment) => segment.text).join("");
}

// A half of a character with no other half: what "cutting an emoji in two" leaves behind.
const LONE_SURROGATE = /[\ud800-\udbff](?![\udc00-\udfff])|(?<![\ud800-\udbff])[\udc00-\udfff]/;

function marked(segments: readonly Segment[]): string[] {
  return segments.filter((segment) => segment.highlighted).map((segment) => segment.text);
}

describe("highlightSegments", () => {
  it("is nothing for no text, and one plain piece when there is nothing to mark", () => {
    expect(highlightSegments("", [])).toEqual([]);
    expect(highlightSegments("", [{ start: 0, end: 3 }])).toEqual([]);
    expect(highlightSegments("Pat Example", [])).toEqual([{ text: "Pat Example", highlighted: false }]);
  });

  it("splits around one span into plain, marked, plain", () => {
    expect(highlightSegments("Hello Pat Example here", [{ start: 6, end: 17 }])).toEqual([
      { text: "Hello ", highlighted: false },
      { text: "Pat Example", highlighted: true },
      { text: " here", highlighted: false },
    ]);
  });

  it("handles a span at the very start and one that ends exactly at the end", () => {
    expect(highlightSegments("Pat Example", [{ start: 0, end: 3 }])).toEqual([
      { text: "Pat", highlighted: true },
      { text: " Example", highlighted: false },
    ]);
    expect(highlightSegments("Pat Example", [{ start: 4, end: 11 }])).toEqual([
      { text: "Pat ", highlighted: false },
      { text: "Example", highlighted: true },
    ]);
    expect(highlightSegments("Pat", [{ start: 0, end: 3 }])).toEqual([{ text: "Pat", highlighted: true }]);
  });

  it("keeps the order of the text whatever order the spans come in", () => {
    const text = "one two three";
    const segments = highlightSegments(text, [
      { start: 8, end: 13 },
      { start: 0, end: 3 },
    ]);
    expect(marked(segments)).toEqual(["one", "three"]);
    expect(joined(segments)).toBe(text);
  });

  it("makes overlapping spans one marked piece", () => {
    const segments = highlightSegments("abcdefghij", [
      { start: 2, end: 6 },
      { start: 4, end: 8 },
    ]);
    expect(segments).toEqual([
      { text: "ab", highlighted: false },
      { text: "cdefgh", highlighted: true },
      { text: "ij", highlighted: false },
    ]);
  });

  it("makes a span inside another vanish into it", () => {
    expect(marked(highlightSegments("abcdefghij", [{ start: 1, end: 9 }, { start: 3, end: 5 }]))).toEqual([
      "bcdefghi",
    ]);
  });

  it("makes adjacent spans one marked piece, not two marks side by side", () => {
    const segments = highlightSegments("abcdef", [
      { start: 0, end: 3 },
      { start: 3, end: 6 },
    ]);
    expect(segments).toEqual([{ text: "abcdef", highlighted: true }]);
  });

  it("leaves spans with a gap of even one character as two marks", () => {
    expect(marked(highlightSegments("abcdef", [{ start: 0, end: 2 }, { start: 3, end: 6 }]))).toEqual([
      "ab",
      "def",
    ]);
  });

  it("cuts a span that runs past the end at the end", () => {
    expect(highlightSegments("Pat Example", [{ start: 4, end: 999 }])).toEqual([
      { text: "Pat ", highlighted: false },
      { text: "Example", highlighted: true },
    ]);
  });

  it("ignores a span that starts at or past the end, is empty, or runs backwards", () => {
    const text = "Pat Example";
    for (const span of [
      { start: 11, end: 20 },
      { start: 99, end: 120 },
      { start: 3, end: 3 },
      { start: 7, end: 2 },
      { start: -5, end: -1 },
    ]) {
      expect(highlightSegments(text, [span]), JSON.stringify(span)).toEqual([
        { text, highlighted: false },
      ]);
    }
  });

  it("cuts a span that starts before the text at the start", () => {
    expect(highlightSegments("Pat Example", [{ start: -4, end: 3 }])).toEqual([
      { text: "Pat", highlighted: true },
      { text: " Example", highlighted: false },
    ]);
  });

  it("ignores spans that are not whole numbers, and keeps the good ones beside them", () => {
    const segments = highlightSegments("Pat Example", [
      { start: Number.NaN, end: 3 },
      { start: 0, end: Number.POSITIVE_INFINITY },
      { start: 1.5, end: 3 },
      { start: 4, end: 11 },
    ]);
    expect(marked(segments)).toEqual(["Example"]);
    expect(joined(segments)).toBe("Pat Example");
  });

  it("never changes the text: the pieces always put back together into it", () => {
    const text = "The quick brown fox jumps over the lazy dog";
    for (const spans of [
      [],
      [{ start: 0, end: text.length }],
      [{ start: 4, end: 9 }, { start: 16, end: 19 }, { start: 40, end: 400 }],
      [{ start: -3, end: 2 }, { start: 30, end: 12 }],
    ]) {
      expect(joined(highlightSegments(text, spans))).toBe(text);
    }
  });

  it("never makes an empty piece", () => {
    for (const segment of highlightSegments("abc", [{ start: 0, end: 1 }, { start: 1, end: 2 }, { start: 2, end: 3 }])) {
      expect(segment.text).not.toBe("");
    }
  });

  it("does not change the spans it is given", () => {
    const spans = [
      { start: 2, end: 6 },
      { start: 4, end: 8 },
    ];
    highlightSegments("abcdefghij", spans);
    expect(spans).toEqual([
      { start: 2, end: 6 },
      { start: 4, end: 8 },
    ]);
  });
});

// The server's offsets are UTF-16 code units, which is what a JavaScript string counts: an
// emoji is two of them. So the offsets are used as they are, and the value comes out whole.
describe("highlightSegments on text outside the basic plane", () => {
  it("marks the value that follows an emoji, at the offsets the server computed in UTF-16", () => {
    const text = "\u{1F680} Pat Example";
    // the rocket is two code units, so "Pat Example" starts at 3 (a code point count would say 2)
    expect("\u{1F680}".length).toBe(2);
    const start = text.indexOf("Pat Example");
    expect(start).toBe(3);
    const segments = highlightSegments(text, [{ start, end: start + "Pat Example".length }]);
    expect(segments).toEqual([
      { text: "\u{1F680} ", highlighted: false },
      { text: "Pat Example", highlighted: true },
    ]);
  });

  it("marks a value that is itself an emoji, whole", () => {
    const text = "Shipped \u{1F680}\u{1F680} fast";
    const start = text.indexOf("\u{1F680}");
    const segments = highlightSegments(text, [{ start, end: start + 4 }]);
    expect(marked(segments)).toEqual(["\u{1F680}\u{1F680}"]);
    expect(joined(segments)).toBe(text);
  });

  it("works across several emoji before and after a value", () => {
    const text = "\u{1F468}\u{200D}\u{1F4BB}\u{1F680} Dev \u{1F31F}\u{1F31F}";
    const start = text.indexOf("Dev");
    const segments = highlightSegments(text, [{ start, end: start + 3 }]);
    expect(marked(segments)).toEqual(["Dev"]);
    expect(joined(segments)).toBe(text);
  });

  it("does not cut an emoji in half when an offset lands between its two units", () => {
    const text = "a\u{1F680}b";
    // a span from the middle of the rocket: moved back to take the whole character
    expect(highlightSegments(text, [{ start: 2, end: 3 }])).toEqual([
      { text: "a", highlighted: false },
      { text: "\u{1F680}", highlighted: true },
      { text: "b", highlighted: false },
    ]);
    // a span that ends in the middle of it: moved on to take the whole character
    expect(highlightSegments(text, [{ start: 0, end: 2 }])).toEqual([
      { text: "a\u{1F680}", highlighted: true },
      { text: "b", highlighted: false },
    ]);
    // and no piece is ever a lone half of a character
    for (const start of [0, 1, 2, 3]) {
      for (const end of [1, 2, 3, 4]) {
        for (const segment of highlightSegments(text, [{ start, end }])) {
          expect(LONE_SURROGATE.test(segment.text)).toBe(false);
        }
      }
    }
  });

  it("a span ending at the end of a text that ends in an emoji, and starting at 0", () => {
    const text = "\u{1F680}";
    expect(highlightSegments(text, [{ start: 0, end: 2 }])).toEqual([{ text, highlighted: true }]);
    expect(highlightSegments(text, [{ start: 0, end: 99 }])).toEqual([{ text, highlighted: true }]);
  });
});

// The guards have edges: a text that begins or ends with the emoji (the unit before the span has no
// neighbour to look at), and characters at the very ends of the range the surrogate pairs cover
// (U+10000 is D800 DC00 and U+10FFFF is DBFF DFFF).
describe("highlightSegments at the edges of a text and of the surrogate range", () => {
  it("does not cut a character at the start of the text when the span starts inside it", () => {
    expect(highlightSegments("\u{1F680}abc", [{ start: 1, end: 3 }])).toEqual([
      { text: "\u{1F680}a", highlighted: true },
      { text: "bc", highlighted: false },
    ]);
  });

  it("does not cut a character at the end of the text when the span ends inside it", () => {
    expect(highlightSegments("ab\u{1F680}", [{ start: 0, end: 3 }])).toEqual([{ text: "ab\u{1F680}", highlighted: true }]);
  });

  it("keeps the first and the last character a pair can be whole, in the middle of a text", () => {
    for (const character of ["\u{10000}", "\u{10FFFF}", "\u{1F680}"]) {
      expect(character.length).toBe(2);
      const text = `a${character}b`;
      // a span that starts inside it, and one that ends inside it
      expect(marked(highlightSegments(text, [{ start: 2, end: 3 }])), character).toEqual([character]);
      const firstPiece = highlightSegments(text, [{ start: 0, end: 2 }])[0];
      expect(firstPiece, character).toEqual({ text: `a${character}`, highlighted: true });
    }
  });

  it("never leaves a lone half of a character in a piece, for any span over texts with an emoji at an end", () => {
    for (const text of ["\u{1F680}abc", "ab\u{1F680}", "\u{10000}x\u{10FFFF}"]) {
      for (let start = 0; start <= text.length + 1; start += 1) {
        for (let end = 0; end <= text.length + 1; end += 1) {
          const segments = highlightSegments(text, [{ start, end }]);
          expect(joined(segments)).toBe(text);
          for (const segment of segments) {
            expect(LONE_SURROGATE.test(segment.text), `${JSON.stringify(text)} ${start}-${end}`).toBe(false);
          }
        }
      }
    }
  });
});

describe("highlightSegments on combining marks", () => {
  it("marks a name written as a letter plus a combining accent, accent included", () => {
    const text = "Name: José Alvarez";
    const value = "José";
    expect(value.length).toBe(5); // the accent is a unit of its own
    const start = text.indexOf(value);
    const segments = highlightSegments(text, [{ start, end: start + value.length }]);
    expect(segments).toEqual([
      { text: "Name: ", highlighted: false },
      { text: value, highlighted: true },
      { text: " Alvarez", highlighted: false },
    ]);
  });

  it("is the same on the precomposed spelling, where the offsets differ by one", () => {
    const text = "Name: José Alvarez";
    const value = "José";
    const start = text.indexOf(value);
    expect(marked(highlightSegments(text, [{ start, end: start + value.length }]))).toEqual([value]);
  });

  it("keeps a combining mark with its letter when the span covers both, and joins back whole", () => {
    const text = "áb́c";
    const segments = highlightSegments(text, [{ start: 0, end: 2 }]);
    expect(segments).toEqual([
      { text: "á", highlighted: true },
      { text: "b́c", highlighted: false },
    ]);
    expect(joined(segments)).toBe(text);
  });

  it("handles combining marks and an emoji in the same text", () => {
    const text = "\u{1F680} Zoë \u{1F31F}";
    const value = "Zoë";
    const start = text.indexOf(value);
    const segments = highlightSegments(text, [{ start, end: start + value.length }]);
    expect(marked(segments)).toEqual([value]);
    expect(joined(segments)).toBe(text);
  });
});
