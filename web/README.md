# Between Jobs web app

React and TypeScript on Vite, a thin client over the API. It is built to static files and can be
served from any static host; `web/.env.example` lists the three variables it reads.

```bash
npm ci
cp .env.example .env.local     # VITE_SUPABASE_URL and VITE_SUPABASE_PUBLISHABLE_KEY
npm run dev                    # http://localhost:5173, /api is proxied to the API on :8012
npm run typecheck
npm test
VITE_API_BASE_URL=https://api.example.com VITE_SUPABASE_URL=https://<project>.supabase.co npm run build
```

A production build needs `VITE_API_BASE_URL` and `VITE_SUPABASE_URL`, both `https://` origins that
are not local addresses, and refuses to run without them (`scripts/apiBaseGuard.ts`,
`scripts/securityHeaders.ts`). Pass them in the shell rather than in `.env.local`, so a leftover
local file cannot leak into a bundle you meant to deploy. A change to either needs a new build.

## Fonts and brand files

The typefaces are Newsreader (display), IBM Plex Sans (body) and IBM Plex Mono (provenance), served from
the app's own origin and never from a font CDN. `src/fonts.css` is the only place that names the files,
which live in `src/fonts/` with their license texts; `THIRD_PARTY_FONTS.md` says where each came from and
records a SHA-256 for every file, which `npm test` checks.

The favicon, touch icons, web app manifest and the link-preview image are in `public/` and are linked from
`index.html`. The PNG and ICO files are generated, not drawn by hand; to change the mark or the preview
image, edit `scripts/generateBrandAssets.mjs` and run:

```bash
node scripts/generateBrandAssets.mjs     # needs Chrome; set CHROME_BIN if it is not in the usual place
```

It uses Node's own modules only and draws through headless Chrome, so the preview image carries the real
typefaces. `src/brandAssets.test.ts` checks the sizes of the files it writes.

## Security headers

`npm run build` also writes `dist/_headers`: a Content-Security-Policy and a few other response
headers, in the file format Cloudflare Pages (and Workers static assets) applies to the site it serves
from `dist`. Nothing needs configuring. The policy lets the browser contact exactly the API and
Supabase hosts the bundle was built against, so building for your own hosts gives you a policy for
your own hosts.

The build prints the whole file in its log (`vite build --logLevel warn` silences that), which is the
easiest way to copy it from a CI run. The file is plain text, one `Name: value` per line.

### What is in it

| Header | Value, and why |
| --- | --- |
| `Content-Security-Policy` | See the next table. |
| `X-Content-Type-Options` | `nosniff`. |
| `Referrer-Policy` | `strict-origin-when-cross-origin`. |
| `Permissions-Policy` | Camera, microphone, geolocation, payment, USB, serial, HID, Bluetooth, MIDI, motion and orientation sensors, screen capture, XR and clipboard read switched off. The app uses none of them, and only writes to the clipboard. |
| `X-Frame-Options` | `DENY`, the older twin of `frame-ancestors 'none'`. |
| `Cache-Control` | `no-cache` for the HTML, so a new deploy is picked up; `public, max-age=31536000, immutable` for `/assets/*`, whose file names carry a content hash. |

Not set here, on purpose: `Strict-Transport-Security` is a one-way commitment (browsers refuse plain
http for the whole domain, for as long as you say), so turn it on yourself, in your host's dashboard,
once https works everywhere on the domain. `Cross-Origin-Opener-Policy` and
`Cross-Origin-Embedder-Policy` can break cross-origin sign-in windows and embeds, and nothing here
needs them.

| Directive | Value | Why |
| --- | --- | --- |
| `default-src` | `'none'` | Anything not listed below is refused. |
| `script-src` | `'self'` | One same-origin bundle. No inline script, no `eval`, no `'unsafe-inline'`. |
| `style-src` | `'self'` and the SHA-256 of the inline `<style>` in `index.html` | Hashes are computed from the HTML the build emitted, so editing that block keeps working. React's `style={...}` props are applied through the CSSOM, which this directive does not govern, so no `'unsafe-inline'` and no `style-src-attr`. |
| `img-src`, `font-src` | `'self'` | The favicon and touch icons, and the three self-hosted typefaces (see "Fonts and brand files"). `build.assetsInlineLimit` is 0, so an image or font arrives as a file under `/assets/`, never as a `data:` URL. |
| `manifest-src` | `'self'` | The web app manifest, `site.webmanifest`. With `default-src 'none'` the browser would refuse to read it. |
| `connect-src` | the API origin and the Supabase origin | From `VITE_API_BASE_URL` and `VITE_SUPABASE_URL`: scheme, host and a non-default port, no path, no wildcard. Supabase is used for sign-in only, so no websocket origin is listed. The site's own origin is not listed either; nothing fetches it from script. |
| `frame-src` | `https://www.linkedin.com` | The click-to-load post embed in Hiring posts, the only frame the app ever creates. |
| `worker-src`, `object-src` | `'none'` | No workers, no plugins. |
| `base-uri`, `form-action` | `'self'` | |
| `frame-ancestors` | `'none'` | Nothing may frame the app. |
| `upgrade-insecure-requests` | | A stray `http://` subresource is fetched over https instead. |

