"""外部对局导入：通用格式、Kaggle、TextArena，以及 HTTP 入口。"""

from __future__ import annotations

import json
from datetime import datetime

from services.match.episode import EPISODE_FORMAT, build_export, parse_episode


def _board(mark: str, row: int, col: int) -> list[list[str]]:
    board = [["·"] * 3 for _ in range(3)]
    board[row][col] = mark
    return board


SAMPLE = {
    "format": EPISODE_FORMAT,
    "source": "fixture",
    "episodeId": "fixture-ttt",
    "game": "tic-tac-toe",
    "players": ["A", "B"],
    "seed": 11,
    "winner": "A",
    "reason": "三子连线",
    "steps": [
        {"player": "A", "action": "[4]", "board": _board("O", 1, 1)},
        {"player": "B", "action": "[2]", "board": _board("X", 0, 2)},
        {"player": "A", "action": "[0]", "board": _board("O", 0, 0)},
        {"player": "B", "action": "[6]", "board": _board("X", 2, 0)},
        {"player": "A", "action": "[8]", "board": _board("O", 2, 2)},
    ],
}


def test_parse_generic_episode():
    episode = parse_episode(SAMPLE)
    assert episode.game == "tic-tac-toe"
    assert episode.players == ["A", "B"]
    assert episode.winner == "A"
    assert len(episode.steps) == 5


def test_build_export_is_a_contiguous_replay():
    document = build_export(parse_episode(SAMPLE))
    assert document["format"] == "melee.export/1"
    types = [event["type"] for event in document["events"]]
    assert types[0] == "match.created"
    assert types[-1] == "match.ended"
    assert types.count("state.changed") == 5
    assert [event["seq"] for event in document["events"]] == list(range(len(types)))


def test_kaggle_real_root_wrapper_uses_state_history_and_rewards():
    """真实数据集文件是 {"root": ...}，动作在 steps 里是 actionString。

    顶层 root 不是装饰：configuration/info/rewards/steps 全在它里面。
    stateHistory 比动作多一项，最后一项才是终局棋盘。
    """
    document = {
        "root": {
            "configuration": {
                "openSpielGameName": "chess",
                "openSpielGameString": "chess()",
                "seed": 1906742206,
            },
            "info": {
                "EpisodeId": 75884200,
                "TeamNames": ["Gemini", "GPT"],
                "actionHistory": ["2426", "1258"],
                "moveDurations": [3.0, 4.0],
                "stateHistory": [
                    "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
                    "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1",
                    "rnbqkbnr/pp1ppppp/8/2p5/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2",
                ],
            },
            "rewards": [-1.0, 1.0],
            "steps": [
                [
                    {"action": {"submission": -1}, "observation": {"playerId": 0}},
                    {"action": {"submission": -1}, "observation": {"playerId": 1}},
                ],
                [
                    {"action": {"submission": -1}, "observation": {"playerId": 0}},
                    {
                        "action": {"submission": 2426, "actionString": "e4"},
                        "observation": {"playerId": 1},
                        "info": {"actionApplied": 2426},
                    },
                ],
                [
                    {
                        "action": {"submission": 1258, "actionString": "c5"},
                        "observation": {"playerId": 0},
                        "info": {"actionApplied": 1258},
                    },
                    {"action": {"submission": -1}, "observation": {"playerId": 1}},
                ],
            ],
        }
    }
    episode = parse_episode(document)
    assert episode.source == "kaggle-game-arena"
    assert episode.players == ["Gemini", "GPT"]
    assert [step.action for step in episode.steps] == ["e4", "c5"]
    # 席位被 seed 旋转：e4 是 1 号位 GPT，c5 是 0 号位 Gemini。
    assert [step.player for step in episode.steps] == ["GPT", "Gemini"]
    assert episode.winner == "GPT"
    assert len(episode.final_board or []) == 8
    assert episode.steps[-1].board[3][2] == "♟"
    assert episode.duration_sec == 7.0
    export = build_export(episode)
    span = datetime.fromisoformat(export["events"][-1]["at"]) - datetime.fromisoformat(
        export["events"][0]["at"]
    )
    assert span.total_seconds() == 7.0


def test_real_kaggle_example_file_parses():
    """仓库里的真实 episode 必须一直可导入，而不是只在下载目录里能跑。"""
    from pathlib import Path

    path = Path(__file__).resolve().parents[3] / "examples/episodes/kaggle-real-chess.json"
    episode = parse_episode(json.loads(path.read_text(encoding="utf-8")))
    assert episode.players == ["Gemini 3.1 Pro Preview", "GPT-5.5"]
    assert len(episode.steps) == 109
    assert episode.steps[0].action == "e4"
    assert episode.steps[-1].action == "Qc3#"
    assert episode.winner == "GPT-5.5"
    assert len(episode.final_board or []) == 8


