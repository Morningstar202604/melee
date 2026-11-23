# Security

**English** · [简体中文](SECURITY.zh-CN.md)

## Reporting a vulnerability

Please use GitHub's [private vulnerability reporting](https://github.com/badhope/melee/security/advisories/new) on the repository. Do not open a public issue, and do not include a working exploit in a pull request.

Include what you can: the version (`melee --version`), the deployment shape, the request or configuration that triggers it, and what an attacker gains. A rough reproduction is more useful than a polished report.

Expect an acknowledgement within a few days. If the report is valid, the fix ships as a patch release from `main`, and the advisory is published after the release. Credit is given unless you prefer otherwise.

## Scope

In scope: the match service, the static front-end, the CLI and packaged binary, authentication and ticketing, the event log, SSRF boundaries, webhook signing, secret sealing, and anything that lets one player see another player's hidden information.

Out of scope: vulnerabilities in a model provider, a reverse proxy you configured, or a third-party TextArena environment that melee merely adapts. Report those upstream; we will still help if the boundary is unclear.

## What the project already does

- Bearer auth, short-lived SSE tickets, quotas and per-IP rate limits.
- An SSRF allow-list for model endpoints and webhooks.
- Fernet sealing for queued API keys when `MELEE_SECRET_KEY` is set.
- Server-side filtering of private events; live player perspectives are closed until a match ends.
- Security headers and a CSP that permits no inline script.
- Dependency scanning and a license ledger in the release gate.

## Please do not

- Run automated scanners against someone else's deployment.
- Publish details before a fix is available.
- Test with real credentials or real user data.
