"""导入边界：坏 FEN、坏着法、数据集入口和 CLI 棋谱路径。"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

from services.match import cli
from services.match.chess_export import _comment, _header, pgn_from_export
from services.match.episode import _board_from_fen

SAMPLE = Path(__file__).resolve().parents[3] / "examples" / "episodes" / "kaggle-real-chess.json"


def _export() -> dict:
    from services.match.episode import build_export, parse_episode

    episode = parse_episode(json.loads(SAMPLE.read_text(encoding="utf-8")), "kaggle")
    episode.config.update(
        {
            "license": "CC BY 4.0",
            "sourceUrl": "https://www.kaggle.com/datasets/kaggle/chess-text-gameplay",
            "sourceLabel": 'Kaggle "quoted" \\ arena',
        }
    )
    return build_export(episode, finished=True)


def test_pgn_requires_chess_events_and_two_players():
    with pytest.raises(ValueError, match="only available for chess"):
        pgn_from_export({"game": "tic-tac-toe", "events": []})
    with pytest.raises(ValueError, match="no events"):
        pgn_from_export({"game": "chess", "events": []})
    with pytest.raises(ValueError, match="two players"):
        pgn_from_export(
            {"game": "chess", "players": ["A"], "events": [{"type": "match.created", "payload": {}}]}
        )


def test_pgn_draw_and_live_results():
    document = _export()
    ended = next(event for event in document["events"] if event["type"] == "match.ended")
    ended["payload"]["winner"] = None
    text = pgn_from_export(document)
    assert '[Result "1/2-1/2"]' in text
    assert 'Event "Kaggle \\"quoted\\" \\\\ arena"' in text
    assert "tournament arena" not in text
    assert "{" in text and "}" in text
    assert _comment("a {b} c") == "a (b) c"
    assert _header('x "y" \\ z') == 'x \\"y\\" \\\\ z'

    live = _export()
    live["events"] = [event for event in live["events"] if event["type"] != "match.ended"]
    assert '[Result "*"]' in pgn_from_export(live)


def test_pgn_rejects_uci_and_numeric_actions():
    document = _export()
    for event in document["events"]:
        if event["type"] == "action.applied":
            event["payload"]["action"]["value"] = "e2e4"
            break
    with pytest.raises(ValueError, match="not SAN"):
        pgn_from_export(document)


def test_fen_falls_back_without_python_chess(monkeypatch):
    monkeypatch.setitem(sys.modules, "chess", None)
    board = _board_from_fen("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1")
    assert len(board) == 8 and len(board[0]) == 8
    assert board[0][0] == "♜"
    # footer of an actually broken FEN still returns a best-effort rectangle
    assert len(_board_from_fen("8/8")) == 2


def test_cli_dataset_import_and_errors(tmp_path, monkeypatch, capsys):
    import kagglehub

    dataset = tmp_path / "dataset"
    dataset.mkdir()
    shutil.copy(SAMPLE, dataset / "one.json")
    monkeypatch.setattr(kagglehub, "dataset_download", lambda slug: str(dataset))
    db = tmp_path / "batch.db"
    report = tmp_path / "report.json"
    rc = cli.main(
        [
            "match",
            "import-batch",
            "--dataset",
            "kaggle/chess-text-gameplay",
            "--format",
            "kaggle",
            "--limit",
            "1",
            "--db",
            str(db),
            "--out",
            str(report),
        ]
    )
    assert rc == 0
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["imported"] == 1
    assert payload["sourceUrl"].endswith("kaggle/chess-text-gameplay")
    assert payload["sourceLabel"].startswith("Kaggle ·")
    assert "dataset:" in capsys.readouterr().err

    assert cli.main(["match", "import-batch", "--dataset", "a/b", "x"]) == 1
    assert cli.main(["match", "import-batch"]) == 1

    def boom(slug):
        raise RuntimeError("offline")

    monkeypatch.setattr(kagglehub, "dataset_download", boom)
    assert cli.main(["match", "import-batch", "--dataset", "a/b", "--db", str(db)]) == 1
    assert "offline" in capsys.readouterr().err


def test_cli_pgn_writes_bytes(tmp_path, monkeypatch):
    def fake_request(base, path, **kwargs):
        assert "format=pgn" in path
        return 200, b'[Event "x"]\n\n1. e4 *\n'

    monkeypatch.setattr(cli, "_request", fake_request)
    out = tmp_path / "out.pgn"
    rc = cli.main(["match", "export", "m_x", "--format", "pgn", "--out", str(out), "--api", "http://127.0.0.1:9"])
    assert rc == 0 and out.read_bytes().startswith(b"[Event")

    monkeypatch.setattr(cli, "_request", lambda base, path, **kwargs: (200, {"format": "melee.export/1", "events": []}))
    json_out = tmp_path / "out.json"
    rc = cli.main(["match", "export", "m_x", "--out", str(json_out), "--api", "http://127.0.0.1:9"])
    assert rc == 0 and json.loads(json_out.read_text())["format"] == "melee.export/1"
