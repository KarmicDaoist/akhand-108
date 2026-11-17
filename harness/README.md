# Akhand harness

108 agents recite the library around the clock, verify every verse against the book, and report to the relay.
Python 3.12, asyncio, `httpx` and `websockets` only. Contract: `docs/spec.md` (Harness behaviour, Contract 1, Contract 2).

## Layout

| File | Job |
| --- | --- |
| `akhand/config.py` | all env settings, one frozen `Config` |
| `akhand/library.py` | loads and validates `library.json` (Contract 1) |
| `akhand/verify.py`, `parse.py` | normalisation, and the incremental reply parser that counts only exact verses |
| `akhand/ledger.py` | SQLite (WAL): cursors, counts, spend, stuck verses, gathering, control table |
| `akhand/modes.py` | per-station plans: parallel, relay (shared cursor, one seat), japa, japa-per-seat, gathering |
| `akhand/provider.py` | OpenAI-compatible provider (SSE, non-streaming fallback), fake provider |
| `akhand/budget.py` | dollar budget (month and rolling 7 days) with reserve/settle and pacing |
| `akhand/breaker.py`, `router.py` | per-provider circuit breaker, free-provider order, cooldowns, probes, auth/model rest |
| `akhand/events.py` | per-second batches, per-agent pacing |
| `akhand/relay.py` | WebSocket client, reconnects forever |
| `akhand/harness.py` | wires it together: agents, gathering, control file, status export, watchdog |
| `akhand/status.py`, `ctl.py` | the status document, and `python -m akhand.ctl status [--json] / pause / resume / stop / sankalpa [--date]` |
| `akhand/panchang.py` | stdlib panchang at local sunrise: tithi, nakshatra, vara, masa (both systems, adhika), ritu, ayana, Vikram/Shaka year, samvatsara, with Devanagari names |
| `akhand/sankalpa.py`, `sankalpa/template.txt` | the full sankalpa: validation, rendering, the editable template |
| `akhand/contract.py` | strict validator for `batch` and `state` messages (tests and soak) |
| `tools/soak.py` | the end-to-end soak run with a dummy relay |

## Run

```
cp .env.example .env        # fill OPENCODE_GO_API_KEY, RELAY_URL, RELAY_TOKEN
docker compose up -d --build
docker compose exec akhand python -m akhand.ctl status --json
```

Offline demo, no key and no network: `PROVIDER=fake DATA_DIR=./data LIBRARY_PATH=tests/fixtures/library.json python -m akhand.main`.

Host prerequisites (compose bind-mounts them): `../library` (the library, read-only) and the export folder (`EXPORT_DIR` in `.env`, default `/var/lib/hermes/data/akhand`; writable by uid 10001; the status file is written 0644 so Hermes can read it).

## How it behaves

