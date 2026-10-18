// Regenerates the brand files in web/public/ from one description of the mark:
//
//   favicon.svg          the icon, with rounded corners
//   favicon.ico          16, 32 and 48px, PNG-compressed entries
//   favicon-32x32.png    the same rounded icon, for browsers that ignore the SVG
//   apple-touch-icon.png 180px, square and opaque (iOS rounds it itself)
//   icon-192.png         192px and 512px, square and opaque; the mark sits inside the central 80%
//   icon-512.png         that a maskable icon is guaranteed to keep, so any launcher mask is safe
//   og-image.png         1200x630 link preview: the lockup in Newsreader, the promise in Plex Mono
//
// Run from web/:   node scripts/generateBrandAssets.mjs
//
// The rasters are drawn by headless Chrome, so the text in og-image.png is the real Newsreader and
// IBM Plex Mono from src/fonts/. Chrome is found at its usual macOS, Linux and Windows locations, or at
// the path in CHROME_BIN. Nothing is installed and no package is added to the web app; the script uses
// only Node's own modules. The colors below mirror tokens.css (--bj-bg, --bj-text, --bj-border,
// --bj-brand-dot); change both together. Output is stable for a given Chrome version, not byte-for-byte
// across versions, so a regenerated file may differ slightly in its compressed bytes.

import { execFileSync } from "node:child_process";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const webRoot = join(here, "..");
const publicDir = join(webRoot, "public");
const fontsDir = join(webRoot, "src", "fonts");

const COLORS = { ground: "#070607", ivory: "#f2eadf", muted: "#a99da0", rule: "#34252b", dot: "#e0414f" };

// The mark on a 64 x 64 grid: two ivory bars with a red dot between them. Everything sits inside
// the box from 14 to 50, which is within the 40%-radius circle a maskable icon may not be cut into.
function markShapes() {
  return [
    `<rect x="14" y="14" width="6" height="36" rx="3" fill="${COLORS.ivory}"/>`,
    `<rect x="44" y="14" width="6" height="36" rx="3" fill="${COLORS.ivory}"/>`,
    `<circle cx="32" cy="32" r="6" fill="${COLORS.dot}"/>`,
  ].join("");
}

function iconSvg({ rounded }) {
  const ground = rounded
    ? `<rect x="0.5" y="0.5" width="63" height="63" rx="13.5" fill="${COLORS.ground}" stroke="${COLORS.rule}"/>`
    : `<rect width="64" height="64" fill="${COLORS.ground}"/>`;
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">${ground}${markShapes()}</svg>\n`;
}

function chromePath() {
  const candidates = [
    process.env.CHROME_BIN,
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/usr/bin/google-chrome",
    "/usr/bin/google-chrome-stable",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
    "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
  ];
  const found = candidates.find((path) => path && existsSync(path));
  if (!found) throw new Error("Chrome was not found. Install it, or set CHROME_BIN to the executable.");
  return found;
}

function screenshot(chrome, scratch, html, width, height, outFile, { transparent }) {
  const page = join(scratch, `${width}x${height}-${Math.random().toString(36).slice(2)}.html`);
  writeFileSync(page, html);
  execFileSync(
    chrome,
    [
      "--headless=new",
      "--disable-gpu",
      "--hide-scrollbars",
      "--force-device-scale-factor=1",
      `--default-background-color=${transparent ? "00000000" : "ff070607"}`,
      // A profile of its own, so a Chrome that is already open does not take over the request.
      `--user-data-dir=${join(scratch, "profile")}`,
      "--virtual-time-budget=4000",
      `--window-size=${width},${height}`,
      `--screenshot=${outFile}`,
      pathToFileURL(page).href,
    ],
    { stdio: "ignore" },
  );
  const bytes = readFileSync(outFile);
  const [w, h] = [bytes.readUInt32BE(16), bytes.readUInt32BE(20)];
  if (w !== width || h !== height) throw new Error(`${outFile} came out ${w}x${h}, expected ${width}x${height}`);
}

