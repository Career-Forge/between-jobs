# Third-party fonts

The web app serves three typefaces from its own origin. All three are licensed under the SIL Open Font
License 1.1; each family's license text is kept beside its files (`src/fonts/<family>/OFL.txt`). No page
requests a font from another host: the Content-Security-Policy allows `font-src 'self'` only, and
`src/fonts.css` is the one place that names the files.

The deployed site carries the notice too: `public/licenses/fonts.txt` holds the three license texts and is
served at `/licenses/fonts.txt`, linked from the public footer. It is each `OFL.txt` copied verbatim, and
`src/fonts.test.ts` fails when one of them changes and the served copy does not.

| Family | Role | Copyright | Reserved Font Name |
| --- | --- | --- | --- |
| Newsreader | Display serif: job identity, headings, the next-step sentence | The Newsreader Project Authors (Production Type) | none |
| IBM Plex Sans | Body text, controls, labels | IBM Corp. | "Plex" |
| IBM Plex Mono | Provenance: timestamps, ids, counts | IBM Corp. | "Plex" |

## What was and was not changed

The OFL lets a font be bundled and served with an application. IBM Plex reserves its name, so the Plex
files are the upstream builds exactly as IBM published them, under their original names: nothing was
re-subsetted, converted, renamed or edited. Newsreader declares no Reserved Font Name, so its files may be
(and are) the Latin and Latin Extended subsets that Fontsource cuts from the Google Fonts build, which keep
the name Newsreader. To check any file, compare its SHA-256 with the table below (`src/fonts.test.ts` does
this on every test run) or with the same file in the source package.

Each family ships a Latin file and a Latin Extended file; a browser downloads the second only when a page
contains a character from it (accented names on resumes, company names).

### IBM Plex Sans 400, 500, 600

Source: the `@ibm/plex-sans` npm package, version 1.1.0 (`fonts/split/woff2/`), from
https://github.com/IBM/plex. The package's `postinstall` runs IBM's telemetry client, so the files were
copied from the published tarball instead of adding the package to `package.json`.

| File | SHA-256 |
| --- | --- |
| `IBMPlexSans-Medium-Latin1.woff2` | `b5610af04d0d4b5a14a621d96d974b993e945a065db1a8861918f69ef9321934` |
| `IBMPlexSans-Medium-Latin2.woff2` | `46fbe4f398e7ff3cc7773a1156b1efef0f81e4d034f682369c221de587f443ca` |
| `IBMPlexSans-Regular-Latin1.woff2` | `b5ad7bd39f996144915f0ad9849a90183b27d8c28ad97ed98af5b1bebc51f6b1` |
| `IBMPlexSans-Regular-Latin2.woff2` | `fcfa82e0d46ed01a8df38610b451860ca7e7518770c52fdd2a1d136247c40f28` |
| `IBMPlexSans-SemiBold-Latin1.woff2` | `fff0ab3a88b0b4aa0b693e4f0201359a15183b08e3fa5696d1918d8f0ade8ad5` |
| `IBMPlexSans-SemiBold-Latin2.woff2` | `2bc85a8e53540c4c211f7af36e806bb045ba7da8946bb221d87144c12c83d577` |

### IBM Plex Mono 400, 500

Source: the `@ibm/plex-mono` npm package, version 2.5.0 (`fonts/split/woff2/`), from
https://github.com/IBM/plex.

| File | SHA-256 |
| --- | --- |
| `IBMPlexMono-Medium-Latin1.woff2` | `41201b658a328b9d00368215c2f1102770f80b15952ab82631e4006255e6365d` |
| `IBMPlexMono-Medium-Latin2.woff2` | `86208dbe8e00256a77bc3edd09aa6a584b0da393caa1fb200d44eac1d578512c` |
| `IBMPlexMono-Regular-Latin1.woff2` | `e8993d946649b9d01abb1ed06d574b19d8ea3e66b5c3948602db335c44c18e56` |
| `IBMPlexMono-Regular-Latin2.woff2` | `8c5e48e4c6ee60346744db62fd8bbf530c21e531e1a1498c3a410ed8f7d3986e` |

### Newsreader, roman and italic

Source: the `@fontsource-variable/newsreader` npm package, version 5.3.0 (`files/`), which repackages the
Google Fonts build of Newsreader (https://github.com/productiontype/Newsreader). Each file is a variable
font with weight 200-800 and an optical-size axis from 6 to 72, so the browser picks a sturdier or finer
cut from the font size and one file serves every weight. Newsreader declares no Reserved Font Name.

| File | SHA-256 |
| --- | --- |
| `newsreader-latin-ext-opsz-italic.woff2` | `bb24f6648f75d909eb74535916015c97ef72a1344e532ec7bbaf3f80eb6f389f` |
| `newsreader-latin-ext-opsz-normal.woff2` | `45683de03de37187604102316c0b42c0cb2d8dc9c4140a20ad471c3148cc1278` |
| `newsreader-latin-opsz-italic.woff2` | `5dfcd10d24af8c82927ba57f7983dd997f7b200594c09c35a1b1ed9fc4597506` |
| `newsreader-latin-opsz-normal.woff2` | `6e4f2958c3a7c4a80acde4e5a679abe7e01bc1e30b92be3c7a8b696ef401d101` |

## Updating a font

Download the new release from the source above, replace the files and the `OFL.txt` beside them (and the matching text in
`public/licenses/fonts.txt`), update the table, and run `npm test`. A file that has been changed by hand will not match its recorded hash.