* **Request**: one call asks for the next `VERSES_PER_CALL` verses (24, per model overridable) of an agent, or `JAPA_REPS` (27) repetitions, or the rest of a relay seat's turn. The models bill reasoning as output, so calls are batched. A relay seat's turn stays `RELAY_TURN_VERSES` (8) so handovers stay frequent.
* **Verify**: each completed block is compared, after normalisation (NFC; drop whitespace, ZWJ/ZWNJ, dandas, digits, punctuation), with the next expected verse. A match is committed to SQLite in one transaction (cursor, agent total, day, station, all-time, streak), and only then queued for the relay.
* **Cost**: `usage.opencode.allowance` when the API sends it, else tokens times the configured price. A backup call reserves `BACKUP_RESERVE_USD` (0.0003) first and is settled to the real charge afterwards. Month cap is the local calendar month, week cap is rolling 7 days.
* **Providers**: `FREE_MODELS` (default `iceman-contributor-free,beam-free,longcat-2.5-free`) are tried in order, then the paid backup (`BACKUP_*`, budget logic unchanged). Each request goes to the first free provider that is not cooling down, failing or resting; if none, to the paid backup when the budget has room; otherwise the harness rests. `MAX_CONCURRENCY` and the per-agent pace apply across all free providers together. Short names are the model name without `-free`, `-contributor` and version (`iceman`, `beam`, `longcat`). Per-model overrides: `FREE_<NAME>_<FIELD>` or `FREE_<N>_<FIELD>` (N counts from 1, name wins) for `BASE_URL`, `API_KEY`, `REASONING_EFFORT` (`none` = send no field), `STREAM`, `TIMEOUT_S`, `VERSES_PER_CALL`. Defaults: iceman effort `low`, beam and longcat send no `reasoning_effort`, longcat timeout 180 s. The old `PRIMARY_*` names still configure the first free model.
* **Cooldown**: a 429 with `Retry-After` over 120 s (seconds or HTTP date, capped at 26 h) puts that provider in cooldown until that time; the request's verses go to the next provider at once (no backoff) and the cooled provider gets no call until the time passes. Then exactly one probe request; success clears the cooldown, failure waits 60 s and probes again. The cooldown-until time is stored in the ledger (`meta` keys `cooldown:<name>`), so a restart does not hammer a provider that is still cooling down. Without a long `Retry-After` the old behaviour applies per provider.
* **Failover**: each free provider has its own breaker: it trips when more than half of at least `BREAKER_MIN_CALLS` (20) calls in `BREAKER_WINDOW_S` (60 s) failed (error, or no verified verse). It is probed every `PROBE_INTERVAL_S` (5 minutes) with one real request and returns when a probe verifies.
* **Resting**: no usable provider, paused, or nothing verified for `RESTING_AFTER_S`.
* **Hermes**: every 30 s the same JSON as `ctl status --json` is written atomically to `STATUS_EXPORT_PATH` (with `exported_at`, epoch seconds; treat older than ~90 s as stale). Every 10 s `CONTROL_PATH` is read for `{"command":"pause"|"resume","id":"..."}`; each id applies once (last id kept in the ledger). `ctl` exit codes: 0 healthy, 1 degraded, 2 down.
* **Watchdog**: if praying, a provider looks healthy, and no unit was verified for `WATCHDOG_MIN` (15) minutes, the process logs why and exits with code 4 so Docker restarts it.
* Exit codes: 0 clean stop, 3 another instance holds the lock, 4 watchdog, 5 config or library problem.

## Full sankalpa

Off by default (`SANKALPA_MODE=simple`: the one fixed sentence, exactly as before). With `SANKALPA_MODE=full` the gathering opens with the lines of `sankalpa/template.txt`, one clause per line, each recited and verified as its own verse (same exact-match and stuck rules as every other verse). The text is meant to be corrected by the purohit: edit the file (in Docker, bind-mount a corrected copy over `/app/sankalpa/template.txt`, or point `SANKALPA_TEMPLATE_PATH` at it) and restart. Blank lines and lines starting with `#` are ignored; keep each line under about 300 characters.

Placeholders: `{samvatsara} {vikram_year} {ayana} {ritu} {masa} {paksha} {tithi} {vara} {nakshatra}` come from the panchang (Devanagari, already in the locative form, e.g. `प्रतिपदायां तिथौ`, `सोमवासरे`, `कार्तिकमासे`, `शुक्लपक्षे`, `शरदृतौ`, `दक्षिणायने`; `{samvatsara}` is the bare name and the template adds `संवत्सरे`; `{vikram_year}` is read digit by digit in words because the verifier ignores digits, and the default template does not use it). `{gotra} {name} {city} {state} {purpose} {act}` come from `SANKALPA_GOTRA_TEXT`, `SANKALPA_NAME_TEXT`, `SANKALPA_CITY_TEXT`, `SANKALPA_STATE_TEXT`, `SANKALPA_PURPOSE_TEXT`, `SANKALPA_ACT_TEXT`; give them ready-inflected, nothing is inflected for you. Place and conventions: `PANCHANG_LAT`, `PANCHANG_LON` (required), `PANCHANG_MASA_SYSTEM` (`purnimanta` default, or `amanta`), `PANCHANG_SAMVATSARA` (`north` default, or `south`), `PANCHANG_AYANA` (`drik` default, or `sidereal`). `TZ` must be the place's timezone.

