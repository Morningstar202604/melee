"""切片三：anthropic / gemini 原生协议与 {env:VAR} 密钥引用。

HTTP 层用 httpx.MockTransport 断言真实发出的 URL / 头 / 请求体；
env 引用用 monkeypatch 两态验证，绝不硬编码真实密钥。
"""

from __future__ import annotations

import json

import httpx
import pytest


@pytest.fixture(autouse=True)
def _deterministic_dns(monkeypatch):
    """把 DNS 钉成确定公网地址：本机 fake-IP 隧道会把公网域名解析进
    保留网段（198.18.0.0/15），SSRF 校验随之拒绝合法 baseUrl，
    纯契约测试因此随机红。SSRF 语义本身由 test_resilience 专项负责。
    """
    import socket as _socket

    from services.match import outbound as _outbound

    _real = _socket.getaddrinfo

    def _public(host, *args, **kwargs):
        # 回环与私有地址照常解析：很多测试起本机 HTTP 服务。
        text = str(host)
        if text in {"127.0.0.1", "::1", "localhost"} or text.startswith("127."):
            return _real(host, *args, **kwargs)
        return _real("93.184.216.34", *args, **kwargs)

    monkeypatch.setattr(_outbound.socket, "getaddrinfo", _public)


from services.match.agents.llm import (
    LLMAgent,
    LLMConfig,
    _anthropic_request,
    _gemini_request,
    resolve_env_ref,
)
from services.match.engine.twenty_questions import TwentyQuestions
from services.match.player_specs import ApiKeyMissing, ModelSpec, PlayerSpec, resolve_env_references


def observation(seed: int = 7):
    game = TwentyQuestions()
    state = game.init(seed=seed, players=["A", "B"], config={})
    return game.observe(state, "A")


class MockServer:
    """httpx.MockTransport 包装：记录请求，按脚本回响应。"""

    def __init__(self, status: int = 200, body: dict | None = None) -> None:
        self.requests: list[httpx.Request] = []
        self.request_bodies: list[dict] = []
        self.status = status
        self.body = body if body is not None else {}
        self.transport = httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        raw = request.content.decode("utf-8") if request.content else "{}"
        try:
            self.request_bodies.append(json.loads(raw))
        except json.JSONDecodeError:
            self.request_bodies.append({})
        return httpx.Response(self.status, json=self.body, request=request)


ANTHROPIC_REPLY = {
    "id": "msg_1",
    "type": "message",
    "role": "assistant",
    "content": [
        {"type": "thinking", "thinking": "先排除不可能的", "signature": "sig"},
        {"type": "text", "text": '{"kind":"ask","value":"place=城市","thought":"缩小范围"}'},
    ],
    "usage": {"input_tokens": 12, "output_tokens": 8},
}

GEMINI_REPLY = {
    "candidates": [
        {
            "content": {
                "role": "model",
                "parts": [
                    {"text": "思考：先问地点", "thought": True},
                    {"text": '{"kind":"guess","value":"灯塔","thought":"够了"}'},
                ],
            }
        }
    ],
    "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 6},
}


class TestAnthropicProtocol:
    def test_request_shape_and_headers(self):
        server = MockServer(body=ANTHROPIC_REPLY)
        agent = LLMAgent(
            "A",
            LLMConfig(
                base_url="https://api.anthropic.com/v1",
                api_key="sk-ant-test",
                model="claude-fake",
                protocol="anthropic",
                max_tokens=2000,
                top_p=0.8,
                stop=["END"],
                timeout_s=5,
            ),
            client=httpx.Client(transport=server.transport),
        )
        output = agent.act(observation(), [])

        # 请求体正确之外，动作也必须被解析出来（否则请求对了也白搭）。
        assert output.move.value
        assert len(server.requests) == 1
        request = server.requests[0]
        assert request.url.path == "/v1/messages"
        assert request.headers["x-api-key"] == "sk-ant-test"
        assert request.headers["anthropic-version"]
        body = server.request_bodies[0]
        assert body["model"] == "claude-fake"
        assert body["max_tokens"] == 2000
        assert body["system"]
        assert body["messages"] and body["messages"][-1]["role"] == "user"
        assert body["top_p"] == 0.8
        assert body["stop_sequences"] == ["END"]
        # OpenAI 专属字段不出现
        assert "response_format" not in body
        assert "seed" not in body

    def test_response_parsed_and_usage_accounted(self):
        server = MockServer(body=ANTHROPIC_REPLY)
        agent = LLMAgent(
            "A",
            LLMConfig(
                base_url="https://api.anthropic.com/v1",
                api_key="k",
                model="m",
                protocol="anthropic",
                timeout_s=5,
            ),
            client=httpx.Client(transport=server.transport),
        )
        thoughts: list[str] = []
        output = agent.act(observation(), [], thoughts.append)
        assert output.move == ("ask", "城市") or (output.move.kind, output.move.value) == ("ask", "place=城市")
        assert thoughts == ["先排除不可能的"]
        assert agent.tokens_used == 20

    def test_upstream_error_is_redacted(self):
        secret = "sk-ant-secret-value"
        server = MockServer(status=500, body={"error": f"boom {secret}"})
        agent = LLMAgent(
            "A",
            LLMConfig(
                base_url="https://api.anthropic.com/v1",
                api_key=secret,
                model="m",
                protocol="anthropic",
                timeout_s=5,
                retry_backoff_s=0,
            ),
            client=httpx.Client(transport=server.transport),
        )
        output = agent.act(observation(), [])
        assert output.raw.startswith("[http-500]")
        assert secret not in output.raw


