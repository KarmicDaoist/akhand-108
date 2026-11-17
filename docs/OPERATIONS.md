# Akhand 108: operations

How the author's own install is run. Host names, IDs and secrets are deliberately left out; yours will differ.

Live since 2026-11-13.

## Where things are

| Piece | Where |
|---|---|
| Live page and relay | Cloudflare Worker `akhand-108`, https://akhand-108.tech-inkosei.workers.dev (deploy: `cd relay && npx wrangler deploy`) |
| Prayer program | Docker container `akhand` on a small arm64 cloud VM that also runs the Hermes agent, files in `/opt/akhand`, compose in `/opt/akhand/harness`, limits 512 MB and 0.5 CPU, no ports |
| Ledger | Docker volume `akhand-data` (`/data/akhand.db` in the container) |
| Secrets | `/opt/akhand/harness/.env` on the host (OpenCode key, relay token), never in git. Cloudflare secret `RELAY_TOKEN`. |
| Head priest | Hermes crons `akhand-watch` (every 10 min, silent unless the state changes) and `akhand-daily-report` (00:45 UTC = 06:15 IST), scripts in `/opt/data/scripts/akhand_watch.py` and `akhand_daily.py`, skill `/opt/data/skills/akhand`. Both DM Mehul on Discord. |
| Hermes hand-off folder | host `/var/lib/hermes/data/akhand` = `/opt/data/akhand` in Hermes = `/export` in akhand: `status.json`, `control.json` |

## Common tasks (run on the host)

- Status: `docker exec akhand python -m akhand.ctl status --json`
- Pause / resume: `docker exec akhand python -m akhand.ctl pause` / `resume` (or ask Hermes on Discord)
- Stop everything: `cd /opt/akhand/harness && docker compose down` (the ledger volume is kept)
- Logs: `docker logs --tail 100 akhand`
- Change pace, budget, gathering time or sankalpa: edit `/opt/akhand/harness/.env`, then `docker compose up -d`
- Update code or texts: copy the new `harness/` and `library/` to `/opt/akhand`, then `docker compose up -d --build`

## Settings in force

- Free models, tried in order: `iceman-contributor-free` (effort low), `beam-free`, `longcat-2.5-free` (180 s timeout). 24 verses per request, 360 verses per agent per hour, 24 requests at a time.
- A free model that answers 429 with a long Retry-After is skipped until that time (kept across restarts). Iceman's daily allowance was about 2,900 requests on 2026-11-13 and resets at 00:00 UTC (05:30 IST). Beam's and LongCat's allowances are not yet known.
- Paid backup `muse-spark-1.3-contributor` (effort minimal), used only when all free models are cooling down, capped at $30 per month and $7 per 7 days.
- Gathering 06:00 Asia/Kolkata. Sankalpa names मेहुल.
- Previous harness version is kept on the host at `/opt/akhand/harness.prev`.

## Known gaps

- Texts come from mirrors (sanskritdocuments.org blocked the fetch); see `texts/COVERAGE.md`. Durga Saptashati has 602 of 700 verses. Navagraha uses one stotra shloka per graha, not bija mantras. Ramcharitmanas has had one spot-check only.
- The sankalpa sentence was written for this project; have it checked.
- Not yet seen on the real system: failover to Muse, a 06:00 gathering, the daily Discord report, Cloudflare free-plan limits, a real phone.
