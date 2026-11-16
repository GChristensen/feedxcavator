// Builds the landing page into the GitHub Pages entry point (the repo-root index.html).
//
//   node build.mjs          aggressive build: one minified index.html with the CSS, the JS and the small
//                           svg icons inlined. Other local images (the png logo) are copied to media/landing/.
//   node build.mjs --plain  no build to speak of, no npm packages needed: a readable index.html whose
//                           assets are still the files in landing/, only the paths are re-based.
//   --out <dir>             write into <dir> instead of the repo root (to preview without touching the site)
//
// Run `npm install` here once for the aggressive build (html-minifier-terser + svgo).
//
// The root index.html has to be *replaced* either way: GitHub Pages serves /index.html and the current one
// is a Jekyll page (front matter + layout). Neither output has front matter, so Jekyll copies it verbatim.

import { readFile, writeFile, mkdir, rm, cp } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import path from "node:path";

const here = path.dirname(fileURLToPath(import.meta.url));
const args = process.argv.slice(2);
const plain = args.includes("--plain");
const outIdx = args.indexOf("--out");
if (outIdx >= 0 && !args[outIdx + 1]) throw new Error("--out needs a directory");
const outDir = outIdx >= 0 ? path.resolve(args[outIdx + 1]) : path.resolve(here, "..");

const INLINE_LIMIT = 4096;            // inline svg icons up to this many bytes (after minification) as data: URIs
const ASSET_DIR = "media/landing";     // where the remaining assets go, relative to the output root

