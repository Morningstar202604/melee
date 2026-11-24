# Screenshots and recording

Every image here was captured by Playwright against a locally running melee instance, with real matches and a real season. Nothing is a mock-up and no number is typed by hand.

Each language ships its own set under `en/` and `zh/`, so the English README never shows a Chinese interface and vice versa.

| File | What it shows |
|---|---|
| [en/01-home.png](en/01-home.png) | Home page and match library |
| [en/02-live.png](en/02-live.png) | A match being broadcast live |
| [en/03-twenty-questions.png](en/03-twenty-questions.png) | Twenty Questions replay |
| [en/04-codenames.png](en/04-codenames.png) | Codenames |
| [en/05-tic-tac-toe.png](en/05-tic-tac-toe.png) | Tic-tac-toe |
| [en/06-nim.png](en/06-nim.png) | Nim |
| [en/07-othello.png](en/07-othello.png) | Othello |
| [en/08-recap.png](en/08-recap.png) | Match recap |
| [en/09-season.png](en/09-season.png) | Season standings and fixtures |
| [en/10-director.png](en/10-director.png) | Director console |
| [en/11-mobile-home.png](en/11-mobile-home.png) | Home on a phone-width screen |
| [en/12-mobile-watch.png](en/12-mobile-watch.png) | Watch page on a phone-width screen |
| [en/13-admin.png](en/13-admin.png) | Admin console: health, queue, quota, scheduler |
| [en/14-imported-chess-live.png](en/14-imported-chess-live.png) | External arena snapshot pushed into a live broadcast |
| [en/15-imported-chess.png](en/15-imported-chess.png) | Real Kaggle chess replay with provenance, coordinates and last move |
| [en/16-imported-recap.png](en/16-imported-recap.png) | Recap for an imported match; move numbers come from chess moves, not event seq |
| [en/17-obs-overlay.png](en/17-obs-overlay.png) | Transparent OBS overlay: players, board, last move, material balance and commentary |
| [en/18-embed.png](en/18-embed.png) | Chrome-free `/embed/{id}` page used in an iframe |
| [en/19-arena3d.png](en/19-arena3d.png) | 3D arena on a live 8×8 chess match: AI thinking stream, play-by-play rail, camera presets |
| [en/20-coach-3d.png](en/20-coach-3d.png) | Live 3×3 coaching deck: instruction guide, delivered command, AI thinking stream and localized commentary |

The Chinese set mirrors this one under `zh/`.

## Recording

[Watch the English walkthrough](../video/walkthrough.mp4) · [中文演示](../video/walkthrough.zh.mp4). Both are recorded by `scripts/record_walkthrough.py` against the same demo instance.

## Reproducing

Start the service on an isolated database, then seed demo matches and a season:

```bash
MELEE_DB=.tmp/melee-demo.db python scripts/serve.py --port 8123
python scripts/seed_demo.py --api http://127.0.0.1:8123 --season demo-2026 --lang en
python scripts/capture_gallery.py --api http://127.0.0.1:8123 --season demo-2026 --langs en
python scripts/record_walkthrough.py --api http://127.0.0.1:8123 --season demo-2026 --lang en
```

Use `--lang zh` / `--langs zh` for the Chinese set. `capture_gallery.py` fails if any page logs a JavaScript error. The video needs Playwright and ffmpeg.
