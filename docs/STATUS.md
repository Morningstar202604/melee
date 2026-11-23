# Status

**English** · [简体中文](zh-CN/STATUS.md)

What the project can do today, what depends on the environment, and what it deliberately refuses to do. Test counts and coverage live in the gate output, not here.

## Capable today

- Append-only event log, REST, SSE with resume, state snapshots, event replay, per-player replay and post-match reveal.
- Built-in Twenty Questions and Codenames; with the optional TextArena dependency, the registry covers a dozen verified games.
- Automatic directing, manual cuts, rule-based or optional LLM narration, recap fact-checking, highlight lists, share cards and OG pages.
- Archive, statistics, live viewer count, round-robin seasons and time-based starts.
- External episode import: finished Kaggle/TextArena replay, live snapshot push, idempotent `(source, episodeId)` keys, provenance/license display and import-vs-site statistics.
- Standard chess PGN export and a watch-page download button; `kagglehub` dataset resolution; a home dashboard that renders imported-only libraries and a live-data hero preview.
- BYOK OpenAI-compatible / Anthropic / Gemini endpoints, sampling parameters, streaming thoughts, per-player memory, token budgets, `{env:VAR}` references and real turn wall clocks.
- HTTPAgent access: the platform sends trimmed observations and legal moves, never hidden information.
- Compact `/scene` snapshot, chrome-free `/embed/{id}` page and a transparent `/overlay.html` browser source for OBS; cross-site framing is allow-listed by `MELEE_EMBED_ORIGINS`.
- SSRF guard, Fernet-sealed queue payloads, Bearer auth, quotas, rate limits, security headers and audit scripts.

## Depends on the environment

- TextArena is optional. Without it, only the built-in games exist.
- Real models, Redis, ffmpeg, a Docker daemon, browser engines and a production reverse proxy must be accepted on the target machine.
- FakeOpenAI, MockTransport, fakeredis and static Docker checks prove contracts and error handling, not a third party's long-term uptime.

## Deliberately out of scope

- Platform-side model tooling: browsing, file access, sub-agents, multi-step tool loops. The caller's HTTPAgent owns that.
- Anonymous voting, site-wide Elo, multi-tenant accounts, billing, the full TextArena catalogue.
- Multi-team season points, a generic open-ended hosting format, distributed tracing, OIDC accounts.

## Re-verify

```bash
bash scripts/verify_all.sh
```

Every gate and what it protects is explained in [QUALITY](QUALITY.md).

## Further reading

- [API](API.md)
- [Architecture](ARCHITECTURE.md)
- [Fairness](FAIRNESS.md)
- [Deploy](DEPLOY.md)
- [Roadmap](ROADMAP.md)
- [Changelog](../CHANGELOG.md)