// relative paths only: absolute URLs, root-absolute paths (/images/...) and #anchors are left alone
const isLocal = (url) => !/^([a-z][a-z0-9+.-]*:|\/|#)/i.test(url);
const bytes = (s) => Buffer.byteLength(s, "utf8");
const read = (rel) => readFile(path.join(here, rel), "utf8");

// Rewrites every local src/href/data attribute. fn(url, attr) returns the new value.
const mapUrls = (html, fn) =>
  html.replace(/(\s(src|href|data)=")([^"]+)(")/g,
    (m, pre, attr, url, post) => isLocal(url) ? pre + fn(url, attr) + post : m);

let html = await read("index.html");
let before = bytes(html);
let after;
const log = [];

if (plain) {
  // ---------------------------------------------------------------- plain
  html = mapUrls(html, (url) => "landing/" + url);
  after = bytes(html);
  await mkdir(outDir, { recursive: true });
  await writeFile(path.join(outDir, "index.html"), html, "utf8");
  // a custom --out has no landing/ next to it: bring the assets along
  if (path.resolve(outDir, "landing") !== here) {
    for (const f of ["styles.css", "main.js", "img"])
      await cp(path.join(here, f), path.join(outDir, "landing", f), { recursive: true });
    log.push(`  + landing/{styles.css,main.js,img/} copied to ${path.join(outDir, "landing")}`);
  }
} else {
  // ---------------------------------------------------------------- aggressive
  const { minify } = await import("html-minifier-terser");
  const { optimize } = await import("svgo");

  // 1. inline local stylesheets
  const linkRe = /<link\s+rel="stylesheet"\s+href="([^"]+)"\s*\/?>/g;
  const styles = new Map();
  for (const [, url] of html.matchAll(linkRe)) {
    if (!isLocal(url) || styles.has(url)) continue;
    const css = await read(url);
    if (/url\(\s*['"]?(?![a-z]+:|#)/i.test(css))
      throw new Error(`${url}: relative url() references are not supported by the inliner`);
    styles.set(url, css);
    before += bytes(css);
    log.push(`  + ${url}`);
  }
  html = html.replace(linkRe, (tag, url) => styles.has(url) ? `<style>${styles.get(url)}</style>` : tag);

  // 2. inline local scripts; a deferred script keeps its meaning by moving to the end of <body>
  const scriptRe = /<script\b([^>]*?)\ssrc="([^"]+)"([^>]*)><\/script>\s*/g;
  const deferred = [];
  const scripts = new Map();
  for (const [, , url] of html.matchAll(scriptRe)) {
    if (!isLocal(url) || scripts.has(url)) continue;
    const js = await read(url);
    scripts.set(url, js);
    before += bytes(js);
    log.push(`  + ${url}`);
  }
  html = html.replace(scriptRe, (tag, pre, url, post) => {
    if (!scripts.has(url)) return tag;
    const tagOut = `<script>${scripts.get(url).replace(/<\/script/gi, "<\\/script")}</script>`;
    if (/\bdefer\b/.test(pre + post)) { deferred.push(tagOut); return ""; }
    return tagOut;
  });
  if (deferred.length) html = html.replace("</body>", deferred.join("") + "</body>");

  // 3. images: minify the svgs and inline the small ones that are used as images; other formats (png)
  // are copied as they are. Everything that is not inlined is written to ASSET_DIR.
  // svgo's structural plugins don't understand animation: removeHiddenElems deletes elements that are
  // opacity:0 until their keyframes bring them in, and group/shape rewrites drop the class names the
  // keyframes are attached to. So an animated SVG -- CSS keyframes or SMIL -- gets only cosmetic plugins
  // that never restructure elements. Static SVGs get the full default preset.
  const CAREFUL = ["removeDoctype", "removeXMLProcInst", "removeComments", "removeMetadata",
    "removeEditorsNSData", "cleanupAttrs", "removeUnusedNS", "cleanupNumericValues", "convertColors",
    "sortAttrs", "removeEmptyAttrs"];
  const svgOpts = (src) => ({
    multipass: true,
    plugins: /<(animate|set)\b|@keyframes/.test(src)
      ? CAREFUL
      : [{ name: "preset-default", params: { overrides: { removeViewBox: false } } }],
  });
  const assets = new Map();                 // url -> { data: minified svg text | file Buffer, svg: bool }
  for (const [, , url] of html.matchAll(/(\s(?:src|href|data)=")([^"]+)"/g)) {
    if (!isLocal(url) || assets.has(url)) continue;
    if (/\.svg$/i.test(url)) {
      const src = await read(url);
      before += bytes(src);
      assets.set(url, { svg: true, data: optimize(src, { path: url, ...svgOpts(src) }).data });
    } else {
      const file = await readFile(path.join(here, url));
      before += file.length;
      assets.set(url, { svg: false, data: file });
    }
  }
  const written = new Set();
  html = mapUrls(html, (url, attr) => {
    const asset = assets.get(url);
    if (asset === undefined) throw new Error(`unhandled local asset: ${url}`);
    if (asset.svg && attr !== "data" && bytes(asset.data) <= INLINE_LIMIT)
      return "data:image/svg+xml," + encodeURIComponent(asset.data).replace(/%20/g, " ").replace(/%3A/g, ":")
        .replace(/%2F/g, "/").replace(/%3D/g, "=").replace(/%2C/g, ",").replace(/%3B/g, ";");
    written.add(url);
    return `${ASSET_DIR}/${path.basename(url)}`;
  });

  // 4. minify everything else
  html = await minify(html, {
    collapseWhitespace: true,
    conservativeCollapse: false,
    collapseBooleanAttributes: true,
    removeComments: true,
    removeRedundantAttributes: true,
    removeScriptTypeAttributes: true,
    removeStyleLinkTypeAttributes: true,
    removeEmptyAttributes: true,
    removeAttributeQuotes: true,
    sortAttributes: true,
    sortClassName: true,
    useShortDoctype: true,
    minifyCSS: { level: 2 },
    minifyJS: { compress: { passes: 2 }, mangle: { toplevel: true } },
  });
  after = bytes(html);

  // 5. write
  await mkdir(outDir, { recursive: true });
  await writeFile(path.join(outDir, "index.html"), html, "utf8");
  const assetOut = path.join(outDir, ASSET_DIR);
  await rm(assetOut, { recursive: true, force: true });   // generated directory: drop stale files
  await mkdir(assetOut, { recursive: true });
  for (const url of written) {
    const { data } = assets.get(url);
    await writeFile(path.join(assetOut, path.basename(url)), data, data instanceof Buffer ? undefined : "utf8");
    const size = data instanceof Buffer ? data.length : bytes(data);
    after += size;
    log.push(`  + ${ASSET_DIR}/${path.basename(url)}  ${size} bytes`);
  }
  log.push(`  ${[...assets.values()].filter((a) => a.svg).length - [...written].filter((u) => assets.get(u).svg).length} small svgs inlined as data: URIs`);
}

console.log(`landing (${plain ? "plain" : "minified"}): ${path.join(here, "index.html")}`);
for (const l of log) console.log(l);
console.log(`  -> ${path.join(outDir, "index.html")}`);
console.log(`  ${before} bytes -> ${after} bytes (${(100 * after / before).toFixed(1)}%)`);
