"""嵌入页、OBS 场景快照与观战外壳的接线测试。"""

from __future__ import annotations

import json
from pathlib import Path

from services.match.episode import build_export, parse_episode

from .support import create, make_client, wait_finished

SAMPLE = Path(__file__).resolve().parents[3] / "examples" / "episodes" / "kaggle-real-chess.json"


def test_scene_for_native_match(client):
    data = create(client)
    wait_finished(data["matchId"])
    scene = client.get(f"/matches/{data['matchId']}/scene").json()
    assert scene["matchId"] == data["matchId"]
    assert scene["status"] == "ended"
    assert scene["finished"] is True
    assert scene["players"] == ["A", "B"]
    assert scene["events"] > 0
    assert scene["lastAction"]["player"] in {"A", "B"}


def test_scene_for_imported_chess_keeps_board(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch, worker=False)
    document = build_export(parse_episode(json.loads(SAMPLE.read_text(encoding="utf-8")), "kaggle"))
    response = client.post("/matches/import", json=document)
    assert response.status_code == 200, response.text
    match_id = response.json()["matchId"]
    scene = client.get(f"/matches/{match_id}/scene").json()
    assert scene["source"] == "import"
    assert scene["status"] == "ended"
    assert scene["board"] and len(scene["board"]) == 8
    assert scene["lastMove"]["from"] and scene["lastMove"]["to"]
    assert set(scene["material"]) == {"white", "black", "diff"}


def test_embed_route_is_iframeable_and_keeps_token(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch, worker=False)
    data = create(client, inlineWorker=False)
    response = client.get(f"/embed/{data['matchId']}?token=read-only")
    assert response.status_code == 200, response.text
    body = response.text
    assert "<iframe" in body
    assert f"match={data['matchId']}" in body
    assert "embed=1" in body
    assert "token=read-only" in body
    assert response.headers["x-frame-options"] == "SAMEORIGIN"
    assert "frame-ancestors 'self'" in response.headers["content-security-policy"]


def test_embed_rejects_bad_match_id(client):
    assert client.get("/embed/../etc").status_code in {400, 404}


def test_embed_origins_allow_cross_site_framing(tmp_path, monkeypatch):
    from services.match.settings import reset_settings

    monkeypatch.setenv("MELEE_EMBED_ORIGINS", "https://partner.example.com")
    reset_settings()
    try:
        client = make_client(tmp_path, monkeypatch, worker=False)
        data = create(client, inlineWorker=False)
        response = client.get(f"/embed/{data['matchId']}")
        assert response.status_code == 200
        assert "x-frame-options" not in response.headers
        assert "https://partner.example.com" in response.headers["content-security-policy"]
    finally:
        reset_settings()


def test_overlay_static_surface_is_wired():
    root = Path(__file__).resolve().parents[3] / "apps" / "web"
    html = (root / "overlay.html").read_text(encoding="utf-8")
    css = (root / "src" / "overlay.css").read_text(encoding="utf-8")
    assert "./src/overlay.js" in html
    assert "./src/overlay.css" in html
    assert "background:transparent" in css


def test_material_and_empty_scene_branches():
    from services.match.routes.matches import _material, _scene_from_events

    assert _material(None) is None
    assert _material(["X", ["P", "p", "", None, "K"]]) == {"white": 1, "black": 1, "diff": 0}
    empty = _scene_from_events("m_missing", [])
    assert empty["status"] == "waiting" and empty["source"] == "log"
    assert empty["lastSeq"] == -1


def test_arena3d_static_surface_is_wired(client):
    """3D 页、vendor 的 Three.js 与 CSP importmap hash 必须一起成立。"""
    root = Path(__file__).resolve().parents[3] / "apps" / "web"
    html = (root / "arena3d.html").read_text(encoding="utf-8")
    js = (root / "src" / "arena3d.js").read_text(encoding="utf-8")
    assert "./src/arena3d.js" in html
    assert "./src/theme-boot.js" in html
    assert 'id="a3CoachForm"' in html
    assert 'id="a3CoachTarget"' in html
    assert "vendor/three/three.module.js" in html
    assert "vendor/three/addons/" in html
    assert (root / "vendor" / "three" / "three.module.js").is_file()
    assert (root / "vendor" / "three" / "three.core.js").is_file()
    assert (root / "vendor" / "three" / "addons" / "controls" / "OrbitControls.js").is_file()
    assert "MIT" in (root / "vendor" / "three" / "LICENSE").read_text(encoding="utf-8")
    assert "api.coach" in js
    assert "a3CoachForm" in js
    assert 'id="a3Thoughts"' in html and 'id="a3Timeline"' in html
    assert "renderTrace" in js and "lastThoughts" in (root / "src" / "view.js").read_text(encoding="utf-8")
    assert "lang: getLang()" in (root / "src" / "api.js").read_text(encoding="utf-8")
    assert "lang: getLang()" in (root / "src" / "sse.js").read_text(encoding="utf-8")
    assert "coach.message" in (root / "src" / "view.js").read_text(encoding="utf-8")
    assert "PCFShadowMap" in js
    assert "PCFSoftShadowMap" not in js

    import base64
    import hashlib
    import re

    match = re.search(r'<script type="importmap">(.*?)</script>', html, re.S)
    assert match
    digest = "sha256-" + base64.b64encode(hashlib.sha256(match.group(1).encode()).digest()).decode()
    response = client.get("/arena3d.html")
    assert response.status_code == 200
    assert digest in response.headers["content-security-policy"]
