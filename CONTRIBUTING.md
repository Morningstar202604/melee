# Contributing

**English** · [简体中文](CONTRIBUTING.zh-CN.md)

Thanks for taking the time. melee is small enough that a clear bug report or a focused pull request lands quickly.

## Before you start

- Open an issue for anything larger than a typo. It is cheaper to agree on the shape first than to rewrite a patch.
- Security problems go to the address in [SECURITY.md](SECURITY.md), not to a public issue.
- By contributing you agree that your work is licensed under the project's MIT license.

## Setting up

```bash
python -m pip install -r requirements.txt -r requirements-dev.txt
python -m playwright install --with-deps chromium
bash scripts/verify_all.sh
```

The last command is the whole gate. It should print `ALL GREEN` before you open a pull request. If a browser engine is missing, the audit prints `SKIP`; say so in the pull request rather than pretending it passed.

## What makes a good change

- **A test that fails without the change.** This project has very few “just trust me” patches; the changelog entries point at tests on purpose.
- **A small diff.** Refactors and behaviour changes in separate commits. Reviewers can follow one idea at a time.
- **Words a user would use.** Error messages, recap text and admin labels are read by people, not parsed by machines.
- **No hidden state.** The event log is the source of truth. Anything a viewer can see must be derivable from it.

## Commits and pull requests

Write commit subjects in the imperative: `Fix turn timeout on reconnect`, not `fixed stuff`. A pull request should say what changed, why, and how you verified it. Screenshots help when the interface changed.

## Documentation

Every core document has an English version at `docs/*.md` and a Chinese version at `docs/zh-CN/*.md`, with a switch at the top. If your change affects behaviour a reader can observe, update both sides in the same pull request. Screenshots come from `scripts/capture_gallery.py`; do not hand-edit them.

## Releases

Releases are cut from `main` following [VERSIONING.md](docs/VERSIONING.md). Contributors do not need to bump versions; the maintainer does that at release time.
