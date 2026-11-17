# Akhand 108: spec

108 AI agents recite Hindu prayers around the clock on Mehul's behalf, and a public live page shows it.
"Akhand" (unbroken) is a working name.

## Decisions already made (do not reopen)

- 108 agents = 12 stations x 9 agents. The AI model itself writes every word of every prayer.
- The model recites "with the book open": the verified verse text is in the prompt; the reply is checked against it. A wrong line does not count and is recited again.
- Models, through OpenCode Go (OpenAI-compatible chat completions, `https://opencode.ai/inference/go/openai/v1/chat/completions`):
  - primary `iceman-contributor-free` (free, may disappear or throttle), at human chanting pace (about 1 verse per agent per 10 s).
  - backup `muse-spark-1.3-contributor` ($0.10 in / $0.20 out per 1M tokens), hard budget **$30 per calendar month** and **$7 per rolling 7 days**; pace follows the budget.
- Runs on a small arm64 cloud VM as its own Docker container, separate from the `hermes` container. Hermes is the "head priest": health, restart, daily Discord report. It is not one of the 108.
- Public watch-only live page (approved design: `design/hero-mala-v2.html`) plus relay, both on Cloudflare (one Worker with static assets and one Durable Object). The VM only makes outbound connections.
- The sankalpa names the beneficiary by first name only (`SANKALPA_NAME`, default `Mehul`).
- Every day at `GATHERING_TIME` (default 06:00, `TZ=Asia/Kolkata`) all 108 gather: sankalpa, Ganesh invocation, Shiv vandana, Durga vandana, then back to stations.
- Nothing on the page is simulated. If no real events arrive, the page says the agents are resting.

## Stations (index 0-11, agent index = station*9 + seat, seats 0-8)

| # | id | Deity/label (hi) | Mode | Works |
|---|---|---|---|---|
| 0 | shiv-japa | शिव जप | japa | Om Namah Shivaya |
| 1 | shiv-stuti | शिव स्तुति | parallel | Rudrashtakam, Shiv Tandav Stotram, Lingashtakam, Shiv Chalisa |
| 2 | mrityunjaya | मृत्युंजय | japa | Mahamrityunjaya mantra |
| 3 | saptashati | सप्तशती | relay | Durga Saptashati (as much as is cleanly sourced) |
| 4 | durga | दुर्गा | parallel | Durga Chalisa, Devi Kavach, Argala Stotram, Navarna mantra |
| 5 | ganesh | गणेश | parallel | Ganesh Atharvashirsha, Vakratunda, Ganesh Chalisa |
| 6 | ram | राम | relay | Ramcharitmanas (akhand path) |
| 7 | hanuman | हनुमान | parallel | Hanuman Chalisa, Bajrang Baan, Sundarkand if not already under Ram |
| 8 | vishnu | विष्णु | parallel | Vishnu Sahasranama, Bhagavad Gita |
| 9 | lakshmi | लक्ष्मी | parallel | Sri Suktam, Lakshmi Chalisa, Saraswati Vandana |
| 10 | surya | सूर्य | parallel | Gayatri mantra (japa-style), Aditya Hridayam |
| 11 | navagraha | नवग्रह | japa-per-seat | seat k recites graha k's mantra: Surya, Chandra, Mangal, Budh, Guru, Shukra, Shani, Rahu, Ketu |

Modes:
- `parallel`: each seat has its own cursor and loops through the station's works; seats start at staggered offsets.
- `relay`: one shared cursor; one seat recites a turn (`RELAY_TURN_VERSES`, default 8), then hands to the next seat. Exactly one seat is active at a time.
- `japa`: every seat repeats the single mantra; one request asks for `JAPA_REPS` (default 9) repetitions, one per line.
- `japa-per-seat`: like japa, but each seat has its own mantra.

## Contract 1: text library (`library/library.json`), owner: text agent, consumer: harness

```json
{
  "version": 1,
  "built_at": "ISO-8601",
  "stations": [
    {
      "index": 0, "id": "shiv-japa", "label_hi": "शिव जप", "label_en": "Shiv Japa", "mode": "japa",
      "works": [
        {
          "id": "om-namah-shivaya", "title_hi": "...", "title_en": "Om Namah Shivaya",
          "source_url": "https://...", "source_name": "...", "seat": null,
          "verses": [ { "n": 1, "text": "ॐ नमः शिवाय" } ]
        }
      ]
    }
  ],
  "gathering": {
    "ganesh": { "title_en": "...", "source_url": "...", "verses": [ { "n": 1, "text": "..." } ] },
    "shiv":   { "...": "same shape" },
    "durga":  { "...": "same shape" }
  }
}
```

Rules:
- `text` is Devanagari, NFC-normalised, one verse per entry (a shloka, a chaupai pair, a doha, or one mantra). Line breaks inside a verse are `\n`. No verse numbers, no translation, no trailing `॥१॥` markers inside `text`.
- A verse is at most about 400 characters; split longer units.
- `seat` is null except in station 11, where it is 0-8.
- Every verse comes from a fetched source recorded in `source_url`. Nothing is written from memory.
- All 12 stations must have at least one work. Long works may be partial at launch if the source is not clean; say so in `texts/COVERAGE.md`.

## Contract 2: relay events (harness -> relay -> page), owner: this spec

