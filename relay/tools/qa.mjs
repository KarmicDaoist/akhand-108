// Local acceptance run against `wrangler dev` (port 8787) with token test-token-local-only.
// Starts its own feed and its own private headless Chromium, and stops both at the end.
import { spawn } from "node:child_process";
import { chromium } from "playwright";
import WebSocket from "ws";

const BASE = "http://127.0.0.1:8787";
const WSB = "ws://127.0.0.1:8787";
const OUT = new URL("../qa/", import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, "$1");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const results = [];
const ok = (name, pass, info = "") => {
  results.push({ name, pass, info });
  console.log(`${pass ? "PASS" : "FAIL"}  ${name}${info ? "  (" + info + ")" : ""}`);
};

let feed = null;
function startFeed(extra = []) {
  feed = spawn(process.execPath, ["tools/feed.mjs", ...extra], { stdio: ["pipe", "pipe", "inherit"] });
  feed.stdout.on("data", () => {});
  return feed;
}
const cmd = (c) => feed.stdin.write(c + "\n");
function stopFeed() {
  if (feed && !feed.killed) feed.kill();
  feed = null;
}

const sizes = [
  { name: "390x844", width: 390, height: 844, deviceScaleFactor: 2 },
  { name: "1440x900", width: 1440, height: 900, deviceScaleFactor: 1 },
];

