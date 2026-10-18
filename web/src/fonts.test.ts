import { describe, expect, it } from "vitest";
import fontsCss from "./fonts.css?raw";
import tokensCss from "./tokens.css?raw";
import notices from "../THIRD_PARTY_FONTS.md?raw";
import shippedLicenses from "../public/licenses/fonts.txt?raw";

// The typefaces are self-hosted and unmodified (IBM Plex reserves its name, so a changed file would
// no longer be allowed to carry it). These read the shipped files as base64, which is the only way
// to get at their bytes here: @types/node is not installed.

const files = import.meta.glob("./fonts/**/*.woff2", { query: "?inline", import: "default", eager: true }) as Record<
  string,
  string
>;
const licenses = import.meta.glob("./fonts/*/OFL.txt", { query: "?raw", import: "default", eager: true }) as Record<
  string,
  string
>;

async function sha256(dataUri: string): Promise<string> {
  const bytes = Uint8Array.from(atob(dataUri.slice(dataUri.indexOf(",") + 1)), (c) => c.charCodeAt(0));
  const digest = new Uint8Array(await crypto.subtle.digest("SHA-256", bytes));
  return [...digest].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

const referenced = [...fontsCss.matchAll(/url\("\.\/(fonts\/[^"]+)"\)/g)].map((match) => match[1]);

describe("fonts.css", () => {
  it("names only files that exist, and every font file that exists is named", () => {
    const onDisk = Object.keys(files).map((path) => path.replace("./", ""));
    expect([...referenced].sort()).toEqual([...onDisk].sort());
    expect(referenced.length).toBe(new Set(referenced).size);
  });

  it("loads nothing from another origin and inlines nothing: the policy allows font-src 'self' only", () => {
    expect(fontsCss).not.toMatch(/https?:|data:|\/\/fonts\./);
    expect(tokensCss).not.toMatch(/@import|https?:/);
  });

  it("swaps: text shows in the fallback face while a file loads", () => {
    const faces = fontsCss.match(/@font-face/g)?.length ?? 0;
    expect(faces).toBe(referenced.length);
    expect(fontsCss.match(/font-display: swap;/g)?.length).toBe(faces);
  });

  it("ships only the weights the design uses", () => {
    const weights = (family: string): string[] =>
      [...fontsCss.matchAll(new RegExp(`font-family: "${family}";\\s+font-style: (\\w+);\\s+font-weight: ([^;]+);`, "g"))]
        .map((match) => `${match[1]} ${match[2]}`)
        .filter((value, index, all) => all.indexOf(value) === index)
        .sort();
    expect(weights("IBM Plex Sans")).toEqual(["normal 400", "normal 500", "normal 600"]);
    expect(weights("IBM Plex Mono")).toEqual(["normal 400", "normal 500"]);
    expect(weights("Newsreader")).toEqual(["italic 200 800", "normal 200 800"]);
  });

  it("is what the three font tokens name first", () => {
    for (const [token, family] of [
      ["--bj-font-display", "Newsreader"],
      ["--bj-font-body", "IBM Plex Sans"],
      ["--bj-font-mono", "IBM Plex Mono"],
    ]) {
      expect(tokensCss).toMatch(new RegExp(`${token}:\\s*"${family}",`));
      expect(fontsCss).toContain(`font-family: "${family}";`);
    }
  });
});

describe("the font files", () => {
  it("match the SHA-256 recorded in THIRD_PARTY_FONTS.md, so none has been edited", async () => {
    const recorded = new Map(
      [...notices.matchAll(/\| `([^`]+\.woff2)` \| `([0-9a-f]{64})` \|/g)].map((match) => [match[1], match[2]]),
    );
    expect(recorded.size).toBe(Object.keys(files).length);
    for (const [path, dataUri] of Object.entries(files)) {
      const name = path.slice(path.lastIndexOf("/") + 1);
      expect(await sha256(dataUri), name).toBe(recorded.get(name));
    }
  });

  it("each family keeps its license beside its files", () => {
    const families = new Set(Object.keys(files).map((path) => path.split("/")[2]));
    expect([...families].sort()).toEqual(["ibm-plex-mono", "ibm-plex-sans", "newsreader"]);
    for (const family of families) {
      const text = licenses[`./fonts/${family}/OFL.txt`];
      expect(text, family).toContain("SIL OPEN FONT LICENSE Version 1.1");
    }
    expect(licenses["./fonts/ibm-plex-sans/OFL.txt"]).toContain('Reserved Font Name "Plex"');
  });

  it("ships every family's full license text at /licenses/fonts.txt, so the deployed site carries the notice", () => {
    // public/ is copied into the build as it is, so this file is what a visitor or a mirror receives.
    // It holds each OFL.txt verbatim, so a license updated beside the fonts fails here until the
    // shipped copy is regenerated.
    // (Two of the upstream files use CRLF line endings; the shipped copy uses LF throughout.)
    for (const [path, text] of Object.entries(licenses)) {
      expect(shippedLicenses, path).toContain(text.replaceAll("\r\n", "\n").trim());
    }
    for (const holder of ["The Newsreader Project Authors", "IBM Corp."]) expect(shippedLicenses).toContain(holder);
  });
});
