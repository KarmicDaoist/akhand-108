// Renders public/og.png (1200x630) with the private Playwright Chromium. Run once: node tools/og.mjs
import { chromium } from "playwright";

const colors = ["#7cc4ff", "#a8dcff", "#d2ecff", "#ff4b3a", "#ff2e63", "#ff8a1f", "#37d67a", "#ff6a13", "#ffd23f", "#ff7ac3", "#ffb300", "#b388ff"];
let beads = "", petals = "";
const pol = (r, deg) => { const a = (deg - 90) * Math.PI / 180; return [r * Math.cos(a), r * Math.sin(a)]; };
for (let i = 0; i < 108; i++) {
  const g = Math.floor(i / 9), w = i % 9, [x, y] = pol(236, g * 30 + 3 + w * 3);
  beads += `<circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="7" fill="${colors[g]}"/><circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="17" fill="${colors[g]}" opacity="0.22"/>`;
}
for (let s = 0; s < 12; s++) {
  const m = s * 30 + 15, p = (r, d) => pol(r, d).map((v) => v.toFixed(1)).join(" ");
  petals += `<path d="M ${p(82, m - 14)} Q ${p(170, m - 20)} ${p(216, m)} Q ${p(170, m + 20)} ${p(82, m + 14)} Z" fill="${colors[s]}" fill-opacity="0.3" stroke="${colors[s]}" stroke-width="1.5"/>`;
}
const html = `<!doctype html><meta charset="utf-8"><link href="https://fonts.googleapis.com/css2?family=Cormorant+Garamond:ital,wght@0,600;1,500&family=Tiro+Devanagari+Sanskrit&display=swap" rel="stylesheet">
<body style="margin:0;width:1200px;height:630px;overflow:hidden">
<svg width="1200" height="630" viewBox="0 0 1200 630">
<defs><radialGradient id="bg" gradientUnits="userSpaceOnUse" cx="0" cy="0" r="1" gradientTransform="translate(880 300) scale(900 700)">
<stop offset="0" stop-color="#7a2410"/><stop offset="0.38" stop-color="#3d0f17"/><stop offset="0.75" stop-color="#1a0716"/><stop offset="1" stop-color="#0c0410"/></radialGradient>
<radialGradient id="aura"><stop offset="0" stop-color="#ffd27a" stop-opacity="0.9"/><stop offset="0.35" stop-color="#ff9a2e" stop-opacity="0.38"/><stop offset="1" stop-color="#ff9a2e" stop-opacity="0"/></radialGradient>
<radialGradient id="core"><stop offset="0" stop-color="#fff3c9"/><stop offset="0.6" stop-color="#ffc24a"/><stop offset="1" stop-color="#e8782a"/></radialGradient></defs>
<rect width="1200" height="630" fill="url(#bg)"/>
<g transform="translate(880 315)">
<circle r="330" fill="url(#aura)" opacity="0.55"/>
<circle r="262" fill="none" stroke="#ffd27a" stroke-opacity="0.5" stroke-width="1.5"/>
${petals}
<circle r="82" fill="#2a0a10" stroke="#ffd27a" stroke-width="2.5"/>
<text y="30" text-anchor="middle" font-family="Tiro Devanagari Sanskrit, serif" font-size="100" fill="url(#core)">ॐ</text>
${beads}
</g>
<text x="70" y="262" font-family="Cormorant Garamond, Georgia, serif" font-weight="600" font-size="96" letter-spacing="14" fill="#ffd27a">AKHAND</text>
<text x="70" y="352" font-family="Cormorant Garamond, Georgia, serif" font-weight="600" font-size="96" fill="#fff6e3">108</text>
<text x="70" y="418" font-family="Cormorant Garamond, Georgia, serif" font-style="italic" font-size="44" fill="#fff1d6">108 pandit agents praying 24/7</text>
<text x="70" y="468" font-family="Cormorant Garamond, Georgia, serif" font-style="italic" font-size="44" fill="#fff1d6">for the moksha of Mehul</text>
<text x="70" y="540" font-family="Cormorant Garamond, Georgia, serif" font-weight="600" font-size="24" letter-spacing="6" fill="#ffb86b">LIVE, WATCH ONLY</text>
</svg>`;

const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage({ viewport: { width: 1200, height: 630 } });
  await page.setContent(html, { waitUntil: "networkidle" });
  await page.evaluate(() => document.fonts.ready);
  await page.screenshot({ path: new URL("../public/og.png", import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, "$1") });
  console.log("wrote public/og.png");
} finally {
  await browser.close();
}
