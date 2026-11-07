# Screenshots and recording

Every image here was captured by Playwright against a locally running melee instance, with real matches and a real season. Nothing is a mock-up and no number is typed by hand. Desktop viewport is 1600×1000, mobile is 390×844; long pages use full-page capture, which is why the output height exceeds the viewport.

Each language ships its own full set under `en/` and `zh/`, so the English README never shows a Chinese interface and vice versa.

| File | What it shows | Output size (px) |
|---|---|---:|
| [en/01-home.png](en/01-home.png) | Home page and match library | 1600×2766 |
| [en/02-live.png](en/02-live.png) | A match being broadcast live | 1600×1000 |
| [en/03-twenty-questions.png](en/03-twenty-questions.png) | Twenty Questions replay | 1600×1000 |
| [en/04-codenames.png](en/04-codenames.png) | Codenames | 1600×1000 |
| [en/05-tic-tac-toe.png](en/05-tic-tac-toe.png) | Tic-tac-toe | 1600×1000 |
| [en/06-nim.png](en/06-nim.png) | Nim | 1600×1000 |
| [en/07-othello.png](en/07-othello.png) | Othello | 1600×1000 |
| [en/08-recap.png](en/08-recap.png) | Match recap | 1600×1364 |
| [en/09-season.png](en/09-season.png) | Season standings and fixtures | 1600×1130 |
| [en/10-director.png](en/10-director.png) | Director console | 1600×1000 |
| [en/11-mobile-home.png](en/11-mobile-home.png) | Home on a 390px screen | 390×6841 |
| [en/12-mobile-watch.png](en/12-mobile-watch.png) | Watch page on a 390px screen | 390×1480 |
| [en/13-admin.png](en/13-admin.png) | Admin console: health, queue, quota, scheduler | 1600×1160 |

The Chinese set mirrors this one exactly under `zh/`.

## Recording

[Watch the product walkthrough](../video/walkthrough.mp4), recorded by `scripts/record_walkthrough.py` against the same demo instance. 1440×900 H.264 MP4.

## Reproducing

Start the service on an isolated database, then seed demo matches and a season:

```bash
MELEE_DB=.tmp/melee-demo.db python scripts/serve.py --port 8123
python scripts/seed_demo.py --api http://127.0.0.1:8123 --season demo-2026
python scripts/capture_gallery.py --api http://127.0.0.1:8123 --season demo-2026
python scripts/record_walkthrough.py --api http://127.0.0.1:8123 --season demo-2026
```

`capture_gallery.py` fails if any page logs a JavaScript error. The video needs Playwright and ffmpeg. On Windows, scripts fall back to an installed Microsoft Edge when the bundled Chromium is missing.