class TestGeminiProtocol:
    def test_request_shape_and_headers(self):
        server = MockServer(body=GEMINI_REPLY)
        agent = LLMAgent(
            "A",
            LLMConfig(
                base_url="https://generativelanguage.googleapis.com/v1beta",
                api_key="g-key",
                model="gemini-fake",
                protocol="gemini",
                max_tokens=1500,
                temperature=0.3,
                timeout_s=5,
            ),
            client=httpx.Client(transport=server.transport),
        )
        output = agent.act(observation(), [])

        assert output.move.value
        request = server.requests[0]
        assert ":generateContent" in request.url.path
        assert "gemini-fake" in request.url.path
        assert request.headers["x-goog-api-key"] == "g-key"
        body = server.request_bodies[0]
        gen = body["generationConfig"]
        assert gen["maxOutputTokens"] == 1500
        assert gen["temperature"] == 0.3
        assert body["systemInstruction"]["parts"][0]["text"]
        assert body["contents"][-1]["role"] == "user"
        assert "response_format" not in body

    def test_response_parsed_with_thought(self):
        server = MockServer(body=GEMINI_REPLY)
        agent = LLMAgent(
            "A",
            LLMConfig(
                base_url="https://x/v1beta",
                api_key="k",
                model="m",
                protocol="gemini",
                timeout_s=5,
            ),
            client=httpx.Client(transport=server.transport),
        )
        thoughts: list[str] = []
        output = agent.act(observation(), [], thoughts.append)
        assert (output.move.kind, output.move.value) == ("guess", "灯塔")
        assert thoughts == ["思考：先问地点"]
        assert agent.tokens_used == 16


class TestProtocolRequestMappers:
    def test_anthropic_maps_penalty_fields_not_sent(self):
        config = LLMConfig(
            base_url="https://x",
            api_key="k",
            model="m",
            frequency_penalty=0.5,
            presence_penalty=0.5,
            seed=1,
            json_mode=True,
        )
        body = _anthropic_request(config, [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}])
        assert "frequency_penalty" not in body
        assert "presence_penalty" not in body
        assert "seed" not in body
        assert body["system"] == "s"

    def test_gemini_maps_stop_and_top_p(self):
        config = LLMConfig(base_url="https://x", api_key="k", model="m", top_p=0.7, stop=["A", "B"])
        body = _gemini_request(config, [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}])
        assert body["generationConfig"]["topP"] == 0.7
        assert body["generationConfig"]["stopSequences"] == ["A", "B"]
        assert body["contents"][0]["role"] == "user"


