// Sound acceptance run. Needs `npx wrangler dev --port 8787` running. Starts its own feed and a private
// headless Chromium (autoplay allowed), measures the real page's output with the page's own AnalyserNode,
// renders the same synthesis graph offline to qa/sound-sample.wav, and stops everything at the end.
import { spawn } from "node:child_process";
import { chromium } from "playwright";
import fs from "node:fs";
import { fileURLToPath } from "node:url";

const BASE = "http://127.0.0.1:8787";
const OUT = fileURLToPath(new URL("../qa/", import.meta.url));
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const db = (x) => (x > 0 ? 20 * Math.log10(x) : -200);
const f1 = (x) => db(x).toFixed(1) + " dBFS";
let fails = 0;
const ok = (name, pass, info = "") => { if (!pass) fails++; console.log(`${pass ? "PASS" : "FAIL"}  ${name}${info ? "  (" + info + ")" : ""}`); };

let feed = null;
const startFeed = () => { feed = spawn(process.execPath, ["tools/feed.mjs"], { stdio: ["pipe", "pipe", "inherit"] }); feed.stdout.on("data", () => {}); };
const cmd = (c) => feed.stdin.write(c + "\n");

// in-page meter: samples the page's analyser every 25 ms
const METER = () => {
  const an = window.__snd.analyser(), buf = new Float32Array(an.fftSize);
  window.__m = [];
  window.__mi = setInterval(() => {
    an.getFloatTimeDomainData(buf);
    let s = 0, pk = 0;
    for (let i = 0; i < buf.length; i++) { s += buf[i] * buf[i]; pk = Math.max(pk, Math.abs(buf[i])); }
    window.__m.push({ t: performance.now(), r2: s / buf.length, pk });
  }, 25);
};
const mark = (page) => page.evaluate(() => performance.now());
async function stats(page, a, b) {
  const rows = await page.evaluate(([a, b]) => window.__m.filter((x) => x.t >= a && x.t <= b), [a, b]);
  if (!rows.length) return { rms: 0, peak: 0, n: 0 };
  return { rms: Math.sqrt(rows.reduce((z, x) => z + x.r2, 0) / rows.length), peak: Math.max(...rows.map((x) => x.pk)), n: rows.length };
}
async function perSecond(page, t0, secs) {
  const out = [];
  for (let s = 0; s < secs; s++) out.push((await stats(page, t0 + s * 1000, t0 + (s + 1) * 1000)).rms);
  return out;
}

