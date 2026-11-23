# Architecture

**English** · [简体中文](zh-CN/ARCHITECTURE.md)

## One sentence

The engine produces facts, the event log keeps them, and every surface recomputes from those facts. Live view, replay, recap, directing, highlights and the share card are different projections of the same stream.

## Layers

```
apps/web                 zero-build ES modules
  index / watch / season / recap / director / admin
  view.js: reduce(event) → view, shared by live and replay
        │ SSE / REST                     │ PNG
api.py + routes/          matches · season · media · director · share · system
                          auth · quota · rate limit · CORS/CSP · metrics
        │
runner / worker / season / control          narrator · director · recap · render · card
        │                                   (derived layer: never writes facts)
services/match/engine/    twenty_questions · codenames · textarena_adapter · connect_four
                          Game: init / observe / public_state / current_player / legal_moves / apply
        │
eventlog.py (SQLite append-only) · workqueue.py (leases) · broadcast.py (memory/Redis)
db.py (one SQLite factory) · language.py · clock.py · teams.py · font.py
auth.py · secrets.py · outbound.py · ratelimit.py
```

Dependencies point downward only. The engine does not import the log; the domain layer does not import HTTP. `api.py` is a composition root: middleware, lifespan, mounting. Endpoints live in `routes/*.py`, and `test_architecture.py` freezes the public route list so a refactor cannot silently change the API.

## Five invariants

**The log is the only truth.** `EventLog` only appends. `seq` starts at 0 and rises without holes; a primary-key collision retries with `MAX(seq)+1`, so “contiguous” holds even with the API, a worker and the director console writing at once. `assert_contiguous()` fails loudly in tests.

**Live is append, replay is prefix.** The front-end owns one `reduce(view, event)`. Live reduces each incoming event; replay reduces from 0 to the cursor. Same input, same function, same picture. Reproducibility tests lock this.

**Director decisions are events.** Automatic cuts and manual cuts are both `director.cue` events. Replay therefore shows the original camera sequence, and `apply_director()` can rebuild it offline, event by event. A manual cut reopens the stream in `control.py` and retries on `seq` conflict.

**The engine is a pure function.** `Game.apply(state, player, move)` reads no clock, opens no socket, uses no global randomness. Randomness appears once, in `init(seed)`. TextArena mutates its environment in place; the adapter deep-copies before every step, so a caller's state never changes underneath it. That is the only place where purity is translated.

**Statistics are counted, not invented.** `/archive` counts rows, `/stats` reads `match.ended` events, `/presence` reports real subscriptions, and the home chart plots real daily counts — empty days are zero, not noise.

## Key mechanisms

**Writes go through a queue.** `POST /matches` validates and enqueues. A worker takes a lease with a conditional `UPDATE` and renews it every `lease/3`. Single-node runs default to an inline worker: the API process consumes its own queue, so it works out of the box. If a lease is not renewed, a second replica may rerun a five-minute match and emit a second `match.ended`; the regression is covered by `test_workqueue.py`.

**Crash recovery replays first.** On retry the worker calls `rehydrate()`: unfinished state resumes; finished state missing `match.ended` gets `ensure_terminal()` and an `ended_at`. The old implementation skipped `finished` matches and left them “live” forever.

**Hidden information is trimmed at observation.** In Codenames the spymaster's `observe()` returns the key; operatives do not. `public_state()` exposes only revealed colours. Private events exist only for a spymaster's reasoning, carry `visibility: {players: [...]}`, and are filtered server-side on `/events`. `GET /view/as/{player}` is closed during live play; `GET /events?reveal=1` returns 403 until the match ends. “Not visible live, readable after” is enforced by the server, not by client politeness.

**Narration is fact-checked.** `LLMNarrator` passes three gates: shape (non-empty, short, no winner claims), numbers (every figure must exist in the fact JSON), and semantics (direction, player identity, action type). Any failure falls back to a rule-based line. Plain beats false.

**Multiple replicas.**

| Capability | Single node | Multiple replicas |
|---|---|---|
| Event log | SQLite WAL | Shared filesystem or another database (not supported today) |
| Broadcast | In memory | `MELEE_BROADCAST=redis` |
| Rate limit | In memory | `MELEE_RATELIMIT=redis` |
| Tickets | Per-process secret | `MELEE_TICKET_SECRET` (stateless signature) |
| Queue | Same database | Shared with the database |
| Viewer count | Per process | Each replica reports its own; not aggregated |

## Why these choices

| Decision | Reason | Cost |
|---|---|---|
| SQLite, not Postgres | Zero-dependency self-hosting; WAL handles API + worker + director writes | Multi-replica needs shared storage |
| Zero-build frontend | Player state is events plus a cursor; a reducer is enough. Looks come from typography and pacing, not a component tree | Complex forms get wordy |
| lit-html, not React | 8 KB, auto-escaping templates, no build | Smaller ecosystem |
| Built-in Twenty Questions and Codenames | Rules are the product; TextArena's Codenames needs NLTK corpora and is not offline | Only two built-in games |
| TextArena adapter | Mature deterministic rules; no need to rebuild them | Bound to upstream observation format |
| No Kafka | Append-only plus `seq` is the product contract; SQLite is the most controllable writer | Single-node ceiling |

## Frontend

```
index.html      hero, live strip, archive, game catalogue, stats
watch.html      1600×900 stage, side panels, timeline, controls, theater mode
season.html     standings, fixtures, rivalries, create/advance
recap.html      one-liner, numbers, moments, player stats, highlights
director.html   match picker, shots, cuts, live events
src/
  api.js        REST client, formatting, game metadata
  nav.js        shared top bar and footer
  paint.js      clears static placeholders before first paint, then lit-html
  view.js       events → view, shared by live and replay
  sse.js        EventSource wrapper with ticket exchange and cursor resume
  watch/        player.js (state machine), render.js (state → DOM), timeline.js (pure functions)
```

`main.js` only wires the page: data through `api.js`, stream through `sse.js`, “which event” through `player.js`, “what it looks like” through `render.js`, “where the cursor lands” through `timeline.js`.

The timeline maps events to real time (`at`), not to sequence numbers. In Twenty Questions, questions and thought fragments differ by an order of magnitude; a sequence-based scrubber would crawl through the first ninety percent and jump through the rest. Missing or identical timestamps fall back to index interpolation. Cue markers that overlap in pixels are merged (the final one wins), because otherwise the buttons intercept each other and none can be clicked — a browser audit caught that.

## Data model

```sql
matches(match_id PK, game, seed, players JSON, created_at, ended_at)
events(match_id, seq, type, player, at, payload JSON, PK(match_id, seq))
jobs(job_id PK, kind, payload JSON, status, attempts, max_attempts,
     lease_owner, lease_until, last_error, created_at, updated_at)
seasons(season_id PK, name, created_at, game, status)
rosters(season_id, player_id, display, persona, model, PK(season_id, player_id))
fixtures(season_id, round, match_id, player_a, player_b, scheduled_at,
         PK(season_id, round, player_a, player_b))
results(season_id, match_id PK, player_a, player_b, winner, reason)
quotas(token_name, day, matches, renders, PK(token_name, day))
```

`seasons.game` and `seasons.status` were added later; `season.py::_migrate()` checks `PRAGMA table_info` and runs `ALTER TABLE`, so an old database upgrades without breaking.