function iconPage(size, rounded) {
  const svg = iconSvg({ rounded }).replace("<svg ", `<svg width="${size}" height="${size}" `);
  return `<!doctype html><meta charset="utf-8"><style>html,body{margin:0;background:transparent}svg{display:block}</style>${svg}`;
}

// A .ico is a small directory followed by the images; every entry here is a PNG, which Windows
// (Vista and later) and every browser read.
function packIco(entries) {
  const header = Buffer.alloc(6);
  header.writeUInt16LE(1, 2);
  header.writeUInt16LE(entries.length, 4);
  let offset = 6 + 16 * entries.length;
  const directory = entries.map(({ size, png }) => {
    const entry = Buffer.alloc(16);
    entry.writeUInt8(size, 0);
    entry.writeUInt8(size, 1);
    entry.writeUInt16LE(1, 4);
    entry.writeUInt16LE(32, 6);
    entry.writeUInt32LE(png.length, 8);
    entry.writeUInt32LE(offset, 12);
    offset += png.length;
    return entry;
  });
  return Buffer.concat([header, ...directory, ...entries.map(({ png }) => png)]);
}

function ogPage() {
  // Inlined into this throwaway page, because Chrome will not load a font from one file:// page to another.
  const face = (family, file, weight) => {
    const data = readFileSync(join(fontsDir, file)).toString("base64");
    return `@font-face{font-family:"${family}";font-weight:${weight};src:url("data:font/woff2;base64,${data}") format("woff2")}`;
  };
  return `<!doctype html><meta charset="utf-8"><style>
${face("Newsreader", "newsreader/newsreader-latin-opsz-normal.woff2", "200 800")}
${face("IBM Plex Mono", "ibm-plex-mono/IBMPlexMono-Regular-Latin1.woff2", 400)}
html,body{margin:0;width:1200px;height:630px;background:${COLORS.ground};overflow:hidden}
.card{box-sizing:border-box;width:1200px;height:630px;padding:0 96px;display:flex;flex-direction:column;justify-content:center}
.lockup{display:inline-flex;align-items:flex-start;gap:.11em;align-self:flex-start;font:500 132px/1 "Newsreader",Georgia,serif;color:${COLORS.ivory};letter-spacing:-.01em}
.dot{width:.2em;height:.2em;border-radius:50%;background:${COLORS.dot};margin-top:.05em}
.rule{width:96px;height:1px;background:${COLORS.rule};margin:56px 0 40px}
.promise{font:400 34px/1.3 "IBM Plex Mono",ui-monospace,monospace;color:${COLORS.ivory}}
.site{position:absolute;left:96px;bottom:56px;font:400 22px/1 "IBM Plex Mono",ui-monospace,monospace;color:${COLORS.muted}}
</style><div class="card"><div class="lockup">Between Jobs<span class="dot"></span></div><div class="rule"></div><div class="promise">Draft, don\u2019t send. Fill, don\u2019t submit.</div></div><div class="site">between-jobs.tech</div>`;
}

function main() {
  const chrome = chromePath();
  const scratch = mkdtempSync(join(tmpdir(), "between-jobs-brand-"));
  mkdirSync(publicDir, { recursive: true });
  try {
    writeFileSync(join(publicDir, "favicon.svg"), iconSvg({ rounded: true }));

    const draw = (name, size, rounded) =>
      screenshot(chrome, scratch, iconPage(size, rounded), size, size, join(publicDir, name), { transparent: rounded });
    draw("favicon-32x32.png", 32, true);
    draw("apple-touch-icon.png", 180, false);
    draw("icon-192.png", 192, false);
    draw("icon-512.png", 512, false);

    const icoEntries = [16, 32, 48].map((size) => {
      const file = join(scratch, `ico-${size}.png`);
      screenshot(chrome, scratch, iconPage(size, true), size, size, file, { transparent: true });
      return { size, png: readFileSync(file) };
    });
    writeFileSync(join(publicDir, "favicon.ico"), packIco(icoEntries));

    screenshot(chrome, scratch, ogPage(), 1200, 630, join(publicDir, "og-image.png"), { transparent: false });
    console.log(`Wrote the brand files to ${publicDir}`);
  } finally {
    rmSync(scratch, { recursive: true, force: true });
  }
}

main();