let browser;
try {
  startFeed();
  await sleep(2500);
  browser = await chromium.launch({ headless: true, args: ["--autoplay-policy=no-user-gesture-required"] });
  const errors = [];
  const newPage = async (w, h, dsf = 1, init) => {
    const ctx = await browser.newContext({ viewport: { width: w, height: h }, deviceScaleFactor: dsf });
    const page = await ctx.newPage();
    if (init) await page.addInitScript(init);
    page.on("console", (m) => { if (m.type() === "error") errors.push(`[${w}x${h}] ${m.text()}`); });
    page.on("pageerror", (e) => errors.push(`[${w}x${h}] ${String(e)}`));
    return page;
  };

  // ---------------- live feed, 390x844 ----------------
  const page = await newPage(390, 844, 2);
  await page.goto(BASE + "/", { waitUntil: "load" });
  await sleep(4000);
  const pre = await page.evaluate(() => ({ ctx: window.__snd.ctx(), view: window.__akhand.view(), label: document.getElementById("sndBtn").innerText.replace(/\s+/g, " "), pressed: document.getElementById("sndBtn").getAttribute("aria-pressed") }));
  ok("nothing plays before first tap (no AudioContext exists)", pre.ctx === null && pre.pressed === "false", `${pre.label}, view ${pre.view}`);

  await page.click("#sndBtn");
  await page.evaluate(METER);
  const tOn = await mark(page);
  const st0 = await page.evaluate(() => ({ state: window.__snd.ctx().state, on: window.__snd.isOn(), pressed: document.getElementById("sndBtn").getAttribute("aria-pressed") }));
  ok("first tap creates and resumes the AudioContext", st0.state === "running" && st0.on && st0.pressed === "true", JSON.stringify(st0));
  await sleep(14000);
  const live = await stats(page, tOn + 3000, tOn + 13000);
  ok("live: non-silent over 10 s", live.rms > 0.003, `RMS ${f1(live.rms)}, peak ${f1(live.peak)} (${live.n} frames)`);
  ok("live: peak below -1 dBFS (no clipping)", live.peak < 0.89, f1(live.peak));
  const perSec = await perSecond(page, tOn + 3000, 10);
  console.log("      per-second RMS: " + perSec.map((x) => db(x).toFixed(0)).join(" "));
  const levelBeforeBell = (await stats(page, tOn + 11000, tOn + 14000)).rms;

  // mala completion through the real code path: 1000 units crosses a mala boundary
  const tBell = await page.evaluate(() => { const t = performance.now(); window.__akhand.handle({ t: "batch", ts: Date.now(), ev: [[5, 1000]] }); return t; });
  await sleep(7000);
  const bellRows = await perSecond(page, tBell, 6);
  const bellPeak = Math.max(...(await Promise.all([0, 1, 2].map((s) => stats(page, tBell + s * 500, tBell + s * 500 + 500)))).map((x) => x.rms));
  ok("mala complete: clear level bump", bellPeak > levelBeforeBell * 1.5, `bed ${f1(levelBeforeBell)} -> bell 0.5 s RMS ${f1(bellPeak)} (+${(db(bellPeak) - db(levelBeforeBell)).toFixed(1)} dB); per-second after: ${bellRows.map((x) => db(x).toFixed(0)).join(" ")}`);
  const bellAll = await stats(page, tBell, tBell + 6000);
  ok("with the bell: peak still below -1 dBFS", bellAll.peak < 0.89, f1(bellAll.peak));

  // gathering then back
  cmd("gather"); await sleep(9000);
  const gath = await stats(page, (await mark(page)) - 6000, await mark(page));
  const gView = await page.evaluate(() => window.__akhand.view());
  ok("gathering: sounding (drone + bells)", gView === "gathering" && gath.rms > 0.003, `RMS ${f1(gath.rms)}, peak ${f1(gath.peak)}`);
  cmd("praying"); await sleep(7000);

  // silent feed: harness stops sending batches
  cmd("silent");
  const tSil = await mark(page);
  await sleep(11000);
  const silRows = await perSecond(page, tSil, 10);
  console.log("      silent feed, per-second RMS: " + silRows.map((x) => db(x).toFixed(0)).join(" "));
  const silentFrom = silRows.findIndex((x, i) => silRows.slice(i).every((y) => y < 0.0005));
  ok("feed silent: RMS falls to silence (< -66 dBFS) within ~6 s", silentFrom >= 0 && silentFrom <= 6, `silent from second ${silentFrom}; at 7-10 s ${f1((await stats(page, tSil + 7000, tSil + 10000)).rms)}`);
  cmd("talk"); await sleep(1000);
  const tBack = await mark(page); await sleep(6000);
  ok("feed returns: sound fades back in", (await stats(page, tBack + 3000, tBack + 6000)).rms > 0.003, f1((await stats(page, tBack + 3000, tBack + 6000)).rms));

  // resting mode (state says resting)
  cmd("resting");
  const tRest = await mark(page); await sleep(8000);
  const restRows = await perSecond(page, tRest, 8);
  console.log("      resting, per-second RMS: " + restRows.map((x) => db(x).toFixed(0)).join(" "));
  const restView = await page.evaluate(() => window.__akhand.view());
  ok("resting: quiet within ~5 s", restView === "resting" && (await stats(page, tRest + 5000, tRest + 8000)).rms < 0.0005, `view ${restView}, 5-8 s ${f1((await stats(page, tRest + 5000, tRest + 8000)).rms)}`);
  await page.screenshot({ path: OUT + "sound-resting-390x844.png" });
  cmd("praying"); await sleep(7000);

  // toggle off
  await page.click("#sndBtn");
  const tOff = await mark(page); await sleep(3500);
  const off = await stats(page, tOff + 700, tOff + 3500);
  const offInfo = await page.evaluate(() => ({ state: window.__snd.ctx().state, pressed: document.getElementById("sndBtn").getAttribute("aria-pressed"), stored: localStorage.getItem("akhand_sound") }));
  ok("after toggling off: silent and context suspended", off.peak < 0.0005 && offInfo.pressed === "false", `peak ${f1(off.peak)}, ${JSON.stringify(offInfo)}`);
  await page.evaluate(() => clearInterval(window.__mi));

  // remembered choice: toggle on, reload, still off until a gesture, then on
  await page.click("#sndBtn");
  await sleep(300);
  await page.reload({ waitUntil: "load" });
  await sleep(1500);
  const afterReload = await page.evaluate(() => ({ ctx: window.__snd.ctx(), stored: localStorage.getItem("akhand_sound") }));
  await page.mouse.click(200, 400);
  await sleep(500);
  const afterTap = await page.evaluate(() => ({ on: window.__snd.isOn(), state: window.__snd.ctx() && window.__snd.ctx().state }));
  ok("remembered choice: no sound until a gesture, then resumes", afterReload.ctx === null && afterReload.stored === "1" && afterTap.on && afterTap.state === "running", JSON.stringify({ afterReload: afterReload.stored, afterTap }));
  await page.click("#sndBtn"); // leave off

  // keyboard
  await page.focus("#sndBtn");
  await page.keyboard.press("Enter");
  const kb = await page.evaluate(() => window.__snd.isOn());
  await page.keyboard.press("Space");
  const kb2 = await page.evaluate(() => window.__snd.isOn());
  ok("keyboard toggles (Enter on, Space off)", kb === true && kb2 === false);
  const hit = await page.evaluate(() => { const r = document.getElementById("sndBtn").getBoundingClientRect(); return { w: r.width, h: r.height }; });
  ok("touch target >= 44px", hit.w >= 44 && hit.h >= 44, `${hit.w.toFixed(0)}x${hit.h.toFixed(0)}`);

  // ---------------- offline render of the same graph ----------------
  const r = await page.evaluate(() => window.__snd.render(10));
  const pcm = Buffer.from(r.b64, "base64"), n = pcm.length / 2;
  const hdr = Buffer.alloc(44);
  hdr.write("RIFF", 0); hdr.writeUInt32LE(36 + pcm.length, 4); hdr.write("WAVEfmt ", 8); hdr.writeUInt32LE(16, 16); hdr.writeUInt16LE(1, 20); hdr.writeUInt16LE(2, 22);
  hdr.writeUInt32LE(r.sr, 24); hdr.writeUInt32LE(r.sr * 4, 28); hdr.writeUInt16LE(4, 32); hdr.writeUInt16LE(16, 34); hdr.write("data", 36); hdr.writeUInt32LE(pcm.length, 40);
  fs.writeFileSync(OUT + "sound-sample.wav", Buffer.concat([hdr, pcm]));
  let ss = 0, pk = 0, seg = [0, 0, 0], segN = [0, 0, 0];
  for (let i = 0; i < n; i++) { const v = pcm.readInt16LE(i * 2) / 32768; ss += v * v; pk = Math.max(pk, Math.abs(v)); const s = Math.min(2, Math.floor((i / 2) / r.sr / (10 / 3))); seg[s] += v * v; segN[s]++; }
  console.log(`      offline sample: ${r.notes} notes, RMS ${f1(Math.sqrt(ss / n))}, peak ${f1(pk)}; RMS by third: ${seg.map((x, i) => f1(Math.sqrt(x / segN[i]))).join(" / ")}`);
  ok("offline WAV written, peak below -1 dBFS", pk < 0.89 && pk > 0.05, f1(pk));

  // ---------------- demo mode ----------------
  const demo = await newPage(390, 844, 2);
  await demo.goto(BASE + "/?demo=1", { waitUntil: "load" });
  await sleep(1500);
  await demo.click("#sndBtn");
  await demo.evaluate(METER);
  const tD = await mark(demo); await sleep(12000);
  const dm = await stats(demo, tD + 3000, tD + 12000);
  ok("demo mode drives the same sound", dm.rms > 0.003 && dm.peak < 0.89, `RMS ${f1(dm.rms)}, peak ${f1(dm.peak)}`);
  await demo.screenshot({ path: OUT + "sound-demo-390x844.png" });
  const demoRest = await newPage(390, 844, 2);
  await demoRest.goto(BASE + "/?demo=resting", { waitUntil: "load" });
  await sleep(1000);
  await demoRest.click("#sndBtn");
  await demoRest.evaluate(METER);
  const tDR = await mark(demoRest); await sleep(6000);
  const dr = await stats(demoRest, tDR + 1500, tDR + 6000);
  ok("demo=resting stays silent with sound on", dr.peak < 0.0005, f1(dr.peak));

  // ---------------- chant (stubbed Hindi voice) ----------------
  const stub = () => {
    const sp = { spoken: [], cancels: 0 };
    const ss = {
      speaking: false, pending: false, getVoices: () => [{ lang: "en-US", name: "E" }, { lang: "hi-IN", name: "Fake Hindi" }], addEventListener() {},
      cancel() { sp.cancels++; ss.speaking = false; }, speak(u) { sp.spoken.push({ text: u.text, rate: u.rate, pitch: u.pitch, volume: u.volume, lang: u.lang }); ss.speaking = true; if (u.onstart) u.onstart(); },
    };
    window.__sp = sp; window.__ss = ss;
    Object.defineProperty(window, "speechSynthesis", { get: () => ss, configurable: true });
    window.SpeechSynthesisUtterance = function (t) { this.text = t; };
  };
  const ch = await newPage(390, 844, 2, stub);
  await ch.goto(BASE + "/", { waitUntil: "load" });
  await sleep(3000);
  ok("chant toggle hidden while sound is off", await ch.evaluate(() => document.getElementById("chantBtn").hidden));
  await ch.click("#sndBtn");
  await sleep(6500);
  ok("chant toggle shown when sound on and a Hindi voice exists", await ch.evaluate(() => !document.getElementById("chantBtn").hidden));
  const sp1 = await ch.evaluate(() => window.__sp.spoken.slice());
  ok("speaks the featured verse (rate 0.8, low pitch, hi voice)", sp1.length === 1 && sp1[0].rate === 0.8 && sp1[0].pitch < 1 && sp1[0].lang === "hi-IN", JSON.stringify(sp1[0]));
  await sleep(11000);
  ok("never interrupts or queues while a verse is still speaking", (await ch.evaluate(() => window.__sp.spoken.length)) === 1);
  await ch.evaluate(() => { window.__ss.speaking = false; });
  await sleep(5500);
  ok("speaks the next verse once the first has ended", (await ch.evaluate(() => window.__sp.spoken.length)) === 2);
  await ch.screenshot({ path: OUT + "sound-chant-390x844.png" });
  const c0 = await ch.evaluate(() => window.__sp.cancels);
  await ch.click("#chantBtn");
  ok("chant off cancels speech", (await ch.evaluate(() => window.__sp.cancels)) > c0 && (await ch.evaluate(() => document.getElementById("chantBtn").getAttribute("aria-pressed"))) === "false");
  await ch.click("#chantBtn");
  await ch.evaluate(() => { window.__ss.speaking = false; });
  const c1 = await ch.evaluate(() => window.__sp.cancels);
  cmd("resting"); await sleep(2500);
  ok("resting cancels speech", (await ch.evaluate(() => window.__sp.cancels)) > c1);
  cmd("praying"); await sleep(7000);
  const c2 = await ch.evaluate(() => window.__sp.cancels), n2 = await ch.evaluate(() => window.__sp.spoken.length);
  await ch.evaluate(() => { Object.defineProperty(document, "hidden", { get: () => true, configurable: true }); document.dispatchEvent(new Event("visibilitychange")); });
  await sleep(300);
  ok("hidden tab cancels speech and suspends audio", (await ch.evaluate(() => window.__sp.cancels)) > c2 && (await ch.evaluate(() => window.__snd.ctx().state)) === "suspended", `spoke ${n2} so far`);
  await ch.evaluate(() => { Object.defineProperty(document, "hidden", { get: () => false, configurable: true }); document.dispatchEvent(new Event("visibilitychange")); });
  await sleep(500);
  ok("visible again resumes audio", (await ch.evaluate(() => window.__snd.ctx().state)) === "running");
  const c3 = await ch.evaluate(() => window.__sp.cancels);
  await ch.click("#sndBtn");
  ok("sound off cancels speech and hides chant toggle", (await ch.evaluate(() => window.__sp.cancels)) > c3 && (await ch.evaluate(() => document.getElementById("chantBtn").hidden)));
  await ch.click("#sndBtn"); // sound on again for screenshots below

  // ---------------- off-state shots (fresh context, no localStorage) ----------------
  for (const [w, hh, dsf] of [[390, 844, 2], [1440, 900, 1]]) {
    const p = await newPage(w, hh, dsf);
    await p.goto(BASE + "/", { waitUntil: "load" });
    await sleep(5000);
    const lab = await p.evaluate(() => { const b = document.getElementById("sndBtn"), s = document.getElementById("sndState"), r = b.getBoundingClientRect(), sr = s.getBoundingClientRect(), f = document.getElementById("frame").getBoundingClientRect(); return { text: s.textContent, lineH: sr.height, btnCx: (r.left + r.right) / 2, frameCx: (f.left + f.right) / 2, w: r.width }; });
    ok(`[${w}x${hh}] sound-off label on one line and centred`, lab.lineH < 20 && Math.abs(lab.btnCx - lab.frameCx) < 2, JSON.stringify(lab));
    await p.screenshot({ path: OUT + `sound-off-live-${w}x${hh}.png` });
  }

  // ---------------- motion: rAF pacing ----------------
  const PROBE = () => new Promise((res) => { const ts = []; const t0 = performance.now(); const f = (t) => { ts.push(t); if (t - t0 < 10000) requestAnimationFrame(f); else res(ts); }; requestAnimationFrame(f); });
  const fstats = (ts) => { const d = []; for (let i = 1; i < ts.length; i++) d.push(ts[i] - ts[i - 1]); return { fps: +((ts.length - 1) / ((ts[ts.length - 1] - ts[0]) / 1000)).toFixed(1), over50: d.filter((x) => x > 50).length, longest: +Math.max(...d).toFixed(1) }; };
  fs.mkdirSync(OUT + "vid", { recursive: true });
  const mk = async (w, hh, dsf, video) => {
    const ctx = await browser.newContext({ viewport: { width: w, height: hh }, deviceScaleFactor: dsf, ...(video ? { recordVideo: { dir: OUT + "vid", size: { width: w, height: hh } } } : {}) });
    const p = await ctx.newPage(); p.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
    await p.goto(BASE + "/", { waitUntil: "load" }); await sleep(3500); return { p, ctx };
  };
  for (const c of browser.contexts()) await c.close();   // other open pages would steal frames from the probe
  cmd("praying"); await sleep(3000);
  const motion = [];
  { const { p, ctx } = await mk(390, 844, 2, true); const s = fstats(await p.evaluate(PROBE)); motion.push(["390x844 live (video recording on)", s]); const v = p.video(); await ctx.close(); fs.copyFileSync(await v.path(), OUT + "motion-live-390.webm"); }
  { const { p, ctx } = await mk(390, 844, 2, false); motion.push(["390x844 live", fstats(await p.evaluate(PROBE))]); await ctx.close(); }
  { const { p, ctx } = await mk(1440, 900, 1, false); motion.push(["1440x900 live", fstats(await p.evaluate(PROBE))]); await ctx.close(); }
  cmd("gather"); await sleep(6000);
  { const { p, ctx } = await mk(390, 844, 2, false); motion.push(["390x844 gathering", fstats(await p.evaluate(PROBE))]); await ctx.close(); }
  { const { p, ctx } = await mk(1440, 900, 1, false); motion.push(["1440x900 gathering", fstats(await p.evaluate(PROBE))]); await ctx.close(); }
  cmd("praying");
  for (const [n, s] of motion) ok("motion " + n, s.over50 <= 5, JSON.stringify(s));
  fs.rmSync(OUT + "vid", { recursive: true, force: true });

  // ---------------- layout: overlap + screenshots ----------------
  const overlapJs = () => {
    const r = (e) => { const b = e.getBoundingClientRect(); return { l: b.left, t: b.top, r: b.right, b: b.bottom }; };
    const bar = [...document.querySelectorAll("#sndbar button")].filter((b) => !b.hidden).map(r);
    const others = ["#sub", "#statusG", "#status", "#demoBadge", "#feedText", "#feedDot", "#nShloka", "#lDay"].map((s) => document.querySelector(s)).filter(Boolean)
      .concat([...document.querySelectorAll("#stage > text")].slice(0, 1)).map((e) => ({ id: e.id || e.textContent.slice(0, 8), ...r(e) }));
    const hits = [];
    for (const a of bar) for (const o of others) if (a.l < o.r && a.r > o.l && a.t < o.b && a.b > o.t) hits.push(o.id);
    const frame = r(document.getElementById("frame"));
    let gl = 1e9, gr = 1e9, gEl = "";
    for (const e of document.querySelectorAll("#sub1,#sub2,#status,#statusDot,#feedText,#feedText2,#feedDot,#v1,#v2,#vmeta,#stage g.lat text")) { if (!(e.textContent || e.id === "statusDot" || e.id === "feedDot")) continue; const b = e.getBoundingClientRect(); if (!b.width) continue; if (b.left - frame.l < gl) { gl = b.left - frame.l; gEl = e.id || e.textContent.slice(0, 12); } gr = Math.min(gr, frame.r - b.right); }
    for (const b of bar) { gl = Math.min(gl, b.l - frame.l); gr = Math.min(gr, frame.r - b.r); }
    const inside = bar.every((a) => a.l >= frame.l && a.r <= frame.r && a.t >= frame.t && a.b <= frame.b);
    const sb = document.getElementById("sndBtn").getBoundingClientRect(), fr = document.getElementById("frame").getBoundingClientRect();
    return { hits, inside: inside && gl >= 15.5 && gr >= 15.5, buttons: bar.length, gutterL: +gl.toFixed(1), gutterR: +gr.toFixed(1), gutterEl: gEl, geo: { frameY: Math.round(fr.top), frameH: Math.round(fr.height), statusToRingPx: +((fr.top + (804 - 497 - 23) * fr.width / 1080) - document.getElementById("status").getBoundingClientRect().bottom).toFixed(1), pillBottomToEdgePx: +(fr.bottom - sb.bottom + 6).toFixed(1), btnTopInFrame: +(sb.top - fr.top).toFixed(1), btnH: sb.height, pillH: sb.height - 12, fontPx: getComputedStyle(document.getElementById("sndBtn")).fontSize, widths: bar.map((b) => Math.round(b.r - b.l)) } };
  };
  for (const [w, h, dsf] of [[320, 640, 2], [360, 780, 2], [390, 844, 2], [1440, 900, 1]]) {
    const p = await newPage(w, h, dsf, stub);
    await p.goto(BASE + "/", { waitUntil: "load" });
    await sleep(3500);
    await p.click("#sndBtn");
    await sleep(2500);
    for (const view of ["praying", "gathering", "resting"]) {
      cmd(view === "praying" ? "praying" : view === "gathering" ? "gather" : "resting");
      await sleep(7000);
      const o = await p.evaluate(overlapJs);
      ok(`[${w}x${h}] ${view}: sound controls inside the frame, overlapping nothing`, o.hits.length === 0 && o.inside && o.buttons === 2, JSON.stringify(o));
      await p.screenshot({ path: OUT + `sound-${view === "praying" ? "live" : view}-${w}x${h}.png` });
    }
    cmd("praying");
  }
  const dm2 = await newPage(390, 844, 2, stub);
  await dm2.goto(BASE + "/?demo=gathering", { waitUntil: "load" });
  await sleep(1500); await dm2.click("#sndBtn"); await sleep(500);
  const o2 = await dm2.evaluate(overlapJs);
  ok("[390x844] demo gathering (longest subtitle + DEMO badge): no overlap", o2.hits.length === 0 && o2.inside, JSON.stringify(o2));

  ok("no console errors at 390x844 and 1440x900", errors.length === 0, errors.slice(0, 5).join(" | "));
} finally {
  if (feed) feed.kill();
  if (browser) await browser.close();
}
console.log(fails ? `\n${fails} FAILED` : "\nALL PASS");
process.exit(fails ? 1 : 0);