Downloads of generated PDFs and exports are blobs saved through a link, which no CSP directive
governs, and Google sign-in is a top-level redirect through Supabase, which none governs either.

### How it is made

`scripts/securityHeaders.ts` builds the text, and a plugin in `vite.config.ts` emits it as part of the
bundle, in the `generateBundle` hook after Vite has written `index.html`. That is why it is a plugin
and not a step after `vite build`: it reads the same resolved environment Vite inlines into the
bundle (shell variables over `.env.local` over `.env`), so the policy cannot name different hosts than
the code calls; it follows `--outDir` and `--mode`; it hashes the HTML that was actually emitted; and
a build that fails writes nothing. Both addresses are checked before bundling starts, and a bad one
stops the build with a message that names the variable and never prints its value (the build log
is a CI log, and an address can carry a password or a token by mistake).

`--base` and `build.assetsDir` shape the cache rule, which is a path on the site's own host, so both
must be plain paths: a base such as `/` or `/app/`, and an assets directory such as `assets`. A base
on another origin (`https://cdn.example.com/` or `//cdn.example.com/`) is refused, because the policy
allows scripts and styles from the site's own origin only and would blank the page; a path with a
space, `:` or `*` in it, which `_headers` reads as something else, is refused too.

The generated file replaces any `web/public/_headers`. To add a rule, change the generator.

`vite dev` is untouched. `vite preview` serves the built files **without** applying `_headers`, so a
policy problem will not show up there; check the built output behind a server that applies the file
(a Cloudflare preview deployment, or Wrangler's local server for your project type).

### Other hosts

Cloudflare Pages reads `dist/_headers` as it is. Anywhere else, copy the policy out of that file (or
the build log) into your server's own syntax. Two things to carry over besides the values: unknown
paths must serve `index.html` (a single-page app), and the HTML must stay revalidated while
`/assets/*` is cached for a year. These sketches are untested; check them against your server's
documentation, in particular how it combines headers set at more than one level.

```nginx
location / {
    try_files $uri /index.html;
    add_header Content-Security-Policy "<the value from dist/_headers>" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header Referrer-Policy "strict-origin-when-cross-origin" always;
    add_header Permissions-Policy "<the value from dist/_headers>" always;
    add_header X-Frame-Options "DENY" always;
    add_header Cache-Control "no-cache" always;
}
location /assets/ {
    # add_header in a block replaces what the enclosing block set, so repeat every header here.
    add_header Cache-Control "public, max-age=31536000, immutable" always;
    # ... and the five security headers above ...
}
```

```caddy
example.com {
    root * /srv/between-jobs
    try_files {path} /index.html
    header {
        Content-Security-Policy "<the value from dist/_headers>"
        X-Content-Type-Options nosniff
        Referrer-Policy strict-origin-when-cross-origin
        Permissions-Policy "<the value from dist/_headers>"
        X-Frame-Options DENY
        Cache-Control no-cache
    }
    @assets path /assets/*
    header @assets Cache-Control "public, max-age=31536000, immutable"
    file_server
}
```

Netlify also reads a `_headers` file from the publish directory in a similar format; check its
documentation for how overlapping rules and the `! Cache-Control` removal line are treated before
relying on it.

### When something is blocked

Open the browser console on the page that misbehaves. A blocked request says which directive refused
it and what it was, for example:

```
Connecting to 'https://api.other.example/' violates the following Content Security Policy directive:
"connect-src https://api.example.com https://project.supabase.co". The action has been blocked.
```

- **A `connect-src` refusal for your own API or Supabase host.** The build was made with different
  values than the ones in use. Fix `VITE_API_BASE_URL` or `VITE_SUPABASE_URL` in the build environment
  and build again; the policy only changes when you rebuild. Compare the origin in the message with
  the one in the `connect-src` line of `dist/_headers`: a different port, `http` instead of `https`
  and a `www.` prefix each count as a different origin.
- **A `connect-src` refusal for a `wss://` Supabase address.** Something now uses Supabase Realtime.
  Add the `wss://` origin to `connect-src` in `scripts/securityHeaders.ts`, with a test.
- **A refusal to run an inline script, or to apply an inline style.** Something put markup into the
  page that is not in the build, most often a hosting feature that injects a script (some CDN
  analytics and optimisation features do). A `<style>` block in `index.html` is hashed
  automatically. An inline `style="..."` attribute or an event-handler attribute (`onclick`,
  `onload`, ...) cannot be hashed, so the build itself refuses a page that has one and names the
  attribute: move the style to the stylesheet and the handler to a script module. An inline
  `<script>` in `index.html` is hashed, but prefer a module.
- **A `frame-src` refusal.** A new embed needs its origin added to `frame-src` in the generator.
- **To try a change without enforcing it,** rename the header to
  `Content-Security-Policy-Report-Only` in the file you deploy: violations are logged to the console
  and nothing is blocked.

If the browser seems to ignore the policy, check that the response carries it:
`curl -sI https://your-site.example/ | grep -i content-security`. Cloudflare Pages ignores a
`_headers` line longer than 2,000 characters, so the generator refuses to write one.
