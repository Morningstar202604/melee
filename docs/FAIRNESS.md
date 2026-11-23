# Fairness and multi-agent capability

**English** · [简体中文](zh-CN/FAIRNESS.md)

The question: can AI agents truly play on their own — without leaked information, scripted advantages or built-in favouritism? The method: audit adversarially, fix each real defect, then lock it with gates. Every claim below has a reproducible command or a `file:line` behind it.

## Outcome

| Dimension | Before the audit | Now |
|---|---|---|
| Hidden-information leaks | Kuhn hands, GOPS hands, GOPS pending bids | Zero; parameterised gates cover them |
| First-move bias | Fixed (same side in six seeds) | Decided by seed; 20/20 across forty matches |
| Turn symmetry | 20 Questions: A asked 11, B asked 10 | Symmetric; question budget is a multiple of player count |
| Resource symmetry | Already symmetric | Kept, with tests |
| Natural playability | Four of twelve games deadlocked or forfeited | Every game reaches a legal terminal state |
| Multi-agent line-ups | Eleven games forced exactly two players; Codenames hard-coded four | Codenames supports 2–6 teams × 2–4 players (4–16 agents) |
| Fairness gates | None | Parameterised fairness, line-up and external-agent suites |

The counts above are a snapshot of the 2026-10-29 audit. Current counts come from `scripts/verify_all.sh`.

## What was actually wrong

**Leaks.** The TextArena adapter returned the full `gameState` in `observe()`, including both players' hands in Kuhn and GOPS; GOPS also exposed pending bids, so the second player could see the first player's secret card before choosing. `public_state()` leaked the same fields to spectators and replays.

**Structural bias.** Upstream environments always seat the first player at index 0, and melee passed the player list straight through: the same side moved first for every seed. Twenty Questions used an odd question budget, giving the first player one extra question, and awarded a win to the questioner when the budget ran out.

**Natural play was impossible.** Twenty Questions offered `guess:<candidate>` placeholders without ever listing the candidates, so only a script that read the catalogue could win. Othello hit a deadlock when no move existed because the adapter never offered `[pass]`. GOPS forced a forfeit near hand exhaustion. Connect Four derived columns from a regex over the board text and could offer a full column.

**No multi-agent line-ups.** Eleven games accepted exactly two players; Codenames assumed red/blue and hard-coded the index mapping. A third team had no words of its own.

## What changed

**Trimmed observations.** The adapter's `SUPPORTED` table declares hidden fields per game: `player_cards` and `player_hands` keep only the viewer's own copy; `pending_bids` is dropped. At showdown, `reveal=True` restores the full state — poker must show cards after the hand, or viewers cannot see the bluff.

**Seat rotation.** Player order remains identity; seats are rotated by seed:

```python
seats = [players[(seed + i) % len(players)] for i in range(len(players))]
```

`current_player()` and `_outcome()` translate through seats, so the same seed reproduces the same match.

**Twenty Questions rules.** The question budget becomes a multiple of the player count; first move is `seed % len(players)`; running out of questions is a draw; candidates are listed in the observation and in the model prompt, because "guess the secret" is meaningless if the candidate list is hidden.

**Legal moves repaired.** Othello returns `["[pass]"]` and accepts it; GOPS derives legal moves from the player's remaining hand; Connect Four reads `gameState.board` instead of regexing text; the board agent prefers the engine's `legal` list.

**Line-ups.** `services/match/roster.py` separates three concepts that must not be conflated: player (identity), seat (position), team (side). Codenames accepts `{"teams": 3, "teamSize": 2}`; two teams keep the classic 9/8/7/1 colour counts, three or more split the words evenly; teammates guess in rotation. Config is written into `match.created.config`, so replay rebuilds the same match.

## Evidence

First-move distribution across forty matches per game:

| Game | Matches | A first | B first |
|---|---:|---:|---:|
| tic-tac-toe | 40 | 20 | 20 |
| othello | 40 | 20 | 20 |
| nim | 40 | 20 | 20 |
| connect-four | 40 | 20 | 20 |
| twenty-questions | 40 | 20 | 20 |
| kuhn-poker | 40 | 20 | 20 |

Mirror matches, Minimax vs Minimax, twenty games: tic-tac-toe and reverse tic-tac-toe draw 20/20 — which is the theoretical result, and would be impossible if the platform favoured a side. Wild tic-tac-toe splits 10/10 exactly with first move, as its rules require.

Natural play, twelve games with only `observe()`: every game ends legally, with zero illegal moves and zero forfeits.

Anti-cheat attempts: reading `observation.gameState` for the opponent's hand yields only your own key; `public_state()` has no hand field; GOPS pending bids are empty for the second player; Twenty Questions exposes candidates but never the target. One clarification: in Kuhn, knowing your own card reveals the opponent's card because the deck is dealt without replacement — that is public rules, not a leak. The leak that mattered was reading the opponent's card directly, and that is gone.

## Deliberately not done

| Item | Why |
|---|---|
| MaxFlow fixture scheduling | Needs multi-worker capacity first; four inline threads are not a bottleneck |
| Melting Pot-style observation specs | `Game.observe()` already expresses it; another spec layer would be duplicate abstraction |
| Million-agent social simulation | Different product and different performance model |
| Multi-team season points | The standings model assumes two sides; it needs its own ranking model |

## Reproduce

```bash
python -m pytest services/match/tests/test_fairness.py -q
python -m pytest services/match/tests/test_roster.py -q

MELEE_DB=data/match.db python scripts/serve.py
curl -X POST localhost:8099/matches -H 'Content-Type: application/json' -d '{
  "game":"codenames","seed":11,
  "players":[{"id":"AI-0"},{"id":"AI-1"},{"id":"AI-2"},{"id":"AI-3"},{"id":"AI-4"},{"id":"AI-5"}],
  "config":{"teams":3,"teamSize":2}}'
```
