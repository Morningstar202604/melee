"""SSRF 门禁：BYOK 的 baseUrl 不允许指向内网、回环、链路本地与云元数据。"""

from __future__ import annotations

import pytest

from services.match.outbound import OutboundAddressError, check_base_url
from services.match.player_specs import ModelSpec


def _spec(url: str) -> ModelSpec:
    return ModelSpec(baseUrl=url, model="m", apiKey="k")


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8051",
        "http://localhost:11434/v1",
        "http://192.168.1.10/v1",
        "http://10.0.0.5/v1",
        "http://172.16.0.1/v1",
        "http://169.254.169.254/latest/meta-data",
        "http://[::1]:8000/v1",
    ],
)
def test_private_and_metadata_endpoints_are_rejected(url):
    with pytest.raises(OutboundAddressError):
        check_base_url(url)
    with pytest.raises(ValueError):
        _spec(url)


@pytest.mark.parametrize("url", ["ftp://example.com/v1", "file:///etc/passwd", "example.com/v1"])
def test_non_http_schemes_are_rejected(url):
    with pytest.raises(OutboundAddressError):
        check_base_url(url)


def test_public_endpoint_is_allowed(monkeypatch):
    # 用真实公网域名解析；CI 无网时跳过而不是假通过
    try:
        result = check_base_url("https://api.openai.com/v1")
    except OutboundAddressError as exc:
        pytest.skip(f"无法解析公网域名：{exc}")
    assert result == "https://api.openai.com/v1"


def test_private_endpoints_can_be_enabled_explicitly(monkeypatch):
    monkeypatch.setenv("MELEE_ALLOW_PRIVATE_ENDPOINTS", "1")
    assert check_base_url("http://127.0.0.1:11434/v1") == "http://127.0.0.1:11434/v1"
    # 元数据地址即使放开私有网段也必须拒绝
    with pytest.raises(OutboundAddressError):
        check_base_url("http://169.254.169.254/latest/meta-data")


def test_api_rejects_internal_base_url(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from services.match import api as api_module

    monkeypatch.setattr(api_module, "DB_PATH", tmp_path / "m.db")
    api_module.MATCHES.clear()
    client = TestClient(api_module.create_app())
    response = client.post(
        "/matches",
        json={
            "game": "twenty-questions",
            "players": [
                {"id": "A", "model": {"baseUrl": "http://127.0.0.1:8051", "model": "m", "apiKey": "k"}},
                {"id": "B"},
            ],
            "inlineWorker": False,
        },
    )
    assert response.status_code == 422, response.text
    assert "回环" in response.text or "私有" in response.text
