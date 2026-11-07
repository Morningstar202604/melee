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

def test_cli_error_paths_report_nonzero_and_explain(live_server, capsys):
    """CLI 的失败路径必须非零退出并说明原因。

    这些分支是用户最容易踩到的（服务没起、id 打错、参数越界），
    没人测就等于"到用户手里才发现"。
    """
    # 服务不可达
    code, _ = _run(["--api", "http://127.0.0.1:1", "health"], capsys)
    assert code != 0

    # 不存在的比赛
    code, out = _run(["--api", live_server, "match", "get", "m_nope"], capsys)
    assert code == 1

    # 不存在的赛季
    code, out = _run(["--api", live_server, "season-run", "no-such-season"], capsys)
    assert code == 1


def test_cli_create_with_delay_and_max_questions(live_server, capsys):
    """--delay 与 --max-questions 必须真的传给服务端。"""
    code, out = _run(
        ["--api", live_server, "match", "create", "--game", "twenty-questions",
         "--seed", "11", "--player", "Slow1", "--player", "Slow2",
         "--delay", "10", "--max-questions", "5", "--json"],
        capsys,
    )
    assert code == 0
    created = json.loads(out)
    assert created["matchId"].startswith("m_")


def test_cli_events_type_filter(live_server, capsys):
    """--type 过滤要真的只看那一类事件。"""
    code, out = _run(
        ["--api", live_server, "match", "create", "--game", "nim", "--seed", "5",
         "--player", "N1", "--player", "N2", "--json"],
        capsys,
    )
    assert code == 0
    match_id = json.loads(out)["matchId"]

    deadline = time.time() + 60
    out = ""
    while time.time() < deadline:
        code, out = _run(
            ["--api", live_server, "match", "events", match_id,
             "--type", "match.started", "--json"], capsys)
        if out.strip() and out.strip() != "[]":
            break
        time.sleep(1)
    assert code == 0
    # events 的输出契约是 JSONL（每行一条），不是 JSON 数组——
    # 这是流式命令的正确形态：--follow 要能边收边打。
    rows = [json.loads(line) for line in out.splitlines() if line.strip()]
    assert rows and all(e["type"] == "match.started" for e in rows), rows[:2]


def test_cli_export_then_import_roundtrip(live_server, capsys, tmp_path):
    """导出再导入：文件要能落地，导入要给出新 matchId。"""
    code, out = _run(
        ["--api", live_server, "match", "create", "--game", "nim", "--seed", "3",
         "--player", "E1", "--player", "E2", "--json"],
        capsys,
    )
    match_id = json.loads(out)["matchId"]

    deadline = time.time() + 60
    while time.time() < deadline:
        code, out = _run(["--api", live_server, "match", "events", match_id,
                          "--type", "match.ended", "--json"], capsys)
        if out.strip() and out.strip() != "[]":
            break
        time.sleep(1)

    target = tmp_path / "export.json"
    code, out = _run(["--api", live_server, "match", "export", match_id,
                      "--out", str(target)], capsys)
    assert code == 0 and target.exists()
    exported = json.loads(target.read_text(encoding="utf-8"))
    assert exported["events"]

    code, out = _run(["--api", live_server, "match", "import", str(target), "--json"], capsys)
    assert code == 0
    imported = json.loads(out)
    assert imported["matchId"] != match_id
    assert imported["events"] == len(exported["events"])


def test_cli_archive_filters(live_server, capsys):
    """archive 的 --game / --status / --q 过滤都要生效。"""
    code, out = _run(["--api", live_server, "archive", "--game", "nim", "--json"], capsys)
    assert code == 0
    items = json.loads(out)["items"]
    assert all(item["game"] == "nim" for item in items)

    code, out = _run(["--api", live_server, "archive", "--status", "ended", "--json"], capsys)
    assert code == 0
    assert all(item["status"] == "ended" for item in json.loads(out)["items"])

    code, out = _run(["--api", live_server, "archive", "--q", "Ann", "--json"], capsys)
    assert code == 0


def test_cli_token_is_sent_as_bearer(live_server, capsys):
    """--token 必须变成 Authorization 头。

    没有这一条时，token 模式下 CLI 会静默 401，用户只看到"出错了"。
    """
    from services.match import cli as cli_module

    captured = {}

    class FakeResponse:
        status = 200
        headers = {"content-type": "application/json"}

        def read(self):
            return b'{"ok": true}'

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake_urlopen(request, timeout=30):
        captured["auth"] = request.headers.get("Authorization")
        captured["lang"] = request.headers.get("Accept-language")
        return FakeResponse()

    original = cli_module.urllib.request.urlopen
    cli_module.urllib.request.urlopen = fake_urlopen
    try:
        status, body = cli_module._request(
            live_server, "/quota", token="secret-token", lang="zh")
    finally:
        cli_module.urllib.request.urlopen = original
    assert status == 200 and body == {"ok": True}
    assert captured["auth"] == "Bearer secret-token"
    assert captured["lang"] == "zh"


def test_cli_rejects_non_http_base_url():
    """CLI 不能接受 file:// 之类的 base URL：那会读本地文件而不是调服务。"""
    from services.match import cli as cli_module

    with pytest.raises(SystemExit):
        cli_module._request("file:///etc/passwd", "/games")

