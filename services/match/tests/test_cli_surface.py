"""CLI 分支面：错误路径、过滤旗标、serve 接线、导入导出处理器、输出格式。"""

from __future__ import annotations

import json
import os

import pytest

from services.match import cli


@pytest.fixture(scope="module")
def live_server():
    import socket
    import tempfile
    import threading
    import time
    import urllib.request
    from pathlib import Path

    import uvicorn

    from services.match.api import create_app

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    db_path = Path(tempfile.mkdtemp(prefix="cli-surface-")) / "s.db"
    app = create_app(db_path=db_path)
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}"
    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(base + "/health", timeout=2) as response:  # nosec B310 - loopback fixture
                if response.status == 200:
                    break
        except Exception:  # noqa: BLE001
            time.sleep(0.3)
    else:
        raise RuntimeError("surface fixture not ready")
    yield base
    server.should_exit = True
    thread.join(timeout=10)


def _run(argv, capsys):
    code = cli.main(argv)
    captured = capsys.readouterr()
    return code, captured.out + captured.err


def test_serve_command_wires_uvicorn(tmp_path, capsys, monkeypatch):
    """serve 走 uvicorn.Server(config).run()；patch 掉 run 验证接线与参数。"""
    captured = {}

    class _FakeServer:
        def __init__(self, config):
            captured["config"] = config

        def run(self):
            captured["ran"] = True

    import uvicorn

    monkeypatch.setattr(uvicorn, "Server", _FakeServer)
    db = tmp_path / "w.db"
    code = cli.main([
        "serve", "--port", str(8231), "--db", str(db),
    ])
    assert code == 0
    assert captured["ran"] is True
    assert captured["config"].port == 8231


def test_health_timeout_returns_nonzero(capsys):
    code, out = _run(["health", "--api", "http://127.0.0.1:1", "--wait", "0"], capsys)
    assert code == 1
    assert "not healthy" in out


def test_health_timeout_json_mode(capsys):
    code, out = _run(["health", "--api", "http://dead-host.example:1", "--wait", "0", "--json"], capsys)
    assert code == 1


def test_match_get_error_path(live_server, capsys):
    code, out = _run(["match", "get", "m_nonexistent", "--api", live_server], capsys)
    assert code == 1
    assert "error" in out


def test_match_get_json_mode(live_server, capsys):
    created = json.loads(
        _run(["match", "create", "--game", "tic-tac-toe", "--seed", "3",
              "--player", "A", "--player", "B", "--api", live_server, "--json"], capsys)[1]
    )
    match_id = created["matchId"]
    import time
    deadline = time.time() + 60
    while time.time() < deadline:
        code, out = _run(["match", "get", match_id, "--api", live_server, "--json"], capsys)
        if json.loads(out).get("winner"):
            break
        time.sleep(1)
    body = json.loads(out)
    assert body["problems"] == [] and body["winner"]


def test_archive_filters(live_server, capsys):
    code, out = _run(["archive", "--api", live_server, "--limit", "5", "--game", "tic-tac-toe", "--status", "ended"], capsys)
    assert code == 0
    assert "Tic-tac-toe" in out or "matchId" in out


def test_archive_empty_table_header(live_server, capsys):
    code, out = _run(["archive", "--api", live_server, "--game", "no-such-game"], capsys)
    assert code == 0


def test_events_type_filter_and_follow_off(live_server, capsys):
    created = json.loads(
        _run(["match", "create", "--game", "tic-tac-toe", "--seed", "4",
              "--player", "A", "--player", "B", "--api", live_server, "--json"], capsys)[1]
    )
    match_id = created["matchId"]
    import time
    deadline = time.time() + 60
    while time.time() < deadline:
        code, out = _run(["match", "events", match_id, "--api", live_server,
                          "--type", "match.ended"], capsys)
        if out.strip():
            break
        time.sleep(1)
    event = json.loads(out.strip().splitlines()[-1])
    assert event["type"] == "match.ended"


def test_export_import_handlers(live_server, capsys, tmp_path):
    created = json.loads(
        _run(["match", "create", "--game", "tic-tac-toe", "--seed", "6",
              "--player", "A", "--player", "B", "--api", live_server, "--json"], capsys)[1]
    )
    match_id = created["matchId"]
    import time
    deadline = time.time() + 60
    while time.time() < deadline:
        code, out = _run(["match", "events", match_id, "--api", live_server,
                          "--type", "match.ended"], capsys)
        if out.strip():
            break
        time.sleep(1)
    out_file = tmp_path / "portable.json"
    code, out = _run(["match", "export", match_id, "--api", live_server, "--out", str(out_file)], capsys)
    assert code == 0 and out_file.exists()
    code, out = _run(["match", "import", str(out_file), "--api", live_server], capsys)
    assert code == 0
    assert "imported:" in out


def test_table_renders_empty_input():
    import contextlib
    import io

    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        cli._table([], ["a", "b"])
    text = buffer.getvalue()
    assert "a" in text and "b" in text and "-" in text


def test_stats_human_and_by_game(live_server, capsys):
    code, out = _run(["stats", "--api", live_server], capsys)
    assert code == 0
    assert "matches=" in out
    assert "game" in out  # by_game 表存在（surface 库已有 tic-tac-toe）


def test_match_open_prints_url_and_does_not_launch_browser(live_server, capsys, monkeypatch):
    opened = []
    import webbrowser as _wb

    monkeypatch.setattr(_wb, "open", lambda url: opened.append(url))
    code, out = _run(["match", "open", "m_demo", "--api", live_server, "--lang", "zh"], capsys)
    assert code == 0
    assert "watch.html" in out and "lang=zh" in out and "m_demo" in out
    assert opened and "lang=zh" in opened[0]


def test_mcp_env_wiring(live_server, monkeypatch):
    """mcp 子命令把 --api/--token 写进环境；然后 main 立即返回（patch 掉服务器）。"""
    from services.match import cli as cli_mod

    calls = {}

    def fake_mcp_main():
        calls["api"] = os.environ.get("MELEE_API")
        calls["token"] = os.environ.get("MELEE_TOKEN")
        return 0

    import services.match.mcp_server as mcp_mod

    monkeypatch.setattr(mcp_mod, "main", fake_mcp_main)
    monkeypatch.delenv("MELEE_API", raising=False)
    monkeypatch.delenv("MELEE_TOKEN", raising=False)
    code = cli_mod.main(["mcp", "--api", live_server, "--token", "t-secret"])
    assert code == 0
    assert calls["api"] == live_server
    assert calls["token"] == "t-secret"


def test_request_handles_non_json_and_http_error(live_server):
    """_request 的文本响应与 4xx 分支：HTTPError 被转成 (code, body)。"""
    status, body = cli._request(live_server, "/matches/m_missing/recap")
    assert status == 404
    assert "比赛不存在" in str(body) or "Not" in str(body)


def test_request_connection_error_exits(capsys):
    """连不上的 base 直接 SystemExit：错误信息带地址。"""
    with pytest.raises(SystemExit) as excinfo:
        cli._request("http://127.0.0.1:1", "/health", timeout=1)
    assert "127.0.0.1" in str(excinfo.value)
