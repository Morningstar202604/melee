# Quality gates

**English** · [简体中文](zh-CN/QUALITY.md)

This file does not freeze test counts or coverage percentages; those drift. The current command output is the source of truth.

## One command

```bash
bash scripts/verify_all.sh
```

It runs, in order:

1. Backend pytest.
2. Line and branch coverage (91% / 85% gates).
3. ruff.
4. Bandit at medium and above.
5. License ledger.
6. Front-end JavaScript syntax and Node unit tests.
7. JSON Schema codegen parity.
8. OSV dependency scanning.
9. Dockerfile / Compose static checks.
10. Installer template check.
11. Playwright browser audit.

`6b` is the Node unit-test group, which is why the log shows ten numbered groups but eleven steps. Only an `ALL GREEN` line at the end means everything passed.

## Run a single gate

```bash
python -m pytest -q
node --test apps/web/test/*.test.mjs
python scripts/check_coverage.py
python -m ruff check .
python scripts/audit_cves.py --fail-on high
```

On Windows the project uses `.venv`; run `.venv/Scripts/python.exe` when the interpreter is not on `PATH`. Install with `uv pip` as the project convention says.

## Reading the results

- A missing Playwright browser prints `SKIP`. Only a run where no engine works fails.
- The Dockerfile check is static: entry point, copied sources, runtime dependencies, non-root user, health check, data volume. It is not a real image build; that belongs to CI or the target environment.
- Real Anthropic/Gemini, Redis, ffmpeg and external HTTP agents are not replaced by local mocks. The tests pin request contracts, parsing, error handling and fairness boundaries — not a vendor's long-term availability.
- Example keys in fixtures exist for redaction regressions only. They are not production credentials.

## Boundaries

Historical audits and release records keep their original dates and are not current-state claims. Current state is decided by the code, the tests, the latest `verify_all.sh` output and the commit it ran on.
