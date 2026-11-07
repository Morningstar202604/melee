"""CLI 端到端：起真实 uvicorn，走真 HTTP，验证每条命令的输出契约。

CLI 是 exe 的入口，它的回归等于产品入口的回归——所以这里的用例
跑的是真服务器而不是 mock，断言的是 stdout 的形状（表格/JSON/退出码）。
"""

from __future__ import annotations

import json
import socket
import threading
import time
import urllib.request

import pytest

from services.match import cli


@pytest.fixture(scope="module")
def live_server():
    """模块级起一个真实服务：CLI 测试走真 HTTP。"""
    import tempfile
    from pathlib import Path

    import uvicorn

    from services.match.api import create_app

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    db_path = Path(tempfile.mkdtemp(prefix="cli-test-")) / "cli.db"
    app = create_app(db_path=db_path)
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as response:  # nosec B310 - loopback fixture
                if response.status == 200:
                    break
        except Exception:  # noqa: BLE001 - 启动期连不上是正常的
            time.sleep(0.3)
    else:
        raise RuntimeError("CLI 测试服务没有就绪")

    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=10)


def _run(argv, capsys):
    code = cli.main(argv)
    out = capsys.readouterr().out
    return code, out


def test_games_table_and_json(live_server, capsys):
    code, out = _run(["--api", live_server, "games"], capsys)
    assert code == 0
    assert "twenty-questions" in out and "Twenty Questions" in out

    code, out = _run(["--api", live_server, "games", "--json"], capsys)
    assert code == 0
    parsed = json.loads(out)
    assert any(g["gameId"] == "codenames" for g in parsed["games"])


def test_match_lifecycle_through_cli(live_server, capsys):
    code, out = _run(
        ["--api", live_server, "match", "create", "--game", "tic-tac-toe",
         "--seed", "9", "--player", "Ann", "--player", "Ben", "--json"],
        capsys,
    )
    assert code == 0
    match_id = json.loads(out)["matchId"]

    # 等到终局再取 recap
    deadline = time.time() + 60
    while time.time() < deadline:
        code, out = _run(
            ["--api", live_server, "match", "events", match_id,
             "--type", "match.ended"], capsys)
        if out.strip():
            break
        time.sleep(1)
    assert code == 0

    code, out = _run(["--api", live_server, "match", "get", match_id], capsys)
    assert code == 0
    # 胜者不固定（种子相同但引擎确定性保证同一结果；换引擎实现会换胜者），
    # 断言形状而非具体名字：一句话战报 + 可对账数字 + 高光列表。
    assert "wins." in out and "problems=0" in out and " - " in out


def test_health_wait_and_stats(live_server, capsys):
    code, out = _run(["--api", live_server, "health", "--wait", "5", "--json"], capsys)
    assert code == 0
    assert json.loads(out)["status"] == "ok"

    code, out = _run(["--api", live_server, "stats", "--json"], capsys)
    assert code == 0
    assert json.loads(out)["matches"] >= 1


def test_season_commands(live_server, capsys):
    from services.match.cli import _request

    _request(
        live_server, "/seasons", method="POST",
        body={"seasonId": "cli-season", "name": "CLI", "game": "tic-tac-toe",
              "players": [{"id": "A"}, {"id": "B"}], "intervalMinutes": 30},
    )
    code, out = _run(["--api", live_server, "seasons"], capsys)
    assert code == 0 and "cli-season" in out

    code, out = _run(["--api", live_server, "season-run", "cli-season"], capsys)
    assert code == 0 and "played:" in out