let browser;
try {
  // --- ingest auth ---
  const bad = await new Promise((resolve) => {
    const w = new WebSocket(`${WSB}/ingest`, { headers: { Authorization: "Bearer wrong" } });
    w.on("unexpected-response", (_q, res) => resolve(res.statusCode));
    w.on("open", () => resolve("opened"));
    w.on("error", () => {});
  });
  ok("ingest with wrong token rejected", bad === 401, `HTTP ${bad}`);
  const none = await new Promise((resolve) => {
    const w = new WebSocket(`${WSB}/ingest`);
    w.on("unexpected-response", (_q, res) => resolve(res.statusCode));
    w.on("open", () => resolve("opened"));
    w.on("error", () => {});
  });
  ok("ingest with no token rejected", none === 401, `HTTP ${none}`);

  startFeed();
  await sleep(2500);

  browser = await chromium.launch({ headless: true });
  const pages = [];
  for (const sz of sizes) {
    const ctx = await browser.newContext({ viewport: { width: sz.width, height: sz.height }, deviceScaleFactor: sz.deviceScaleFactor });
    const page = await ctx.newPage();
    const errors = [];
    page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
    page.on("pageerror", (e) => errors.push(String(e)));
    await page.goto(BASE + "/", { waitUntil: "load" });
    pages.push({ sz, page, errors });
  }

  // viewer junk + counts: a raw viewer that sends garbage
  const viewer = new WebSocket(`${WSB}/live`);
  const got = [];
  viewer.on("message", (d) => got.push(String(d)));
  await new Promise((r) => viewer.on("open", r));
  viewer.send("garbage");
  viewer.send(JSON.stringify({ t: "state", mode: "resting" }));
  viewer.send(Buffer.from([1, 2, 3]));
  await sleep(4000);
  ok("junk-sending viewer stays connected and still receives", viewer.readyState === WebSocket.OPEN && got.some((m) => m.includes('"batch"')), `${got.length} frames`);
  ok("viewer junk not echoed to others", !got.some((m) => m.includes('"mode":"resting"')));

  await sleep(4000);
  for (const { sz, page } of pages) {
    const info = await page.evaluate(() => ({
      view: window.__akhand.view(),
      lit: window.__akhand.beads.filter((b) => b.lit > 0.3).length,
      txt: document.getElementById("nShloka").textContent,
      mala: document.getElementById("nMala").textContent,
      feed: document.getElementById("feedText").textContent,
      verse: document.getElementById("v1").textContent,
      day: document.getElementById("nDay").textContent,
      sank: document.getElementById("sankalpa").textContent,
    }));
    ok(`[${sz.name}] live view`, info.view === "live", JSON.stringify(info));
    ok(`[${sz.name}] beads lit from real events`, info.lit > 0, `${info.lit} lit`);
    const n1 = Number(info.txt.replace(/,/g, ""));
    await sleep(5000);
    const n2 = Number((await page.textContent("#nShloka")).replace(/,/g, ""));
    ok(`[${sz.name}] counter increases`, n2 > n1, `${n1} -> ${n2}`);
    await page.screenshot({ path: `${OUT}live-${sz.name}.png` });
  }

  // gathering
  cmd("gather");
  await sleep(7000);
  for (const { sz, page } of pages) {
    const v = await page.evaluate(() => window.__akhand.view());
    ok(`[${sz.name}] gathering view`, v === "gathering", v);
    await page.screenshot({ path: `${OUT}gathering-${sz.name}.png` });
  }
  cmd("praying");
  await sleep(6000);

  // malformed ingest is dropped, relay survives
  cmd("junk");
  await sleep(3000);
  const afterJunk = await pages[0].page.evaluate(() => window.__akhand.view());
  ok("malformed ingest dropped, page still live", afterJunk === "live", afterJunk);

  // silence -> resting within ~35 s
  const t0 = Date.now();
  cmd("silent");
  let restedAt = null;
  while (Date.now() - t0 < 45000) {
    const views = await Promise.all(pages.map((p) => p.page.evaluate(() => window.__akhand.view())));
    if (views.every((v) => v === "resting")) { restedAt = (Date.now() - t0) / 1000; break; }
    await sleep(1000);
  }
  ok("resting state after harness goes silent", restedAt !== null && restedAt <= 36, restedAt ? `${restedAt.toFixed(1)} s` : "never");
  await sleep(1500);
  for (const { sz, page } of pages) {
    const t = await page.evaluate(() => ({ n: document.getElementById("nShloka").textContent, feed: document.getElementById("feedText").textContent }));
    ok(`[${sz.name}] resting keeps last counts`, /\d/.test(t.n), `${t.n} | ${t.feed}`);
    await page.screenshot({ path: `${OUT}resting-${sz.name}.png` });
  }
  const st = await (await fetch(`${BASE}/state`)).json();
  ok("GET /state reports offline with last state while silent", st.t === "offline" && st.last && st.last.t === "state");

  // reconnect after feed restart
  stopFeed();
  await sleep(1000);
  startFeed();
  await sleep(9000);
  for (const { sz, page } of pages) {
    const v = await page.evaluate(() => window.__akhand.view());
    ok(`[${sz.name}] back to live after feed restart`, v === "live", v);
  }
  const s2 = await (await fetch(`${BASE}/state`)).json();
  ok("GET /state live again", s2.t === "state");

  // second ingest replaces first
  stopFeed();
  await sleep(500);
  const tok = { headers: { Authorization: "Bearer test-token-local-only" } };
  const first = new WebSocket(`${WSB}/ingest`, tok);
  await new Promise((r) => first.on("open", r));
  let firstClosed = false;
  first.on("close", () => { firstClosed = true; });
  first.on("message", () => {});
  const second = new WebSocket(`${WSB}/ingest`, tok);
  await new Promise((r) => second.on("open", r));
  await sleep(800);
  ok("second ingest connection replaces the first", (firstClosed || first.readyState >= 2) && second.readyState === WebSocket.OPEN);
  second.close();

  for (const { sz, errors } of pages) ok(`[${sz.name}] no console errors`, errors.length === 0, errors.slice(0, 3).join(" | "));
  viewer.close();
} catch (e) {
  console.error("QA crashed:", e);
  results.push({ name: "crash", pass: false, info: String(e) });
} finally {
  if (browser) await browser.close();
  stopFeed();
}
const failed = results.filter((r) => !r.pass);
console.log(`\n${results.length - failed.length}/${results.length} passed`);
process.exit(failed.length ? 1 : 0);
