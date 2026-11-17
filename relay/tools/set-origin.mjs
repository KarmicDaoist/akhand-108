// Makes og:image / twitter:image absolute. Usage: node tools/set-origin.mjs https://akhand-108.example.workers.dev
import { readFileSync, writeFileSync } from "node:fs";

const origin = (process.argv[2] || "").replace(/\/+$/, "");
if (!/^https:\/\/[^/\s]+$/.test(origin)) {
  console.error("Usage: node tools/set-origin.mjs https://<your-worker-host>");
  process.exit(1);
}
const file = new URL("../public/index.html", import.meta.url);
let html = readFileSync(file, "utf8");
html = html.replace(/(property="og:image" content=")[^"]*"/, `$1${origin}/og.png"`);
html = html.replace(/(name="twitter:image" content=")[^"]*"/, `$1${origin}/og.png"`);
if (!html.includes('property="og:url"')) {
  html = html.replace('<meta property="og:site_name"', `<meta property="og:url" content="${origin}/">\n<meta property="og:site_name"`);
} else {
  html = html.replace(/(property="og:url" content=")[^"]*"/, `$1${origin}/"`);
}
writeFileSync(file, html);
console.log("updated", origin);
