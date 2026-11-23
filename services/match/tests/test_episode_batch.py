"""批量导入：文件发现、成绩单、单场失败不拖垮整批。"""

from __future__ import annotations

import json

from services.match.batch import discover_episode_files, import_batch, write_report


def _episode_doc(index: int) -> dict:
    return {
        "root": {
            "configuration": {
                "openSpielGameName": "chess",
                "openSpielGameString": "chess()",
                "seed": index,
            },
            "info": {
                "EpisodeId": index,
                "TeamNames": [f"White {index}", f"Black {index}"],
                "stateHistory": [
                    "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
                    "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1",
                ],
            },
            "rewards": [1.0, -1.0],
            "steps": [
                [
                    {"action": {"submission": -1}, "observation": {"playerId": 0}},
                    {"action": {"submission": -1}, "observation": {"playerId": 1}},
                ],
                [
                    {
                        "action": {"submission": 2426, "actionString": "e4"},
                        "observation": {"playerId": 0},
                    },
                    {"action": {"submission": -1}, "observation": {"playerId": 1}},
                ],
            ],
        }
    }


def test_discover_episode_files_accepts_directory(tmp_path):
    (tmp_path / "a.json").write_text("{}", encoding="utf-8")
    (tmp_path / "b.json").write_text("{}", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("not an episode", encoding="utf-8")
    files = discover_episode_files(tmp_path)
    assert [file.name for file in files] == ["a.json", "b.json"]


def test_import_batch_reports_success_and_failure(tmp_path):
    db_path = tmp_path / "batch.db"
    good = tmp_path / "good.json"
    bad = tmp_path / "bad.json"
    good.write_text(json.dumps(_episode_doc(1)), encoding="utf-8")
    bad.write_text(json.dumps({"not": "an episode"}), encoding="utf-8")

    report = import_batch(db_path, [good, bad], fmt="auto")
    assert report["selected"] == 2
    assert report["imported"] == 1
    assert report["failed"] == 1

    imported = next(item for item in report["items"] if item["status"] == "imported")
    assert imported["game"] == "chess"
    assert imported["winner"] == "White 1"
    assert imported["matchId"]

    failed = next(item for item in report["items"] if item["status"] == "failed")
    assert "cannot detect" in failed["error"]


def test_import_batch_limit_shuffle_and_report(tmp_path):
    files = []
    for index in range(3):
        path = tmp_path / f"{index}.json"
        path.write_text(json.dumps(_episode_doc(index)), encoding="utf-8")
        files.append(path)

    report = import_batch(
        tmp_path / "batch.db", files, fmt="kaggle", limit=2, shuffle_seed=7
    )
    assert report["selected"] == 2
    assert report["imported"] == 2

    out = write_report(report, tmp_path / "report.json")
    saved = json.loads(out.read_text(encoding="utf-8"))
    assert saved["imported"] == 2
    assert len(saved["items"]) == 2


def test_import_batch_is_idempotent_and_reports_duplicate(tmp_path):
    db_path = tmp_path / "batch.db"
    good = tmp_path / "good.json"
    good.write_text(json.dumps(_episode_doc(9)), encoding="utf-8")

    first = import_batch(db_path, [good], fmt="kaggle")
    second = import_batch(db_path, [good], fmt="kaggle")
    assert first["imported"] == 1
    assert second["imported"] == 0
    assert second["duplicates"] == 1
    assert second["items"][0]["status"] == "duplicate"
    assert second["items"][0]["matchId"] == first["items"][0]["matchId"]


def test_import_batch_hash_sample_is_reproducible_and_first_is_available(tmp_path):
    files = []
    for index in range(10):
        path = tmp_path / f"{index}.json"
        path.write_text("{}", encoding="utf-8")
        files.append(path)
    a = import_batch(tmp_path / "a.db", files, limit=3, sample="hash", shuffle_seed=4)
    b = import_batch(tmp_path / "b.db", files, limit=3, sample="hash", shuffle_seed=4)
    assert [i["file"] for i in a["items"]] == [i["file"] for i in b["items"]]
    # hash 抽样不应等价于文件名前三个（旧实现的偏科来源）。
    assert [i["file"] for i in a["items"]] != [str(p) for p in files[:3]]


def test_import_batch_accepts_via_api_callback(tmp_path):
    files = []
    for index in range(2):
        path = tmp_path / f"{index}.json"
        path.write_text(json.dumps(_episode_doc(index)), encoding="utf-8")
        files.append(path)

    calls = []

    def fake_api(document):
        calls.append(document)
        return {"matchId": f"api-{len(calls)}", "game": "chess", "deduplicated": False}

    report = import_batch(tmp_path / "unused.db", files, fmt="kaggle", import_document=fake_api)
    assert len(calls) == 2
    assert report["imported"] == 2
    assert report["items"][0]["matchId"] == "api-1"
