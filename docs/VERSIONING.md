# Versioning and release process

This document defines how melee is versioned, what a release must contain, and who is allowed to publish one. It exists so a release is a repeatable procedure rather than a judgement call made from memory.

## Semantic versioning

melee follows [SemVer 2.0.0](https://semver.org/). The public surface is:

| Surface | Examples | Breaking change means |
|---|---|---|
| Event contract | `packages/contracts/schema/battle-event.schema.json` | Removing a field, tightening a type, changing an enum value |
| HTTP API | `POST /matches`, `GET /matches/{id}/events` | Removing an endpoint, changing a response shape, changing an error status |
| Config env vars | `MELEE_*` | Renaming or removing a variable, changing its default |
| Engine interface | `Game.observe/apply`, `Agent.move` | Changing the method signatures third-party agents implement |
| Static frontend URLs | `watch.html?match=`, `?lang=` | Removing a query parameter that existing links rely on |

Not part of the public surface, and may change in a minor release: internal module layout, the exact wording of commentary, log formats, CSS class names.

- **MAJOR** — a change that breaks one of the surfaces above.
- **MINOR** — new capability, backwards compatible.
- **PATCH** — bug fix only, no new behaviour beyond the fix.

Pre-1.0 history (`0.x`) was used during initial development and is not governed by this policy.

## Where the version lives

There is exactly one source of truth per surface, and tests keep them aligned. A release bumps **all four carriers at once** — until 1.9.0 only the first two were checked, and `melee --version` / the packaged exe could silently drift behind the real release.

| File | Field | What reports it to a user |
|---|---|---|
| `services/match/api.py` | `FastAPI(version=...)` | `/openapi.json`, `/docs` |
| `services/match/cli.py` | `APP_VERSION` | `melee --version` |
| `scripts/build_exe.py` | `APP_VERSION` | the portable zip's INSTALL.txt and the exe's `--version` |
| `apps/web/package.json` | `version` | the static frontend's own version |
| `CHANGELOG.md` | the top `## [x.y.z]` heading | the release itself |

`services/match/tests/test_architecture.py` asserts every carrier matches the changelog head, so a bump that forgets one of the five fails the gate.

## What a release contains

A release commit must contain all of the following. Anything missing means the release is not finished.

1. **Code** for the change, with tests that would fail without it.
2. **`CHANGELOG.md` entry** under `## [x.y.z] — YYYY-MM-DD`, using the Keep a Changelog sections: Added / Changed / Deprecated / Removed / Fixed / Security. Only sections that apply.
3. **Version bump** in the files listed above (all four carriers, not just the API).
4. **Documentation** updated when behaviour a user can observe changed. If a screenshot shows the changed surface, the screenshot is retaken (`scripts/capture_gallery.py`).
5. **Green gate**: `bash scripts/verify_all.sh` exits 0 and prints `ALL GREEN`. A `SKIP` line is not a pass and must be explained in the release notes if it covers a required engine.
6. **Git tag** `vX.Y.Z` on the release commit, pushed with the branch.

## Release procedure

```bash
# 1. Start from a clean tree on main
git status --short          # must be empty
git pull --ff-only

# 2. Full gate — this is the only thing that decides "shippable"
bash scripts/verify_all.sh

# 3. Bump versions + write the changelog entry, then commit
git add -A
git commit -m "release: v1.7.0"

# 4. Tag and push
git tag -a v1.7.0 -m "v1.7.0"
git push origin main --tags
```

Additional remotes mirror the same commit; see [Deployment](DEPLOY.md) for the current mirror list.

## Changelog rules

- One entry per released version. Unreleased work stays in the commit history, not in an `[Unreleased]` section — this repository releases in batches and an always-present empty section invites stale text.
- Write for someone deciding whether to upgrade: what changed for them, not which module you edited.
- Every entry must be checkable. "Improved performance" is not an entry; "recap render under 1s for a 5000-event match" is.
- Link the test or document that proves the claim when it is not obvious from the entry.

## Deprecation

A feature marked deprecated keeps working for at least one minor release and logs a warning naming the replacement. Removal happens in the next MAJOR, and the changelog entry says so at deprecation time — not at removal time.

## Hotfixes

A security or data-integrity fix ships as a PATCH from `main`, not from a long-lived branch. Cut the fix, run the gate, tag, and write the changelog entry in the same commit. If the fix cannot be described without naming the vulnerability, write the entry one release later and say so.
