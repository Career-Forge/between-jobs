// Cuts a text into the pieces a screen draws, so the part a value came from can be marked.
//
// OFFSETS ARE JAVASCRIPT STRING INDICES, that is UTF-16 code units. The server converts the
// offsets it finds (Python counts code points) into exactly these before it sends them
// (`span_unit: "utf16"`), so `text.slice(start, end)` is the value and nothing here converts:
// an emoji before a value is two units long in a JS string and the offsets already say so. A
// span can still end up between the two halves of one emoji (an offset from somewhere else, or
// a server that counted differently); that is moved outward to the whole character rather than
// cut through it, since half of a character is not text anyone can read.
//
// A span that cannot be a place in this text is ignored, not guessed at: not a whole number,
// nothing left once it is cut to the text, or empty. A span that runs past the end is cut at
// the end. Spans that overlap or touch become one highlighted piece, so a value that was found
// as two neighbouring parts is marked once.
//
// Pure module: no React, no DOM.

export interface TextSpan {
  start: number;
  end: number;
}

export interface Segment {
  text: string;
  highlighted: boolean;
}

function isHighSurrogate(unit: number): boolean {
  return unit >= 0xd800 && unit <= 0xdbff;
}

function isLowSurrogate(unit: number): boolean {
  return unit >= 0xdc00 && unit <= 0xdfff;
}

// The span as it applies to `text`, or null when it is not a place in it.
function fitted(text: string, span: TextSpan): TextSpan | null {
  if (!Number.isInteger(span.start) || !Number.isInteger(span.end)) return null;
  let start = Math.max(0, span.start);
  let end = Math.min(text.length, span.end);
  if (start >= end) return null;
  // Not between the two halves of one character.
  if (start > 0 && isLowSurrogate(text.charCodeAt(start)) && isHighSurrogate(text.charCodeAt(start - 1))) {
    start -= 1;
  }
  if (end < text.length && isHighSurrogate(text.charCodeAt(end - 1)) && isLowSurrogate(text.charCodeAt(end))) {
    end += 1;
  }
  return { start, end };
}

// `text` as alternating plain and highlighted pieces, in order, with no empty piece. Putting the
// pieces' text back together always gives `text`.
export function highlightSegments(text: string, spans: readonly TextSpan[]): Segment[] {
  if (text === "") return [];

  const usable = spans
    .map((span) => fitted(text, span))
    .filter((span): span is TextSpan => span !== null)
    .sort((a, b) => a.start - b.start || a.end - b.end);

  // Overlapping and adjacent spans become one.
  const merged: TextSpan[] = [];
  for (const span of usable) {
    const last = merged[merged.length - 1];
    if (last !== undefined && span.start <= last.end) {
      last.end = Math.max(last.end, span.end);
    } else {
      merged.push({ ...span });
    }
  }

  const segments: Segment[] = [];
  let cursor = 0;
  for (const span of merged) {
    if (span.start > cursor) segments.push({ text: text.slice(cursor, span.start), highlighted: false });
    segments.push({ text: text.slice(span.start, span.end), highlighted: true });
    cursor = span.end;
  }
  if (cursor < text.length) segments.push({ text: text.slice(cursor), highlighted: false });
  return segments;
}
