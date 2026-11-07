# Roadmap

Every item here has a **checkable acceptance criterion**. If you cannot test it, it does not belong on this page. Finished work moves to [CHANGELOG](../CHANGELOG.md).

Versioning is [semantic](https://semver.org/): the current release line is **1.x**. A minor bump adds capability without breaking the event contract; a major bump would change the event schema or the API in a way existing clients cannot follow.

## Next (1.10)

| Item | Why | Acceptance |
|---|---|---|
| Event-stream pagination and compression | Long matches reach thousands of events; replay pulls all of them | `after` cursor + server-side gzip; a 5000-event match renders its first screen in under 1s |
| Distributed tracing | Only request-level metrics exist; slow requests across worker/queue cannot be located | An OpenTelemetry trace_id follows HTTP → queue → runner and a single match's full path is visible in Jaeger |
| Mobile replay gestures | Only timeline dragging today | Pinch to zoom the board, swipe left/right to change match |
| Game types to 16 | Content volume drives retention | At least 4 new deterministic games, each with commentary/cuts/recap, automatically covered by contract tests |
| Webhook delivery log | Failed dispatches are only in server logs | Persisted delivery attempts with status, visible in the admin console, manual re-dispatch |
| Signed installer | Installer exists but is unsigned; Windows SmartScreen still warns | SignPath or equivalent certificate on `melee-setup.exe`; download runs without a warning |

## Explicitly out of scope

These are decisions, not backlog. Re-adding them needs a new rationale.

- Platform-side model tooling: browsing, file access, function calling, sub-agents, multi-step tool loops. That belongs in the caller's HTTPAgent service.
- Anonymous voting, site-wide Elo, multi-tenant accounts, billing.
- The full TextArena environment catalogue; we register a curated subset.
- Season points for multi-team formats — the standings model assumes two sides.

## Released

### 1.9.0 — 2026-11-07

| Item | Acceptance | Result |
|---|---|---|
| Light theme by default | Every page renders on a light canvas; dark is a toggle, not the only option | Design tokens in `theme.css`; no colour literal outside the token file; `test_theme.py` |
| No theme flash | The first painted frame is already the chosen theme | `theme-boot.js` synchronous and before the stylesheet; `test_every_page_boots_theme_before_styles_are_used` |
| Contrast gate | Secondary text readable in both themes | `check_contrast.py`, 26 combinations, WCAG AA; wired as gate 10b |
| Share card in both themes | `?theme=dark` returns a genuinely different image | `test_card_light_and_dark_are_different_images` (pixel brightness assertion) |
| Idempotent match creation | A retried POST cannot create a second match | `Idempotency-Key`; 6 tests incl. 422 on key reuse with a different body |
| Rate-limit headers | Clients can back off before being throttled | `RateLimit` / `RateLimit-Policy` on 200 and 429; 2 tests |
| Secure matches replayable | A match created in the default secure mode can be replayed from its log | Seed falls back to `matches.seed`; `test_rehydrate_works_when_seed_is_not_in_the_event_log` |
| All 12 games run end to end | Every game reaches a terminal state with commentary, cues, recap and highlights | 12/12 external run; each asserts narration count, cue count, empty `problems`, bilingual one-liner, export/import roundtrip |
| Leaderboard query is indexed | Home page does not degrade as the event table grows | `EXPLAIN QUERY PLAN` test; 4.84ms → 0.24ms |

### 1.8.4 — 2026-11-06

| Item | Acceptance | Result |
|---|---|---|
| External acceptance suite | Every promise verified by HTTP from outside | `scripts/acceptance.py`, 30 checks (35 as of 1.9.0) |
| Codenames never declares a non-participant the winner | `winner` is always an entrant | 40 games, 0 violations |
| Manual cue reason is localised | English UI shows English cue text | `cue.manual` message key; `triggerKey`-based downstream |

### 1.8.0 — 2026-11-04

| Item | Acceptance | Result |
|---|---|---|
| CLI + exe | One binary drives serve/query/match/season/MCP; portable zip installs and runs on a clean machine | `dist/melee-portable-win64.zip`; exe verified: health/games/12 games incl. TextArena othello 60-turn match/export-import roundtrip |
| Admin console | Health, queue, quota, scheduler visible and actionable at a glance | `admin.html` bilingual; frozen-route + i18n-audit coverage |
| Agent harness | MCP tools, Agent Card, signed webhooks | `melee mcp` 7 tools; `/.well-known/agent-card.json`; HMAC webhook 4 tests |
| Portable match file | Export/import single file, replay-only, fingerprinted | `portable.py`, 5 tests, CLI roundtrip verified in dev + exe |
| Scheduler auto-discovery | startAt seasons fire without env config | e2e: 2 fixtures auto-played in 45s; 4 tests |
| Registration-time probe | Broken TextArena installs cannot accept doomed matches | `env_buildable()` per env; catalogue degrades honestly |

### 1.7.0 — 2026-11-02

| Item | Acceptance | Result |
|---|---|---|
| Bilingual interface (English default, Chinese switch) | Every page renders both languages; a language-neutral message key lives in the event stream so a recorded match re-reads in either language | `scripts/audit_i18n.py` asserts 17 checks across 5 pages × 2 languages + 2 mobile views, 0 failures |
| Language-neutral narration and recaps | Commentary, recap one-liners, highlight titles, director cue triggers and end reasons all re-render per request language | `GET ...?lang=zh` and `Accept-Language` both work; events store `msg` + `params` with an English `text` fallback for old clients |
| Bilingual screenshot set | `docs/screenshots/{en,zh}/` both complete | 12 images per language, captured against a live service, script fails on any page error |
| Project-management documents | Roadmap has acceptance criteria per item; changelog follows Keep a Changelog | `docs/ROADMAP.md`, `CHANGELOG.md`, `docs/VERSIONING.md` |

### 1.6.0 — 2026-11-02

| Item | Acceptance | Result |
|---|---|---|
| Player capability surface | `ModelSpec` carries `systemPrompt`, `topP`, `seed`, `stop`, penalties, `stream`, `includeHistory`, `protocol`; `maxTokens` up to 32000 | `test_player_spec_fields.py`, `test_llm_sampling.py` |
| Turn wall clock | `config.turnTimeoutMs` (1–600s) enforced by the runner; timeout scores as a forfeit | `test_turn_timeout.py` |
| Credential references | `apiKey: {env:VAR}` resolved by the worker after unsealing; a missing variable forfeits by structured player id | `test_api_llm.py`, `test_llm_protocols.py` |
| Frontend advanced parameters | Quick-start modal configures protocol, sampling, prompt, self-memory, turn deadline | Manual + `audit_ui.py` |

### 1.5.0 — 2026-10-29

| Item | Acceptance | Result |
|---|---|---|
| Multi-team Codenames | `config: {teams, teamSize}`, 2–6 teams × 2–4 players, persisted in `match.created.config` | `test_codenames.py` |
| Fairness audit | Information isolation, first-move rotation, turn symmetry, independent resources, multi-team replay | `test_fairness.py` (59 checks), 40 games strictly 20/20 on first move |
| Leak fixes | Kuhn hand, GOPS hand, GOPS hidden card | `test_regressions.py` |
| Deadlock fixes | Othello pass, GOPS hand exhaustion, Connect Four column derivation | `test_resilience.py` |

### 1.4.0 — 2026-10-29

| Item | Acceptance | Result |
|---|---|---|
| Event `visibility` | At least one event carries `visibility: {players: [...]}`; SSE filters it for unauthorised viewers | Codenames captain thought marked private; `test_visibility.py` |
| Cross-replica viewer count | Redis mode reports a global `viewers` value | Redis counter + TTL; `scope` field on `/presence` |
| Game types to 12 | At least 3 new deterministic games with commentary/cuts/recap | Simple Tak / Wild Tic-tac-toe / Crusade; `test_games_parity.py` |
| Highlight templates | At least 3 layouts selectable by query parameter | `broadcast` / `spotlight` / `scorecard`; `test_render_templates.py` |
| Mobile directing | Select a match and cut at 390px | Hotkey block hidden, match list horizontally scrollable, cut bar pinned at 44px |
| Focus management | Tab cycles inside dialogs, focus returns to the trigger | `a11y.js` `trapFocus`, verified by `audit_ui.py` |

### 1.3.0 — 2026-10-27

| Item | Acceptance | Result |
|---|---|---|
| Event log as the single source of truth | Live and replay both derive from `reduce(events)` | `test_pipeline.py` |
| SSE with resume | Reconnect continues from the last `seq` | `test_streaming.py` |
| Recap fact-checking | Every number in a recap is counted from the event stream; mismatches are reported | `test_recap.py` |
| Share card and OG page | `/matches/{id}/card.png` and `/share/{id}` | CI smoke test asserts PNG magic bytes and `og:image` |

### 1.2.0 and earlier

See [CHANGELOG](../CHANGELOG.md) for 1.0–1.2.
