"""国际象棋标准出口：PGN 必须能被生态里的通用解析器读回。"""

from __future__ import annotations

import io
import json
from pathlib import Path

import chess.pgn

from services.match.chess_export import pgn_from_export
from services.match.episode import build_export, parse_episode
from services.match.tests.support import make_client

SAMPLE = Path(__file__).resolve().parents[3] / "examples" / "episodes" / "kaggle-real-chess.json"


def _document(finished: bool = True):
    raw = json.loads(SAMPLE.read_text(encoding="utf-8"))
    episode = parse_episode(raw, "kaggle")
    episode.config.update(
        {
            "license": "CC BY 4.0",
            "sourceUrl": "https://www.kaggle.com/datasets/kaggle/chess-text-gameplay",
            "sourceLabel": "Kaggle Game Arena",
        }
    )
    return build_export(episode, finished=finished)


def test_pgn_roundtrips_through_python_chess():
    text = pgn_from_export(_document())
    game = chess.pgn.read_game(io.StringIO(text))
    assert game.headers["White"] == "Gemini 3.1 Pro Preview"
    assert game.headers["Black"] == "GPT-5.5"
    assert game.headers["Result"] == "0-1"
    assert game.headers["License"] == "CC BY 4.0"
    assert game.headers["Date"] == "????.??.??"
    assert len(list(game.mainline_moves())) == 109
    assert game.end().board().is_valid()


def test_live_chess_pgn_uses_unknown_result():
    text = pgn_from_export(_document(finished=False))
    game = chess.pgn.read_game(io.StringIO(text))
    assert game.headers["Result"] == "*"


def test_non_chess_is_rejected():
    document = _document()
    document["game"] = "tic-tac-toe"
    try:
        pgn_from_export(document)
    except ValueError as exc:
        assert "only available for chess" in str(exc)
    else:
        raise AssertionError("non-chess PGN export should fail")


def test_pgn_route_returns_standard_media_type(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch, worker=False)
    document = _document()
    response = client.post("/matches/import", json=document)
    assert response.status_code == 200, response.text
    match_id = response.json()["matchId"]
    pgn = client.get(f"/matches/{match_id}/export?format=pgn")
    assert pgn.status_code == 200, pgn.text
    assert pgn.headers["content-type"].startswith("application/x-chess-pgn")
    assert "Content-Disposition" in pgn.headers
    assert pgn.text.startswith('[Event "Kaggle Game Arena"]')


def test_pgn_route_rejects_tic_tac_toe(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch, worker=False)
    response = client.post(
        "/matches/import",
        json=build_export(
            parse_episode(
                {
                    "format": "melee.episode/1",
                    "source": "fixture",
                    "episodeId": "ttt-pgn",
                    "game": "tic-tac-toe",
                    "players": ["A", "B"],
                    "steps": [{"player": "A", "action": "[4]"}],
                    "winner": "A",
                }
            )
        ),
    )
    assert response.status_code == 200, response.text
    match_id = response.json()["matchId"]
    pgn = client.get(f"/matches/{match_id}/export?format=pgn")
    assert pgn.status_code == 422
