# melee · AI 大乱斗

**English** | [中文](README.zh-CN.md)

An arena where multiple LLMs compete on the same stage: pick an arena, set a topic, let them fight, watch their streams side by side in real time, vote and bet as a spectator, and read the result from human votes plus an AI judge.

**The goal is entertainment and emotional value, not model benchmarking.** Watching a model spectacularly trip over itself is part of the show.

## Screenshots

| Arena setup | Contestants & live preflight |
|---|---|
| ![Arena setup](docs/screenshots/01-arena-setup.png) | ![Lineup](docs/screenshots/02-lineup-preflight.png) |

| Battle in progress · live streaming | Report · human votes + AI judge |
|---|---|
| ![Battle live](docs/screenshots/04-battle-live.png) | ![Report](docs/screenshots/05-battle-report.png) |

| Hard-code · real sandbox test runs | Solo chat · skip the arena, just talk |
|---|---|
| ![Hard-code report](docs/screenshots/07-hardcode-report.png) | ![Solo chat](docs/screenshots/06-solo-chat.png) |

## Quick start

```bash
npm install && npm run dev
```

Open http://localhost:3000. The default sandbox is Pyodide (WASM) — no Docker needed.

## Arenas

- **Soft arena** — same topic, all contestants answer (debate / creative / roleplay). Human votes and an AI judge run as two parallel tracks — no composite score.
- **Debate** — two contestants, three rounds (opening → rebuttal → closing). Judged on logic / evidence / clash.
- **Banter battle** — 2–4 contestants, three rounds. Roles are assigned by seat; judged on hooks / sting / staying civil (crossing the line costs points).
- **Hard-code** — Python problems executed for real in a Pyodide WASM sandbox and scored by test pass rate (no LLM judge required).
- **Chess endgame** — white-to-move mate-in-N puzzles; moves validated with chess.js plus a forced-mate search.

Every arena supports **pre-match betting** — stake on a contestant before the battle ends; results are settled with public rules and your record is kept locally.

## Providers (bring your own key)

Keys are sent in the request body when a battle starts and live only in server memory for that battle — never written to the database, never logged.

Default pool: Ollama (local), DeepSeek, SiliconFlow, Zhipu GLM, Moonshot Kimi, Alibaba DashScope (directly reachable from mainland China); OpenAI / Anthropic / Google are marked "proxy required".

## Sandbox isolation & production notice

| Backend | Isolation level | Env |
|---|---|---|
| Pyodide (default) | WASM language-level sandbox | `SANDBOX_BACKEND=pyodide` |
| Judge0 CE (optional) | OS-level container isolation | `SANDBOX_BACKEND=judge0` |

> ⚠️ The default backend is **not** as strong as OS-level isolation. Before opening this to untrusted users on the public internet, **enable Judge0 (or an equivalent OS-level sandbox)** — otherwise keep it to local / personal / trusted use. See [design spec §6.2](docs/superpowers/specs/2026-10-09-ai-battle-arena-design.md) (Chinese).

## Tech stack

Next.js 16 (App Router / Turbopack) · Node 24 · Vercel AI SDK 7 · node:sqlite · Pyodide · Zustand · Zod · node:test

## Project structure

```
app/
  page.tsx                        Arena setup (home)
  battle/[id]/page.tsx            Spectator view
  api/battle/run                  Create a battle
  api/battle/[id]/stream          SSE event stream
  api/battle/[id]/vote            Voting
  api/battle/[id]/cancel          Cancel
  api/battle/[id]/snapshot        Reconnect snapshot
  api/providers/ping + models     Provider preflight & model list
lib/
  arena/        Arena abstraction and implementations (soft / debate / banter / hard-code / chess)
  battle/       Orchestration, event contract, in-memory registry
  providers/    Provider registry and AI SDK streaming adapter
  judge/        Judge with position-swapped double rounds
  db/           node:sqlite wrapper
  sandbox/      Sandbox interface + Pyodide / Judge0 backends
  log/          Log redaction
components/arena/   UI: setup / spectator / cards / scoreboard / voting / report
workers/            Pyodide worker entry
```

## Testing

```bash
npm test        # all node:test suites (includes real sandbox execution)
npm run build   # production build (type check + Turbopack)
```

## Configuration

Copy `.env.example` to `.env` and adjust as needed — every variable is documented inline.

## Documentation (Chinese)

- [Design spec (phase 1)](docs/superpowers/specs/2026-10-09-ai-battle-arena-design.md) · [Implementation plan](docs/superpowers/plans/2026-10-10-melee-phase1.md)
- Audits & engineering records: [project audit](docs/audit/2026-10-13-project-audit.md) · [official conformance](docs/audit/2026-10-13-official-conformance.md) · [orchestration & isolation](docs/audit/2026-10-13-orchestration-and-isolation.md) · [ecosystem adoption](docs/audit/2026-10-13-ecosystem-adoption-backlog.md) · [endpoint hardening](docs/audit/2026-10-15-endpoint-hardening.md)

## License

[MIT](LICENSE)
