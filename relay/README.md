# Akhand 108 relay and live page

One Cloudflare Worker (`akhand-108`) with static assets (`public/`) and one SQLite-backed Durable Object (`Relay`).
The harness connects out to `/ingest`; viewers connect to `/live`. The VM never accepts inbound connections.

| Path | What |
| --- | --- |
| `wss://<host>/ingest` | harness only. `Authorization: Bearer <RELAY_TOKEN>`; a second connection replaces the first |
| `wss://<host>/live` | viewers, read-only. Anything a viewer sends is ignored |
| `GET /state` | last `state` as JSON, CORS open. `{"t":"offline"}` if none yet. If the harness has been silent for 30 s: `{"t":"offline","since":ms,"last":<last state>}` (extension so the polling fallback can still show last known counts) |
| `/` | the live page (`public/index.html`), `?demo=1` runs a labelled local simulation (`?demo=gathering`, `?demo=resting` for the other states) |

Relay behaviour: validates and size-limits every ingest frame (32 KB, shapes per Contract 2, anything else is dropped silently and never forwarded), forwards valid `batch` and `state` frames verbatim to all viewers, stores the last `state` (also in Durable Object storage, so it survives eviction), sends it to each new viewer, sends `viewers` every 10 s, and sends `offline` when the harness has been silent for 30 s (and to new viewers while silent). `MAX_VIEWERS` (var in `wrangler.jsonc`, default 400) caps viewer sockets; extra viewers get HTTP 503 and the page backs off and falls back to polling.

## Run locally

```
npm install
npm run typecheck            # tsc --noEmit
npx wrangler dev --port 8787 # token comes from .dev.vars (gitignored)
node tools/feed.mjs          # fake harness: ~11 events/s, state every 5 s
node tools/feed.mjs --mode gathering
node tools/feed.mjs --silent
```
While `feed.mjs` runs, type `gather`, `praying`, `resting`, `silent`, `talk`, `junk` or `quit` plus Enter.
`node tools/qa.mjs` runs the full acceptance pass (needs wrangler dev running; starts its own feed and private Chromium; screenshots go to `qa/`).

## Deploy (not done by this build)

```
npx wrangler secret put RELAY_TOKEN   # same value as the harness RELAY_TOKEN
npx wrangler deploy
node tools/set-origin.mjs https://akhand-108.<account>.workers.dev   # optional: absolute og:image URL, then deploy again
```
Share cards need an absolute image URL; `index.html` ships with `/og.png` and `tools/set-origin.mjs` rewrites it to your real origin.

## Cloudflare free-plan limits this design relies on

Checked 2026-11-13 against developers.cloudflare.com (Durable Objects pricing, Workers limits, Static Assets billing). All daily limits reset at 00:00 UTC (05:30 IST).

| Limit (Free) | Value | How this design uses it |
| --- | --- | --- |
| Workers requests | 100,000 / day | one per `/live` connect, `/state` poll, `/ingest` connect. Page and `og.png` are static assets: "free and unlimited", not counted |
| Workers CPU | 10 ms / request | the Worker only authenticates and forwards; well under |
| Durable Object requests | 100,000 / day (HTTP, WebSocket opens, alarms, incoming WebSocket messages at 20:1) | see below |
| DO duration | 13,000 GB-s / day (billed at 128 MB) | 86,400 s x 0.125 GB = 10,800 GB-s even if the object never slept, so it cannot run out on time alone. Hibernation API used, so it mostly sleeps |
| DO SQLite rows written | 100,000 / day | one key written per `state` (every 5 s) = 17,280 / day |
| DO SQLite rows read | 5,000,000 / day | one read per wake-up |
| Storage | 5 GB | a few KB |
| Outgoing WebSocket messages, incoming pings | not billed | all fan-out to viewers is free |

DO request budget per day:
- harness: 1.2 incoming frames/s x 86,400 / 20 = about 5,200
- alarm tick every 10 s while anyone is connected: 8,640
- that leaves about 86,000 for viewer connections (each connect is 1 Worker request and 1 DO request; page sends nothing else)

So roughly **85,000 viewer connections a day**. Viewer-hours are not metered by themselves: a connected viewer costs nothing while idle, so the practical ceiling is concurrency (`MAX_VIEWERS`, 400 by default; 400 viewers all day would be 9,600 viewer-hours) and the connection count above. The page closes its socket after the tab has been hidden for 60 s and reconnects when shown, which keeps both numbers down. The polling fallback costs 1 Worker request per 10 s per viewer (8,640 a day for one open tab), so it is only used if WebSockets keep failing.

When a limit is hit (Cloudflare behaviour, not tested here): past 100,000 Worker requests the Worker returns Error 1027 / 429 for `/live` and `/state`, while the static page still loads; past the Durable Object limits, DO operations fail with errors, so new viewers and harness frames are refused until 00:00 UTC. The harness keeps reconnecting with backoff and prayer is unaffected. On the page, viewers lose data, show "RECONNECTING" after 50 s without any message, and keep retrying with backoff (max 30 s). Nothing is simulated while disconnected. If this is a real risk on launch day, the paid Workers plan ($5/month) lifts all of these.

One residual risk: a viewer that spams the socket is billed at 20:1 as DO requests. Messages are ignored but still wake the object. 400 viewers at most, so a single abuser cannot do much; add per-IP limits at Cloudflare if it ever happens.
