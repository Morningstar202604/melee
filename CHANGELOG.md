# Changelog

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow [SemVer](https://semver.org/).

The full history, including the early 0.x builds, lives in the [Chinese archive](docs/zh-CN/CHANGELOG.md). This file keeps the releases a reader is most likely to care about.

## [1.10.3] — 2026-11-24

### Fixed

- **Imported live snapshots are read-only at the API boundary.** `POST /matches/{id}/coach` used to accept commands as long as the imported match had no end event, even though the 2D/3D coach forms were hidden. The endpoint now rejects any imported episode with `409`, not just finished replays, so a direct API call cannot write into an external arena's live facts.

## [1.10.2] — 2026-11-24

### Changed

- **The 3D trace window is incremental.** Live events are appended to a bounded 20-item trace cache instead of re-filtering the full event history on every tick; thought updates no longer rebuild the play-by-play list.
- **Long reasoning can be read in full.** Thoughts longer than two lines get a `Show full thinking` control; expanding a card removes the clamp and lets the panel scroll through the complete model output.

### Fixed

- **Structured actions no longer print `[object Object]`.** Codenames/QA style action objects are flattened into key/value text for the timeline.
## [1.10.1] — 2026-11-24

### Added

- **AI trace panel in the 3D arena.** The left deck now shows a live thinking stream (`lastThoughts`, accumulated per turn) and a play-by-play rail for turns, proposed/applied actions, coach deliveries, narration and the final result. Long thoughts are no longer reduced to the last streamed chunk.
- **Coach onboarding.** The live coach form carries a one-line instruction guide; the empty trace states explain what will appear before sending.

### Changed

- The 2D player cards and the 3D trace both read the complete per-turn thought buffer, so streamed reasoning stays readable as one paragraph.
- Event reads and SSE subscriptions now pass the active language (`lang`) explicitly, keeping live commentary in the same language as the UI.

### Fixed

- Live SSE commentary no longer falls back to English after the initial localized snapshot; the stream URL carries the same language as the page.
- The 3D trace keeps turn deltas and coach commands in sequence order, so a coach delivery is visible next to the action it preceded.

## [1.10.0] — 2026-11-24

### Added

- **Coach deck.** Live matches accept `POST /matches/{id}/coach` with a target and a 280-character instruction. Commands are validated, persisted in `coach_messages`, delivered at most once before the next model call, injected as `coachInstruction`, and recorded as `queued`/`delivered` `coach.message` events. The 2D watch page and the 3D arena share the same endpoint; imported replays stay read-only.
- **3D arena.** `arena3d.html` renders the same event stream with Three.js: board tiles, piece movement/capture tweens, player pods, broadcast/top/side/free cameras, auto orbit, a holographic stage for boardless games, SSE deltas and a clear WebGL fallback.
- **Prompt protocols.** Every registered game gets a scene protocol appended to the LLM system prompt (objective, information boundary, action constraints, JSON output contract); unknown games fall back to a safe generic protocol. Coach instructions get their own guardrail block.
- **Concurrent stress harness.** `scripts/stress_arena.py` creates matches across all registered games, tracks completion/stuck counts and writes a machine-readable report. A 24-match run and a 48-match run each covered 12/12 games with 0 stuck; raw reports live in `docs/benchmarks/`.
- **Baseline and research.** Four-module `docs/PROJECT-BASELINE.md`, `docs/zh-CN/PROJECT-BASELINE.md` and `docs/RESEARCH-2026-11-24.md` record provenance, competitive boundaries and the 3D feasibility path.

### Changed

- **CSP for the arena import map.** The page uses a SHA-256 allow-list for its inline import map instead of `unsafe-inline`; a static test keeps the hash in sync.
- **3D colours come from tokens.** `arena3d.js` resolves its palette from `arena3d.css` custom properties, so the theme test no longer needs an exception for the arena.
- **Long sprite labels** expand their canvas to the measured text width; names such as `GPT-5.5` no longer clip.

### Fixed

- The 3D first frame now builds tiles, stage and coordinates even when the match starts on an 8×8 board (the old `stageSize = 8` guard skipped `rebuildBoard`).
- Boardless games no longer show an empty chess grid; they get a framed holographic stage instead.
- The 3D panel receives pointer events, so the coach form is clickable rather than blocked by the WebGL canvas.

## [1.9.10] — 2026-11-24

### Added

- **Standard PGN export.** `GET /matches/{id}/export?format=pgn` and `melee match export --format pgn` write a standard PGN for Lichess/Stockfish/ChessBase, with provenance, result, and per-move thought comments. The runtime stays GPL-free; the test suite reads the output back with `python-chess`.
- **Dataset resolver.** `melee match import-batch --dataset kaggle/chess-text-gameplay` uses `kagglehub` to resolve/download the dataset and fills `sourceUrl`/`sourceLabel` automatically.
- **Import-aware home.** A library that contains only imported games now fetches `?source=import` and renders the imported activity chart and leaderboard, instead of an empty "no results" state.
- **Broadcast surfaces.** `GET /matches/{id}/scene` exposes a compact, replica-independent snapshot for OBS and third-party lower-thirds; `/embed/{id}` wraps the watch page without product chrome; `/overlay.html` is a transparent browser source with board, last move and material balance. Cross-site framing is opt-in through `MELEE_EMBED_ORIGINS`.

### Changed

- **Hero preview is live data.** The homepage poster no longer hard-codes fictional players; it is filled from the first live match and links to its watch page.
- **Licence ledger cleaned.** `NOTICE` is split into runtime and dev-only tables; `python-chess` is a test-only GPL dependency, `kagglehub` is recorded as Apache-2.0.

## [1.9.9] — 2026-11-23

### Added

- **Live external episodes.** `POST /matches/import-episode/live` creates a match from the first external snapshot and appends only new steps as the arena pushes updates; `finished: true` closes it once. Frames are prefix-stable (rewinds return 409), duplicates are idempotent, and `GET /matches/{id}/state` stays live without re-running an engine.
- **Idempotent replay import.** `matches.source/source_id` and a partial unique index make `(source, episodeId)` one match; re-imports return the existing `matchId`, and duplicate API calls do not consume another quota slot. Historical imported rows are backfilled from `match.created.config`.
- **Batch import through the API.** `--via-api` posts each episode through the authenticated HTTP entry, with quota and rate limits; daily quota exhaustion stops the batch immediately instead of retrying 100 times. `--sample hash` is the default, so a 100-game slice is spread across models instead of taking the first filenames.
- **Provenance and board semantics.** Imports record `license`, `sourceUrl`, `sourceLabel`, `played_at` and show them on the watch page; chess boards get a–h/1–8 coordinates and last-move from/to highlighting. Numeric `actionHistory` is recovered to SAN by comparing consecutive FENs with optional `python-chess` (no `pyspiel` required).
- **Resource bounds.** 10,000 steps, 60,000 events, 8MB input and 2,000-character thoughts; batch rejects oversized files and reports `bytes`.

The 1.9.9 demo imports 100 real `kaggle/chess-text-gameplay` episodes through the authenticated API: 100 imported / 0 duplicate / 0 failed, 8,265 moves, 42,466 events, 40 distinct players and 18 draws. A 34-move live snapshot is then pushed through the live bridge (206 events), giving a 101-match demo database with 42,672 events.

### Changed

- Site statistics separate imports from the domestic ladder: `topWinners` and 14-day `activity` only count `source='melee'`; `importedMatches`, `importedActivity` and `?source=import` expose the import side. Activity never treats import time as game time.
- The home library marks imported cards, uses `playedAt` for the timestamp, and explains why imported wins are absent from the leaderboard.
- `MELEE_RATELIMIT=off` explicitly disables per-IP windows for trusted offline batch runs; token auth and daily quotas stay on.

## [1.9.8] — 2026-11-23

### Added

- Real Kaggle dataset support. `kaggle` import unwraps the dataset file's `root`, reads `steps[*].action.actionString` for readable SAN moves, `info.stateHistory` for the board after every move, `observation.playerId` for the acting seat and `rewards` for the winner/draw.
- `melee match import-batch` imports a directory/glob of episodes, keeps going after individual failures and writes a per-file JSON report. The first 100 real `kaggle/chess-text-gameplay` episodes imported in one run: 100/100, 9,007 moves, 13 draws.
- Model reasoning from Kaggle `generate_returns[*].main_response` becomes `thought.delta`, so imported games show what the player was thinking at each move.
- `GET /matches/{id}/clips` now localizes clip titles and details with `?lang=` / `Accept-Language`; the watch-page player name no longer squeezes the status badge into a vertical column when a model name is long.

## [1.9.7] — 2026-11-23

### Added

- External episode import. `POST /matches/import-episode` and `melee match import-episode` accept `melee.episode/1`, Kaggle Game Arena episodes and TextArena `turns` snapshots; unmatched games such as chess or Go replay through the same watch/recap surface without re-running an engine.
- Imported episodes are replay-only: facts become melee events, `/state` projects the last public snapshot, and the original reason is preserved in `finalState.sourceReason` while the public reason stays localizable.
- `docs/EPISODES.md` documents the neutral schema, the Kaggle/TextArena adapters and the CLI/HTTP entry points; `examples/episodes/` carries runnable tic-tac-toe and Kaggle chess samples.

## [1.9.6] — 2026-11-22

### Changed

- Every legal move result now goes through `engine.base.legal_result()`; the three engines no longer rebuild `MoveResult(next_state, effects, public_state(...))` in each branch.
- Event-log construction, insertion and broadcast live in `EventLog._new_event()` / `_insert_event()` / `_notify()`; `append()` and `append_terminal()` share them instead of copying the row and retry tail.
- `db.open_db()` adds the missing connection lifecycle context manager; routes now close connections without hand-written `try/finally`.
- Card and render text wrapping share `textfit.wrap_text()`; display-width and newline handling are parameters rather than two loops.
- Watch timeline construction shares `makeTimeline()` between full build and incremental append.
- Match and season creation share `routes.common.validate_players()`; unsupported game, duplicate id and roster size are checked once.
- Demo rosters live in `scripts/_demo.py`; seed, screenshot, walkthrough and audit scripts stop maintaining four copies of the same names.

## [1.9.5] — 2026-11-22

### Changed

- Player agents no longer carry private redaction copies: `http_agent` and `llm` use `base.redact_sensitive_text`, and HTTP failure text goes through `agents/errors.py`.
- Request retries for every player agent now share `agents/retry.py`; budget checks, retryable status and linear backoff are defined once instead of three times.
- SSE and media-download authentication share `routes.common.ticket_principal()`: ticket first, Bearer fallback, same 401/403 translation.
- SSE and media tickets share one serializer reader in `auth.py`, with the media match binding as a parameter rather than a second copy of the expiry checks.
- BYOK and external-HTTP outbound validation share `player_specs._reject_internal()`.
- Poker helpers read "your card / your hand" through one `_own_value()` lookup; the card and hand readers no longer repeat the seat-index fallback.
- Read-only CLI commands share `_fetch()`, so token, language, timeout and non-200 handling are written once.

## [1.9.4] — 2026-11-22

### Changed

- One version source. `services/match/version.py` now feeds the API, CLI, A2A card and both packaging scripts; the A2A card (1.8.0) and installer (1.9.2) can no longer drift behind the release.
- One SQLite connection factory. The 23 direct `sqlite3.connect` sites and four private `_connect` helpers now go through `db.connect()`, so timeout, row factory and thread flags are explicit instead of per-module folklore.
- Language, clock and team labels have single homes: `language.py` (supported languages, fallback, `Accept-Language`), `clock.py` (UTC ISO timestamps), `teams.py` (reference keys and Chinese labels, including dynamic `teamN`).
- The three engines share `base.ensure_turn()`; Connect Four's column parser lives in `engine/connect_four.py`, and the archive list/detail queries share one event-column subquery.
- Media font loading moved to `font.py`, and the share-card / social-preview color palette to `palette.py`; neither script re-declares the light-theme colors.
- Front-end share, error bar and page bootstrap live in `ui.js`; the five pages no longer copy the same startup sequence.
- Demo and gate scripts share `scripts/_harness.py` (free port, health wait, JSON client, browser launch) instead of seven local copies.
- The "no legal moves" observation rule lives in `engine/observations.py`; the adapter and scripted player no longer keep two copies.
- Clip ticket fallback is one `api.clipTicket()` helper; tests share `support.wait_job()` / `support.wait_ended()`.

## [1.9.3] — 2026-11-22

### Added

- Response compression for JSON and HTML (gzip above 1 KB). SSE is deliberately excluded, so live matches still arrive event by event instead of in buffered chunks.
- The watch page now paints from the most recent window of the event log instead of waiting for the whole match. The remaining events stream in from the window's last `seq`; long matches no longer open on an empty stage.

### Changed

- Player and season demo data can be seeded in English or Chinese, and the walkthrough recorder follows the same switch. Screenshots and video no longer mix languages.
- Documentation is English-first. Every core document carries an `English | 简体中文` switch at the top; the Chinese set lives in `docs/zh-CN/`.

### Fixed

- `docs/` no longer mixes current guides with old audit reports. Historical reviews moved to `docs/archive/` and are clearly marked as point-in-time records.

## [1.9.2] — 2026-11-08

### Added

- Webhook delivery log: every attempt stored with status, HTTP code, attempt count, readable error and a payload digest. Bounded to the latest 500 rows and 30 days.
- `GET /webhooks/deliveries` and `POST /webhooks/redispatch`; admin table with a retry button; CLI `melee webhooks --redispatch`.

### Fixed

- Webhook tests no longer share receiver state across cases; the receiver thread is joined and cleared on teardown.

## [1.9.1] — 2026-11-08

### Fixed

- Oversized request bodies return a readable JSON 413 instead of a reset connection.
- Imports are atomic: validate everything, then write in one transaction. Errors name the offending element.
- SSE reconnect backfill loses no events; event pagination is pushed down to SQL; migrations refuse to open a newer database.

## [1.9.0] — 2026-11-07

### Added

- Light studio theme is the default; dark is a toggle. Contrast gate covers both themes.
- Idempotent match creation via `Idempotency-Key`; rate-limit headers on 200 and 429.
- Secure matches can be replayed from their log even when the seed is not published.

## Earlier releases

| Line | Theme | Where to read |
|---|---|---|
| 1.8.x | CLI, packaged exe, admin console, agent harness | [Chinese archive](docs/zh-CN/CHANGELOG.md) |
| 1.7.x | Bilingual interface and language-neutral event narration | [Chinese archive](docs/zh-CN/CHANGELOG.md) |
| 1.6.x | Player capability surface, turn wall clock, credential references | [Chinese archive](docs/zh-CN/CHANGELOG.md) |
| 1.5.x | Multi-team Codenames, fairness audit, leak and deadlock fixes | [Chinese archive](docs/zh-CN/CHANGELOG.md) |
| 1.4.x | Event visibility, highlight templates, twelve game types | [Chinese archive](docs/zh-CN/CHANGELOG.md) |
| 1.3.x | Event log as the single source of truth, SSE resume, share card | [Chinese archive](docs/zh-CN/CHANGELOG.md) |
| 1.0–1.2 | First broadcast pipeline and replay | [Chinese archive](docs/zh-CN/CHANGELOG.md) |
