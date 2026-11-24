# melee brand

melee is not a dashboard with a dark mode. It is a place where a match happens.

The identity follows that sentence: one bright event at the center, two sides meeting, and a dark arena around them. Restraint is part of the brand. The product should feel like a broadcast control room at 2 a.m., not a SaaS landing page.

## The mark

![melee mark](assets/brand/logo-badge.svg)

The mark is an **M** built from two rising strokes. They read as two competitors, two towers, or two sides of a bracket. The strokes meet at a **gold core** — the single event that both sides share. A thin cyan orbit around it is the broadcast surface: the thing that carries the match outward.

It is deliberately simple enough to survive a 16 px favicon, a Windows installer icon, a 3D scene header and a README at 1600 px wide.

Do not add effects, rotate it, or replace the gold core with a team color. The mark is the constant; the surrounding screens change.

## Wordmark

The wordmark is lowercase `melee`, set tight and heavy, with the mark to its left. Lowercase keeps the tone conversational; the weight keeps it from disappearing next to a 3D scene.

Use the light wordmark on light surfaces and the dark wordmark on dark surfaces. Never set the wordmark in another typeface, and never put the mark inside a second badge when the wordmark is already present.

## Color

| Token | Hex | Use |
|---|---|---|
| Ink | `#070B12` | arena background, hero, installer |
| Panel | `#111A27` | cards and rails |
| Cyan | `#22D3EE` | live signal, identity stroke |
| Blue | `#2563EB` | second half of the mark gradient |
| Gold | `#F1C46A` | the event core; coach, truth, emphasis |
| Mist | `#E8EAF2` | primary text on dark |
| Muted | `#8F9DB2` | secondary text |

The product UI keeps its own light/dark tokens. The marketing surface is dark because the arena is dark; the app opens in light because a control room needs readable panels in daylight. That contrast is intentional: **the broadcast is dark, the desk is light.**

## Voice

Write like a good commentator, not a press release.

- Prefer short declaratives: “The board is a view. The log is the match.”
- Name the concrete thing: event log, turn, camera cut, coach note.
- Avoid “revolutionary”, “seamless”, “empowering”, “next-generation”.
- No exclamation marks in product copy. Let the image carry the excitement.
- Chinese copy should have the same restraint: 少一点形容词，多一点现场感。

Approved lines:

> Every move, worth rewatching.
>
> AI matches. Broadcast like sport.
>
> Live is a view. Replay is a view. The log is the match.

## Layout

- Generous margins. When in doubt, remove a card rather than add one.
- One accent per composition. Cyan is the signal; gold is the fact.
- Prefer thin rules, soft glows and monospace metadata over heavy borders.
- Screenshots should sit inside a darker frame, never float on white.
- Keep body text at least 16 px in rendered marketing images.

## Files

| File | Use |
|---|---|
| `assets/brand/logo-mark.svg` | transparent mark, app and 3D header |
| `assets/brand/logo-badge.svg` | dark tile, favicon and installer |
| `assets/brand/logo-wordmark-light.svg` | light surfaces, documents |
| `assets/brand/logo-wordmark-dark.svg` | dark surfaces, README and social |
| `apps/web/brand-mark.svg` | runtime copy used by the web app |
| `apps/web/favicon.svg` | browser tab |
| `apps/web/favicon.ico` | Windows installer / portable build |
| `assets/readme-hero.png` | README hero (English), 1600×720 |
| `assets/readme-hero-zh.png` | README hero (Chinese), 1600×720 |
| `assets/social-card.png` | Open Graph / social preview, 1280×640 |
| `assets/architecture.svg` | system map used in README (English) |
| `assets/architecture-zh.svg` | system map used in README (Chinese) |

## Clear space and minimums

- Keep clear space equal to the height of the gold core on every side.
- Minimum mark size: 16 px for favicon, 24 px for UI, 48 px for print.
- Minimum wordmark height: 28 px; below that, use the badge alone.

## Where it lives in the product

- Top bar: 24 px mark + `MELEE / Broadcast`.
- 3D arena header: 28 px mark + `melee` + the gold `3D` pill.
- Browser tab and installer: dark badge.
- Watch page: mark-only link, with an accessible label.

The logo should never be the loudest thing on a screen with a live match. It is the signature in the corner, not the headline.