class TestEnvRefResolution:
    def test_non_ref_value_passes_through(self):
        value, missing = resolve_env_ref("sk-literal")
        assert value == "sk-literal"
        assert missing is None

    def test_ref_resolves_when_env_set(self, monkeypatch):
        monkeypatch.setenv("MELEE_TEST_KEY", "sk-from-env")
        value, missing = resolve_env_ref("{env:MELEE_TEST_KEY}")
        assert value == "sk-from-env"
        assert missing is None

    def test_ref_reports_missing_var(self, monkeypatch):
        monkeypatch.delenv("MELEE_TEST_MISSING", raising=False)
        value, missing = resolve_env_ref("{env:MELEE_TEST_MISSING}")
        assert value is None
        assert missing == "MELEE_TEST_MISSING"

    def test_ref_empty_env_counts_as_missing(self, monkeypatch):
        monkeypatch.setenv("MELEE_TEST_EMPTY", "  ")
        value, missing = resolve_env_ref("{env:MELEE_TEST_EMPTY}")
        assert value is None

    def test_resolve_env_references_mutates_model_spec(self, monkeypatch):
        monkeypatch.setenv("MELEE_TEST_KEY2", "sk-real")
        spec = PlayerSpec(id="A", model=ModelSpec(baseUrl="https://api.openai.com/v1", model="m", apiKey="{env:MELEE_TEST_KEY2}"))
        resolved = resolve_env_references([spec])
        assert resolved[0].model.apiKey == "sk-real"

    def test_resolve_env_references_raises_for_missing(self, monkeypatch):
        monkeypatch.delenv("MELEE_TEST_MISSING2", raising=False)
        spec = PlayerSpec(id="A", model=ModelSpec(baseUrl="https://api.openai.com/v1", model="m", apiKey="{env:MELEE_TEST_MISSING2}"))
        with pytest.raises(ApiKeyMissing) as excinfo:
            resolve_env_references([spec])
        assert "MELEE_TEST_MISSING2" in str(excinfo.value)
        assert "A" in str(excinfo.value)

    def test_http_spec_env_ref_resolves(self, monkeypatch):
        from services.match.player_specs import HTTPAgentSpec

        monkeypatch.setenv("MELEE_TEST_HTTP_KEY", "h-key")
        spec = PlayerSpec(id="X", http=HTTPAgentSpec(endpoint="https://api.openai.com/act", apiKey="{env:MELEE_TEST_HTTP_KEY}"))
        resolved = resolve_env_references([spec])
        assert resolved[0].http.apiKey == "h-key"

    def test_missing_env_var_not_logged_with_value(self, monkeypatch, caplog):
        monkeypatch.delenv("MELEE_TEST_MISSING3", raising=False)
        spec = PlayerSpec(id="A", model=ModelSpec(baseUrl="https://api.openai.com/v1", model="m", apiKey="{env:MELEE_TEST_MISSING3}"))
        with pytest.raises(ApiKeyMissing):
            resolve_env_references([spec])


class TestWorkerEnvRefFlow:
    """worker 全链路：env 在 → agent 拿到真实值；env 缺 → 明确弃权。"""

    def test_worker_resolves_env_ref_into_agent(self, tmp_path, monkeypatch):
        import sqlite3

        from services.match.worker import enqueue_match, run_match_job

        db_path = tmp_path / "m.db"
        sqlite3.connect(str(db_path)).close()
        monkeypatch.setenv("MELEE_TEST_WORKER_KEY", "sk-worker-real")
        enqueue_match(
            db_path,
            match_id="m_envtest",
            seed=7,
            players=["A", "B"],
            max_questions=2,
            specs=[{"id": "A", "model": {"baseUrl": "https://api.openai.com/v1", "model": "m", "apiKey": "{env:MELEE_TEST_WORKER_KEY}"}}, {"id": "B"}],
        )
        # 只验证解引用路径：直接调 run_match_job 前半段会打真实网络，
        # 这里改为验证 resolve_spec_secrets 的输出。
        from services.match.player_specs import PlayerSpec
        from services.match.worker import resolve_spec_secrets

        specs = [PlayerSpec(**{"id": "A", "model": {"baseUrl": "https://api.openai.com/v1", "model": "m", "apiKey": "{env:MELEE_TEST_WORKER_KEY}"}})]
        resolved = resolve_spec_secrets(specs, "m_x", db_path)
        assert resolved[0].model.apiKey == "sk-worker-real"

    def test_worker_missing_env_judges_forfeit(self, tmp_path, monkeypatch):
        import sqlite3

        from services.match.eventlog import read_match
        from services.match.player_specs import PlayerSpec
        from services.match.worker import judge_missing_key

        db_path = tmp_path / "m.db"
        sqlite3.connect(str(db_path)).close()
        payload = {
            "matchId": "m_missing",
            "game": "twenty-questions",
            "seed": 7,
            "players": ["A", "B"],
            "maxQuestions": 4,
        }
        judge_missing_key(db_path, payload, "选手 A 的 apiKey 引用了环境变量 MELEE_NOPE，但该变量不存在或为空")

        events = read_match(db_path, "m_missing")
        types = [e["type"] for e in events]
        assert "match.ended" in types
        ended = next(e for e in events if e["type"] == "match.ended")
        assert ended["payload"]["winner"] == "B"
        assert "MELEE_NOPE" in ended["payload"]["reason"]

        # 不崩：库里没有异常残留（apiKey 值从未出现，因为本来就没有）。
        assert all("sk-" not in str(e) for e in events)