Transport: the harness holds one WebSocket to `wss://<relay>/ingest` with header `Authorization: Bearer <RELAY_TOKEN>`. Viewers connect to `wss://<relay>/live` (no auth, read-only). `GET /state` returns the last `state` message as JSON (or `{"t":"offline"}`), CORS open.

Messages (JSON text frames), all forwarded verbatim to viewers:

```json
{ "t": "batch", "ts": 1794542000000, "ev": [[14, 3], [57, 1]] }
```
Sent once per second when there is anything to send. Each pair is `[agentIndex 0-107, verifiedUnits]` completed in that second.

```json
{
  "t": "state", "ts": 1794542000000,
  "mode": "praying",
  "provider": "iceman",
  "name": "Mehul",
  "day": 12, "streak_days": 12,
  "today": { "units": 431208, "malas": 3992, "corrections": 41 },
  "all_time": { "units": 5120000, "malas": 47407 },
  "stations": [ { "units_today": 31000, "active": 9 } ],
  "voices": [ { "station": 0, "agent": 4, "work_en": "Om Namah Shivaya", "text": "ॐ नमः शिवाय" } ],
  "relay_holders": { "3": 2, "6": 5 }
}
```
Sent every 5 s and immediately on connect. `mode` is `praying`, `gathering` or `resting`. `provider` is `iceman`, `muse` or `none`. `stations` has 12 entries. `voices` has up to 12 entries, one per station: the verse an agent of that station most recently completed (first 2 lines at most). `relay_holders` maps relay-station index to the active seat. A "unit" is one verified verse or one verified mantra repetition; `malas = floor(units / 108)`.

The relay adds, to viewers only:
```json
{ "t": "viewers", "n": 37 }
{ "t": "offline", "since": 1794542000000 }
```
`offline` is sent when the harness has been silent for 30 s, and to new viewers while it is silent.

## Harness behaviour (Python 3.12, asyncio; deps: httpx, websockets only)

- One process, 108 agent tasks, one SQLite ledger (WAL) under `DATA_DIR` (default `/data`).
- Request: system prompt = short role + sankalpa; user prompt = the verses to recite, one per block. Ask for the text exactly, one verse per block separated by a blank line, nothing else. Stream the reply; verify each completed verse as it arrives; emit the event at that moment.
- Verification: NFC, strip ZWJ/ZWNJ, whitespace, dandas (`।` `॥`), Devanagari and ASCII digits and common punctuation, then exact equality with the expected verse. Anything else in the reply is ignored, not counted.
- The cursor advances only for verified verses, in order, and is committed to the ledger before the event is sent. A restart never double-counts and never skips.
- A verse that fails 3 times in a row is recorded in `stuck_verses`, skipped, and reported in status.
- Pacing: global rate limit (`TARGET_VERSES_PER_AGENT_HOUR`, default 360) and `MAX_CONCURRENCY` (default 24) on the primary. On the backup, a dollar bucket: month cap `MUSE_MONTH_USD=30`, 7-day cap `MUSE_WEEK_USD=7`; cost is computed from the response `usage` tokens and the prices above (use `opencode.allowance` from the response if present). A call is made only if both caps have room.
- Failover: per-agent exponential backoff with jitter on errors. A circuit breaker moves all agents to the backup when more than half of primary calls failed over the last 5 minutes, probes the primary every 15 minutes, and moves back when a probe verifies. Auth errors (401/403) on both providers stop the agents (mode `resting`) instead of retrying in a tight loop.
- Mode `resting` whenever no provider is usable or the budget is dry. The streak (`streak_days`) resets if a silence longer than `MAX_SILENCE_MIN` (default 30) occurs.
- Gathering at `GATHERING_TIME`: agents finish their current request, then all 108 recite the sankalpa line and the three gathering texts, then return to their stations. Runs once per local day; if the process was down at that time it runs on start, once.
- Relay client: reconnects with backoff forever; prayer never waits on the relay; unsent batches older than 10 s are dropped.
- Single-instance lock in `DATA_DIR`. Graceful stop on SIGTERM (finish or abandon in-flight requests without counting).
- Sends `User-Agent: akhand-108/1.0` and a stable `x-opencode-session` per agent.
- `python -m akhand.ctl status --json` prints: mode, provider, per-provider health, today's units/malas/corrections, spend this month and last 7 days, seconds since the last verified unit, stuck verses, relay connected. Exit code 0 healthy, 1 degraded, 2 down. Also `ctl pause`, `ctl resume`.
- `PROVIDER=fake` runs entirely offline with a built-in fake model (configurable error rate, wrong-word rate, latency) for tests and demos.
- Env: `OPENCODE_API_KEY`, `RELAY_URL`, `RELAY_TOKEN`, plus the names above. `.env.example` lists all.

## Relay and page

- One Cloudflare Worker `akhand-108`: static assets for the page, one Durable Object holding the sockets and the last state. Must fit the Workers free plan; state the limits it relies on.
- Page = `design/hero-mala-v2.html` driven by real events: a bead lights and sends a spark when its agent appears in a `batch`; counters, verse panel, relay holders, provider and day come from `state`. States: connecting, live, gathering, resting/offline. Reconnects automatically. Share metadata (title, description, Open Graph image) for social posts. Works at phone and desktop sizes, respects reduced motion, and stays smooth on a mid-range phone.

## Out of scope for this version

Sound, visitor interaction, computed astronomical sunrise, a custom domain.