The sankalpa is rendered once per gathering day, when the gathering starts, from the panchang at that day's local sunrise for the place. Startup checks everything (template readable, no unknown placeholder, every value the template uses is set, coordinates valid, conventions valid, a trial render for today, no line over 320 characters). If anything fails the harness logs `sankalpa: falling back to SIMPLE mode ...`, recites the simple sentence, and `ctl status` shows `sankalpa.mode: simple` with `requested` and `error`; it never recites a half-filled sankalpa. `python -m akhand.ctl sankalpa [--date YYYY-MM-DD]` prints the rendered sankalpa and the panchang in plain words for any date, without the main process, and lists the problems if the full sankalpa is not usable. `ctl status` carries `sankalpa: {mode, date, tithi, vara, nakshatra, masa, paksha, near_boundary}` (the elements are null in simple mode).

How the panchang is worked out (`akhand/panchang.py`, stdlib only): Sun from Meeus ch. 25, Moon from the main terms of Meeus ch. 47 (about 0.01 degree, so tithi and nakshatra boundaries are right to a minute or two; checked against published end times), sidereal values with the Lahiri ayanamsa, sunrise as the upper limb with 34' refraction at sea level (published sunrises are 0 to 1 minute later). Tithi = Moon minus Sun in 12 degree steps at sunrise, nakshatra = sidereal Moon in 13 deg 20' steps. Lunar month = named by the sidereal sign the Sun is in at the month's starting new moon (Meena gives Chaitra); same sign at the ending new moon means adhika. Purnimanta gives the dark fortnight the next amanta month's name, except in an adhika month, which keeps its name in both fortnights (as drikpanchang does). Ritu follows the amanta month (Chaitra-Vaishakha Vasanta ... Magha-Phalguna Shishira), the "Vedic Ritu" of published panchangs. Ayana `drik` is the tropical Sun (uttarayana from the December solstice), `sidereal` the Makara and Karka sankranti. Vikram year (Chaitradi) turns at the Chaitra new moon and, as in published panchangs, the whole civil day on which that new moon falls already shows the new year; Shaka = Vikram - 135. Samvatsara: north = `(Vikram + 10) mod 60`, south = `(Shaka + 12) mod 60`, 1 = Prabhava. `near_boundary` is true when sunrise is within 15 minutes of a tithi or nakshatra change (then the printed value could differ from a panchang computed with slightly different constants). Not handled: kshaya months, and the Jovian (Brihaspati) samvatsara that some panchangs print separately.

## Edge cases