def test_import_api_and_replay_surface(client):
    created = client.post("/matches/import-episode", json=SAMPLE)
    assert created.status_code == 200, created.text
    match_id = created.json()["matchId"]

    events = client.get(f"/matches/{match_id}/events").json()["events"]
    assert events[0]["type"] == "match.created"
    assert events[-1]["payload"]["winner"] == "A"

    state = client.get(f"/matches/{match_id}/state").json()
    assert state["finished"] is True
    assert state["winner"] == "A"
    assert state["game"] == "tic-tac-toe"

    recap = client.get(f"/matches/{match_id}/recap").json()
    assert recap["winner"] == "A"
    assert recap["turns"] == 5

    clips = client.get(f"/matches/{match_id}/clips?lang=zh").json()
    assert clips["clipCount"] >= 1
    assert any("落子" in clip["title"] for clip in clips["clips"])


def test_kaggle_episode_uses_team_names_and_fen():
    document = {
        "configuration": {
            "openSpielGameName": "chess",
            "seed": 1906742206,
            "openSpielGameString": "chess()",
        },
        "info": {"EpisodeId": 75884200, "TeamNames": ["Gemini", "GPT"]},
        "steps": [
            [
                {
                    "action": "e2e4",
                    "observation": {"fen": "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"},
                    "info": {"player": 0},
                },
                {"action": None},
            ],
            [
                {
                    "action": "e7e5",
                    "observation": {"fen": "rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2"},
                    "info": {"player": 1},
                },
                {"action": None, "reward": 0},
            ],
        ],
    }
    episode = parse_episode(document, "kaggle")
    assert episode.source == "kaggle-game-arena"
    assert episode.game == "chess"
    assert episode.players == ["Gemini", "GPT"]
    assert episode.episode_id == "75884200"
    assert len(episode.steps[0].board or []) == 8


def test_textarena_turns_snapshot():
    document = {
        "format": "textarena",
        "game": "tic-tac-toe",
        "players": ["A", "B"],
        "seed": 9,
        "state": {"game_state": {"board": [["O", "·", "X"], ["·", "O", "·"], ["X", "·", "O"]]}, "rewards": {0: 1, 1: -1}},
        "turns": [
            {"player": 0, "action": "[4]", "board": _board("O", 1, 1)},
            {"player": 1, "action": "[2]", "board": _board("X", 0, 2)},
        ],
    }
    episode = parse_episode(document, "textarena")
    assert episode.source == "textarena"
    assert episode.game == "tic-tac-toe"
    assert episode.winner == "A"


def test_bad_episode_is_a_readable_422(client):
    response = client.post("/matches/import-episode", json={"format": "unknown"})
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "cannot detect" in detail or "format" in detail


def test_numeric_action_history_is_recovered_from_fen():
    """没有 actionString 时不能把 open_spiel 数字 ID 直接端给观众。"""
    document = {
        "root": {
            "configuration": {"openSpielGameName": "chess", "seed": 1},
            "info": {
                "EpisodeId": 7,
                "TeamNames": ["White", "Black"],
                "actionHistory": [2426, 1258],
                "stateHistory": [
                    "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
                    "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1",
                    "rnbqkbnr/pp1ppppp/8/2p5/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2",
                ],
            },
            "rewards": [1.0, -1.0],
        }
    }
    episode = parse_episode(document, "kaggle")
    assert [step.action for step in episode.steps] == ["e4", "c5"]
    assert (episode.steps[0].move_from, episode.steps[0].move_to) == ("e2", "e4")
    assert (episode.steps[1].move_from, episode.steps[1].move_to) == ("c7", "c5")


def test_import_metadata_is_stored_and_duplicate_returns_same_match(tmp_path):
    from services.match.archive import find_match
    from services.match.episode import import_episode

    db_path = tmp_path / "meta.db"
    first = import_episode(
        db_path, SAMPLE, fmt="episode",
        metadata={"license": "CC BY 4.0", "sourceUrl": "https://example.test/data", "sourceLabel": "Fixture Arena"},
    )
    assert first["matchId"]
    item = find_match(db_path, first["matchId"])
    assert item is not None
    assert item["source"] == "fixture"
    assert item["sourceId"] == "fixture-ttt"

    second = import_episode(
        db_path, SAMPLE, fmt="episode",
        metadata={"license": "CC BY 4.0", "sourceUrl": "https://example.test/data", "sourceLabel": "Fixture Arena"},
    )
    assert second["deduplicated"] is True
    assert second["matchId"] == first["matchId"]
