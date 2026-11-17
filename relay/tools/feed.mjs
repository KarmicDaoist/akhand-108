// Local test feeder: speaks Contract 2 to /ingest like the harness would.
// Usage: node tools/feed.mjs [--url ws://127.0.0.1:8787/ingest] [--token T]
//          [--mode praying|gathering|resting] [--silent] [--rate 11] [--units 431208]
// While running, type a command and press Enter: gather | praying | resting | silent | talk | quit
import WebSocket from "ws";

const args = process.argv.slice(2);
const opt = (name, dflt) => {
  const i = args.indexOf(`--${name}`);
  return i >= 0 ? args[i + 1] : dflt;
};
const URL_ = opt("url", "ws://127.0.0.1:8787/ingest");
const TOKEN = opt("token", process.env.RELAY_TOKEN || "test-token-local-only");
const RATE = Number(opt("rate", 11));
let mode = opt("mode", "praying");
let silent = args.includes("--silent");
let units = Number(opt("units", 431208));
let corrections = 41;
let day = 12;

const stationInfo = [
  ["Om Namah Shivaya", ["ॐ नमः शिवाय"]],
  ["Rudrashtakam", ["नमामीशमीशान निर्वाणरूपं", "विभुं व्यापकं ब्रह्मवेदस्वरूपम्"]],
  ["Mahamrityunjaya", ["ॐ त्र्यम्बकं यजामहे", "सुगन्धिं पुष्टिवर्धनम्"]],
  ["Durga Saptashati", ["या देवी सर्वभूतेषु", "शक्तिरूपेण संस्थिता"]],
  ["Durga Chalisa", ["नमो नमो दुर्गे सुख करनी", "नमो नमो अम्बे दुःख हरनी"]],
  ["Vakratunda", ["वक्रतुण्ड महाकाय", "सूर्यकोटि समप्रभ"]],
  ["Ramcharitmanas", ["मंगल भवन अमंगल हारी", "द्रवहु सो दसरथ अजिर बिहारी"]],
  ["Hanuman Chalisa", ["जय हनुमान ज्ञान गुन सागर", "जय कपीस तिहुँ लोक उजागर"]],
  ["Vishnu Sahasranama", ["शान्ताकारं भुजगशयनं", "पद्मनाभं सुरेशम्"]],
  ["Sri Suktam", ["ॐ श्रीं महालक्ष्म्यै नमः"]],
  ["Gayatri Mantra", ["ॐ भूर्भुवः स्वः", "तत्सवितुर्वरेण्यम्"]],
  ["Navagraha Mantra", ["ॐ शं शनैश्चराय नमः"]],
];
const holders = { 3: 2, 6: 5 };
const stUnits = Array.from({ length: 12 }, () => Math.floor(units / 12));
const lastVoice = stationInfo.map((_, s) => ({ station: s, agent: s * 9 + (s % 9) }));

let ws = null;
let timersOn = false;

function send(obj) {
  if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(obj));
}

function batch() {
  if (silent || mode === "resting") return;
  const n = Math.max(1, Math.round(RATE + (Math.random() * 4 - 2)));
  const ev = [];
  for (let q = 0; q < n; q++) {
    let a = Math.floor(Math.random() * 108);
    const st = Math.floor(a / 9);
    if (st === 3 || st === 6) a = st * 9 + holders[st];
    const u = Math.random() < 0.1 ? 3 : 1;
    ev.push([a, u]);
    units += u;
    stUnits[st] += u;
    lastVoice[st] = { station: st, agent: a };
  }
  if (Math.random() < 0.2) holders[3] = (holders[3] + 1) % 9;
  if (Math.random() < 0.3) holders[6] = (holders[6] + 1) % 9;
  if (Math.random() < 0.02) corrections++;
  send({ t: "batch", ts: Date.now(), ev });
}

function state() {
  if (silent) return;
  const resting = mode === "resting";
  send({
    t: "state",
    ts: Date.now(),
    mode,
    provider: resting ? "none" : "iceman",
    name: "Mehul",
    day,
    streak_days: day,
    today: { units, malas: Math.floor(units / 108), corrections },
    all_time: { units: units + 4_688_792, malas: Math.floor((units + 4_688_792) / 108) },
    stations: stationInfo.map((_, s) => ({ units_today: stUnits[s], active: s === 3 || s === 6 ? 1 : 9 })),
    voices: stationInfo.map(([work, lines], s) => ({ station: s, agent: lastVoice[s].agent, work_en: work, text: lines.join("\n") })),
    relay_holders: resting ? {} : { ...holders },
  });
}

function connect() {
  ws = new WebSocket(URL_, { headers: { Authorization: `Bearer ${TOKEN}` } });
  ws.on("open", () => {
    console.log("ingest connected");
    state();
    if (!timersOn) {
      timersOn = true;
      setInterval(batch, 1000);
      setInterval(state, 5000);
    }
  });
  ws.on("unexpected-response", (_req, res) => {
    console.error(`ingest rejected: HTTP ${res.statusCode}`);
    process.exit(2);
  });
  ws.on("close", (code) => {
    console.log("ingest closed", code);
    setTimeout(connect, 1000);
  });
  ws.on("error", () => {});
}

process.stdin.setEncoding("utf8");
process.stdin.on("data", (chunk) => {
  for (const line of chunk.split(/\r?\n/)) {
    const cmd = line.trim();
    if (!cmd) continue;
    if (cmd === "gather") mode = "gathering";
    else if (cmd === "praying") mode = "praying";
    else if (cmd === "resting") mode = "resting";
    else if (cmd === "silent") silent = true;
    else if (cmd === "talk") silent = false;
    else if (cmd === "junk") {
      send({ t: "batch", ts: "x", ev: "nope" });
      ws?.send("not json");
      ws?.send(JSON.stringify({ t: "state", mode: "praying" }));
    } else if (cmd === "quit") process.exit(0);
    console.log(`mode=${mode} silent=${silent}`);
    state();
  }
});
process.stdin.on("end", () => {});

connect();
