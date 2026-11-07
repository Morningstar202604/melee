"""选手声明新字段的边界契约：合法值通过、非法值 422、默认值=旧行为。

这些字段是"选手自带配置"（与 temperature 同公平地位），校验必须在建场时
挡住（422），不能等 worker 跑到一半才炸。
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _deterministic_dns(monkeypatch):
    """把 DNS 解析钉成确定的公网地址。

    本机若开着 fake-IP 隧道（Clash 等），api.openai.com 会被解析进
    198.18.0.0/15 保留网段，SSRF 校验把"公网域名"当"保留地址"拒绝——
    12 个纯参数测试因此随机红。它们测的是字段契约，不是网络；
    getaddrinfo 钉死后，测的才是实现。SSRF 本身的语义由
    test_resilience 的专用用例（NXDOMAIN/元数据/私网）负责。
    """
    import socket

    from services.match import outbound

    real_getaddrinfo = socket.getaddrinfo

    def _public(host, *args, **kwargs):
        return real_getaddrinfo("93.184.216.34", *args, **kwargs)

    monkeypatch.setattr(outbound.socket, "getaddrinfo", _public)


from pydantic import ValidationError

from services.match.player_specs import ModelSpec, PlayerSpec


def _model(**overrides):
    base = {"baseUrl": "https://api.openai.com/v1", "model": "m", "apiKey": "k"}
    base.update(overrides)
    return ModelSpec(**base)


class TestDefaultsAreCurrentBehavior:
    """三腿之一：不填新字段时，行为与升级前完全一致。"""

    def test_defaults(self):
        spec = _model()
        assert spec.systemPrompt == ""
        assert spec.topP is None
        assert spec.seed is None
        assert spec.stop == []
        assert spec.frequencyPenalty is None
        assert spec.presencePenalty is None
        assert spec.stream is False
        assert spec.includeHistory is False
        assert spec.protocol == "openai"
        assert spec.maxTokens == 400
        assert spec.temperature == 0.7

    def test_max_tokens_old_upper_still_valid(self):
        assert _model(maxTokens=4000).maxTokens == 4000

    def test_api_spec_accepts_player_without_new_fields(self):
        spec = PlayerSpec(id="A")
        assert spec.model is None


class TestSamplingFieldBounds:
    def test_top_p_accepts_bounds(self):
        assert _model(topP=0.0).topP == 0.0
        assert _model(topP=1.0).topP == 1.0

    def test_top_p_rejects_out_of_range(self):
        with pytest.raises(ValidationError):
            _model(topP=1.01)
        with pytest.raises(ValidationError):
            _model(topP=-0.1)

    def test_penalties_accept_bounds(self):
        assert _model(frequencyPenalty=-2.0, presencePenalty=2.0).frequencyPenalty == -2.0

    def test_penalties_reject_out_of_range(self):
        with pytest.raises(ValidationError):
            _model(frequencyPenalty=2.1)
        with pytest.raises(ValidationError):
            _model(presencePenalty=-2.1)

    def test_seed_accepts_any_int(self):
        assert _model(seed=42).seed == 42
        assert _model(seed=-7).seed == -7

    def test_stop_accepts_up_to_four(self):
        assert len(_model(stop=["a", "b", "c", "d"]).stop) == 4

    def test_stop_rejects_fifth_item(self):
        with pytest.raises(ValidationError):
            _model(stop=["a", "b", "c", "d", "e"])

    def test_stop_rejects_item_over_64_chars(self):
        with pytest.raises(ValidationError):
            _model(stop=["x" * 65])

    def test_stop_accepts_item_at_64_chars(self):
        assert _model(stop=["x" * 64]).stop == ["x" * 64]


class TestSystemPrompt:
    def test_accepts_4000_chars(self):
        assert len(_model(systemPrompt="人" * 4000).systemPrompt) == 4000

    def test_rejects_over_4000_chars(self):
        with pytest.raises(ValidationError):
            _model(systemPrompt="人" * 4001)


class TestMaxTokensLimit:
    def test_accepts_32000(self):
        assert _model(maxTokens=32000).maxTokens == 32000

    def test_rejects_32001(self):
        with pytest.raises(ValidationError):
            _model(maxTokens=32001)

    def test_rejects_below_64(self):
        with pytest.raises(ValidationError):
            _model(maxTokens=63)


class TestProtocol:
    def test_accepts_known_protocols(self):
        for protocol in ("openai", "anthropic", "gemini"):
            assert _model(protocol=protocol).protocol == protocol

    def test_rejects_unknown_protocol(self):
        with pytest.raises(ValidationError):
            _model(protocol="cohere")


class TestHttpTimeoutS:
    def test_upper_bound_raised_to_600(self):
        from services.match.player_specs import HTTPAgentSpec

        assert HTTPAgentSpec(endpoint="https://api.openai.com/act", timeoutS=600.0).timeoutS == 600.0
        with pytest.raises(ValidationError):
            HTTPAgentSpec(endpoint="https://api.openai.com/act", timeoutS=600.1)


class TestApi422Contract:
    """非法值必须在建场请求处 422，而不是进队列后炸 worker。"""

    def test_invalid_max_tokens_is_422(self, client):
        response = client.post(
            "/matches",
            json={
                "game": "twenty-questions",
                "players": [
                    {"id": "A", "model": {"baseUrl": "https://api.openai.com/v1", "model": "m", "apiKey": "k", "maxTokens": 32001}},
                    {"id": "B"},
                ],
                "inlineWorker": False,
            },
        )
        assert response.status_code == 422

    def test_invalid_top_p_is_422(self, client):
        response = client.post(
            "/matches",
            json={
                "game": "twenty-questions",
                "players": [
                    {"id": "A", "model": {"baseUrl": "https://api.openai.com/v1", "model": "m", "apiKey": "k", "topP": 5}},
                    {"id": "B"},
                ],
                "inlineWorker": False,
            },
        )
        assert response.status_code == 422

    def test_invalid_protocol_is_422(self, client):
        response = client.post(
            "/matches",
            json={
                "game": "twenty-questions",
                "players": [
                    {"id": "A", "model": {"baseUrl": "https://api.openai.com/v1", "model": "m", "apiKey": "k", "protocol": "mistral"}},
                    {"id": "B"},
                ],
                "inlineWorker": False,
            },
        )
        assert response.status_code == 422

    def test_valid_new_fields_create_match(self, client):
        response = client.post(
            "/matches",
            json={
                "game": "twenty-questions",
                "players": [
                    {
                        "id": "A",
                        "model": {
                            "baseUrl": "https://api.openai.com/v1",
                            "model": "m",
                            "apiKey": "k",
                            "maxTokens": 32000,
                            "topP": 0.9,
                            "seed": 7,
                            "stop": ["END"],
                            "frequencyPenalty": 0.5,
                            "presencePenalty": -0.5,
                            "stream": True,
                            "includeHistory": True,
                            "protocol": "anthropic",
                            "systemPrompt": "你是一名谨慎的选手。",
                        },
                    },
                    {"id": "B"},
                ],
                "inlineWorker": False,
            },
        )
        assert response.status_code == 201, response.text
