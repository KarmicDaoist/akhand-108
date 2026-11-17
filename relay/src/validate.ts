// Validation of harness (ingest) messages. Messages are forwarded verbatim,
// so this only decides keep or drop; it never rewrites.

export const MAX_INGEST_BYTES = 32 * 1024;

const MODES = new Set(["praying", "gathering", "resting"]);
const PROVIDERS = new Set(["iceman", "muse", "none"]);

type Obj = Record<string, unknown>;

function isObj(v: unknown): v is Obj {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}
function int(v: unknown, min: number, max: number): v is number {
  return typeof v === "number" && Number.isInteger(v) && v >= min && v <= max;
}
function num(v: unknown): v is number {
  return typeof v === "number" && Number.isFinite(v) && v >= 0 && v <= 1e15;
}
function str(v: unknown, max: number): v is string {
  return typeof v === "string" && v.length <= max;
}

function validBatch(m: Obj): boolean {
  if (!num(m.ts) || !Array.isArray(m.ev) || m.ev.length > 216) return false;
  return m.ev.every((p) => Array.isArray(p) && p.length === 2 && int(p[0], 0, 107) && int(p[1], 1, 100000));
}

function validState(m: Obj): boolean {
  if (!num(m.ts) || !MODES.has(m.mode as string) || !PROVIDERS.has(m.provider as string)) return false;
  if (!str(m.name, 64) || !int(m.day, 0, 1e6) || !int(m.streak_days, 0, 1e6)) return false;
  const today = m.today;
  const all = m.all_time;
  if (!isObj(today) || !num(today.units) || !num(today.malas) || !num(today.corrections)) return false;
  if (!isObj(all) || !num(all.units) || !num(all.malas)) return false;
  if (!Array.isArray(m.stations) || m.stations.length !== 12) return false;
  if (!m.stations.every((s) => isObj(s) && num(s.units_today) && int(s.active, 0, 9))) return false;
  if (!Array.isArray(m.voices) || m.voices.length > 12) return false;
  const voicesOk = m.voices.every(
    (v) => isObj(v) && int(v.station, 0, 11) && int(v.agent, 0, 107) && str(v.work_en, 120) && str(v.text, 600),
  );
  if (!voicesOk) return false;
  const rh = m.relay_holders;
  if (!isObj(rh)) return false;
  return Object.entries(rh).every(([k, v]) => int(Number(k), 0, 11) && int(v, 0, 8));
}

/** Returns the message type if the raw frame is a valid batch or state, else null. */
export function classify(raw: string): "batch" | "state" | null {
  if (raw.length > MAX_INGEST_BYTES) return null;
  let m: unknown;
  try {
    m = JSON.parse(raw);
  } catch {
    return null;
  }
  if (!isObj(m)) return null;
  if (m.t === "batch") return validBatch(m) ? "batch" : null;
  if (m.t === "state") return validState(m) ? "state" : null;
  return null;
}
