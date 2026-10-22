"""配置集中到一处：用 pydantic-settings 读环境变量并做类型校验。

之前 19 个 `os.environ.get("MELEE_...")` 散落在 8 个模块里，类型转换
（int/float/bool/逗号分隔列表）各写一遍，写错只有运行时才炸。
pydantic-settings 把这些统一成声明式字段：默认值、类型、解析都在一处，
启动时就能发现配置错误。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MELEE_", env_file=".env", extra="ignore")

    db: Path = Path("data/match.db")
    host: str = "127.0.0.1"
    port: int = 8099

    auth: str = "off"
    api_tokens: str = ""
    quota_matches: int = 20
    quota_renders: int = 10
    ticket_secret: str = ""
    secret_key: str = ""

    ratelimit: str = "memory"
    redis_url: str = ""
    trusted_proxy_hops: int = 0
    forwarded_allow_ips: str = ""

    cors_origins: str = "*"
    max_body_bytes: int = 2 * 1024 * 1024
    allow_private_endpoints: bool = False

    broadcast: str = "memory"

    inline_worker: bool = True
    worker: bool = True
    worker_kinds: str = "run-match,render-clip"
    worker_interval: float = 0.5
    worker_lease: float = 180.0
    # 单进程里并发跑几个任务：一场慢比赛不该把后面新开的局全堵住。
    workers: int = 4

    schedule: str = ""
    schedule_interval: float = 120.0
    schedule_per_tick: int = 1

    narrator: str = "rule"
    narrator_model: str = ""
    narrator_base_url: str = ""
    narrator_api_key: str = ""
    narrator_timeout: float = 20.0

    @field_validator("auth")
    @classmethod
    def _auth_mode(cls, value: str) -> str:
        return value if value in {"off", "token"} else "off"

    @field_validator("ratelimit", "broadcast")
    @classmethod
    def _known_backends(cls, value: str) -> str:
        return value.strip().lower() or "memory"

    @field_validator("allow_private_endpoints", "inline_worker", "worker", mode="before")
    @classmethod
    def _bool_from_env(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().lower() not in {"off", "0", "false", "no", ""}
        return value

    @property
    def tokens(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for item in self.api_tokens.split(","):
            name, _, token = item.strip().partition(":")
            if name and token:
                out[name.strip()] = token.strip()
        return out

    @property
    def season_ids(self) -> list[str]:
        return [item.strip() for item in self.schedule.split(",") if item.strip()]

    @property
    def origin_list(self) -> list[str]:
        items = [item.strip() for item in self.cors_origins.split(",") if item.strip()]
        return items or ["*"]

    @property
    def worker_kind_list(self) -> list[str]:
        return [item.strip() for item in self.worker_kinds.split(",") if item.strip()]


@lru_cache(maxsize=1)
def settings() -> Settings:
    """进程级缓存：环境变量在启动后不应频繁重读。

    测试或运行期需要改配置时调用 `reset_settings()`。
    """
    return Settings()


def reset_settings() -> None:
    settings.cache_clear()