| Case | Handling |
| --- | --- |
| Model adds commentary or numbering | The parser works line by line. Numbers and punctuation vanish in normalisation, a label line glued to a verse is skipped by matching the run of lines that equals the verse, separate commentary blocks are noise. Noise never moves the cursor. |
| Wrong word | The block fails the exact comparison, so it is not counted. The stream is stopped (free model) or read to the end for exact usage (paid model); no later verse in that reply is counted, because order is strict. One correction is recorded and the verse is asked again. The third failure in a row records it in `stuck_verses`, skips it, and reports it in status. |
| Truncated stream | Verses already verified stay committed. A cut-off partial block is dropped, not judged, so it is not a verse failure. A stream with no finish marker and no usage is a `truncated` error; zero progress means backoff and a breaker failure. |
| Timeout | 120 s per request overall (`REQUEST_TIMEOUT_S`), 60 s between chunks. Same as truncated: committed verses stay, error kind `timeout`, backoff with jitter. A paid call that timed out is charged from the text received. |
| 429 | `rate_limit`: with `Retry-After` over 120 s the provider is cooled down (see Cooldown). Otherwise per-agent exponential backoff (2 s up to 300 s, jitter), at least `Retry-After` (up to 600 s). Counts toward that provider's breaker. |
| 401/403 (and 402) | `auth`: that provider rests for `AUTH_REST_S` (15 min), requests already queued for it are dropped without a call, the next provider is used. Both resting: mode `resting`, no calls at all; after the rest, one probe request. No retry storm. |
| Model removed (404, or 400 naming the model) | `model_unavailable`: handled exactly like auth, so the next provider takes over. |
| Empty reply | Treated as a provider failure (`malformed`), not as a failed verse, so a flaky empty answer never skips a verse. |
| Relay down for hours | Prayer does not wait. Batches older than 10 s are dropped (the ledger still counts them; the page misses sparks, not units). Reconnects with backoff up to 60 s, forever. Status shows degraded. State is resent on reconnect. |
| Restart or kill mid-request | The unit commit is one SQLite transaction, so a verse is either fully counted or not at all. The in-flight request is simply gone and the agent resumes from its committed cursor. Relay seat, turn position and gathering progress are in the ledger too. A reservation already written for a backup call stays charged at the estimate (conservative). |
| Day rollover at local midnight | Day is the `TZ` local date (zoneinfo), taken at each commit. Counters are per day, so the new day starts at zero by itself; gathering has its own per-day record and cursors. A gathering crossing midnight keeps its start day. |
| Gathering missed while down | On start, if today's `GATHERING_TIME` has passed and no record exists for today, it runs once. A started but unfinished gathering resumes from each agent's cursor. Days missed earlier are not made up. It does not start while no provider is usable (so the day is not burnt), and ends incomplete after `GATHERING_MAX_MIN`. |
| Budget exhausted mid-request | Cannot overrun: room is checked and reserved atomically before the call. If the real charge exceeds the 0.0003 reservation it is recorded as is (a few in-flight calls could overshoot a cap by that small excess). When there is no room the backup is off and the harness rests. |
| SQLite busy | `busy_timeout` 5 s plus 6 short retries. If still locked the unit is not counted, the request is abandoned and tried again later (nothing is lost, nothing double counted). `ctl` writes are single tiny transactions. |
| SIGTERM | Agents are cancelled; verified verses are already committed, the in-flight one is not counted. The last partial second of events is flushed to the relay, `runtime` is cleared so `ctl status` says down at once, exit 0. `ctl stop` does the same without a signal. |
| Two processes started | Exclusive lock on `DATA_DIR/akhand.lock` (released by the OS when a process dies). The second one exits with code 3 and touches nothing. |
| Unusual Unicode (ZWJ, nukta, anusvara) | Both sides go through the same normalisation: NFC makes composed and decomposed nukta (U+095B vs U+091C U+093C) equal, ZWJ/ZWNJ are dropped. Anusvara versus candrabindu, or `ं` versus a nasal conjunct, are different spellings and are not equal; a library that spells it one way will fail if the model spells it the other way, then show up under stuck verses. |
| Clock jumps backwards | Intervals, pacing, breaker, silence and streak use the monotonic clock. The local day never goes back by up to 2 days (high-water mark kept in the ledger), and the month and week budget windows keep counting entries that are "in the future". A step of more than 2 days is trusted. |

## Tests and soak

```
python -m pytest harness/tests -q            # 40 tests, about 8 s, no network
python harness/tools/soak.py --seconds 60    # real main loop, fake model, dummy relay, hard kill, restart, then a quota run:
                                             # free models run out one by one (FAKE_QUOTA), the backup takes over
```

The panchang test compares 26 dates between March 2026 and May 2027 (purnima, amavasya, month changes in both systems, adhika Jyeshtha, both ayana changes, the samvatsara change, 14 Nov 2026) with drikpanchang.com's Raipur pages; another test checks that a full sankalpa with a missing value falls back to simple. Besides those, the tests cover only count and money correctness: verification, exact cursors across kills with one relay seat, the budget caps under concurrency, breaker failover, auth resting, and the 429 cooldown routing (next free provider at once, backup within budget, cooldown kept across a restart).

`ctl status` lists every provider under `providers` (`state` is `ok`, `cooldown`, `failing`, `auth` or `budget-dry`; plus calls, fails, last_error, `cooldown_until` as epoch or null). The relay `state` message keeps `provider` as `iceman` (any free model), `muse` (paid) or `none`, and adds the optional `model` (short name actually in use). Health is `healthy` on any free provider, `degraded` on the paid backup.
