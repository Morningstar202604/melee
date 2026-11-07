"""凭据封存：让 API Key 不再以明文进入队列或数据库。

队列是落盘存储，任何带外部凭据的选手声明都必须使用进程级 Fernet 密钥
加密后才能入队；脚本选手和无外部凭据的普通比赛不需要密钥。

这不是 KMS，也不防"能读进程内存的攻击者"。它解决的是静态落盘。
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("melee.secrets")

PREFIX = "enc:v1:"


class SecretCipher:
    def __init__(self, key: str | None) -> None:
        self._fernet = None
        if key:
            from cryptography.fernet import Fernet

            self._fernet = Fernet(key)

    @property
    def enabled(self) -> bool:
        return self._fernet is not None

    def seal(self, value: str) -> str:
        if not value:
            return value
        if not self._fernet:
            return value
        return PREFIX + self._fernet.encrypt(value.encode("utf-8")).decode("ascii")

    def open(self, value: str) -> str:
        if not value or not value.startswith(PREFIX):
            return value
        if not self._fernet:
            raise RuntimeError("凭据是加密的，但没有配置 MELEE_SECRET_KEY，无法解开")
        return self._fernet.decrypt(value[len(PREFIX) :].encode("ascii")).decode("utf-8")


def generate_key() -> str:
    from cryptography.fernet import Fernet

    return Fernet.generate_key().decode("ascii")


def cipher_from_env() -> SecretCipher:
    from .settings import settings

    key = settings().secret_key.strip()
    try:
        cipher = SecretCipher(key or None)
    except (TypeError, ValueError):
        logger.error("MELEE_SECRET_KEY 格式无效：带外部 API Key 的比赛将被拒绝入队。")
        cipher = SecretCipher(None)
    if not cipher.enabled:
        logger.warning("未配置 MELEE_SECRET_KEY：带外部 API Key 的比赛将被拒绝入队。")
    return cipher


@dataclass
class SealedSpecs:
    """加密后的选手声明，可直接放进队列 payload。"""

    data: list[dict[str, Any]]
    encrypted: bool

    def as_dict(self) -> dict[str, Any]:
        return {"data": self.data, "encrypted": self.encrypted}


def seal_specs(specs: list[dict[str, Any]], cipher: SecretCipher) -> SealedSpecs:
    has_external_key = any(
        isinstance(spec.get(block_key), dict) and bool(spec[block_key].get("apiKey"))
        for spec in specs
        for block_key in ("model", "http")
    )
    if has_external_key and not cipher.enabled:
        raise RuntimeError("MELEE_SECRET_KEY 未配置，带 apiKey 的比赛不能安全入队")

    sealed: list[dict[str, Any]] = []
    for spec in specs:
        copy = json.loads(json.dumps(spec, ensure_ascii=False))
        for key in ("model", "http"):
            block = copy.get(key)
            if isinstance(block, dict) and block.get("apiKey"):
                block["apiKey"] = cipher.seal(block["apiKey"])
        sealed.append(copy)
    return SealedSpecs(data=sealed, encrypted=cipher.enabled)


def open_specs(sealed: dict[str, Any], cipher: SecretCipher) -> list[dict[str, Any]]:
    payload = sealed.get("data") if isinstance(sealed, dict) else None
    encrypted = bool(sealed.get("encrypted")) if isinstance(sealed, dict) else False
    if payload is None:
        payload = sealed if isinstance(sealed, list) else []
    out: list[dict[str, Any]] = []
    for spec in payload:
        copy = json.loads(json.dumps(spec, ensure_ascii=False))
        for key in ("model", "http"):
            block = copy.get(key)
            if isinstance(block, dict) and block.get("apiKey"):
                value = block["apiKey"]
                if encrypted and not str(value).startswith(PREFIX):
                    raise RuntimeError("任务凭据未加密，拒绝执行")
                block["apiKey"] = cipher.open(value)
        out.append(copy)
    return out


def fingerprint(value: str) -> str:
    """给日志用的短指纹：能对上是哪把 Key，但无法还原。"""
    if not value:
        return "-"
    digest = hashlib.sha256(value.encode("utf-8")).digest()
    return base64.b16encode(digest[:4]).decode("ascii").lower()


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="melee 凭据工具")
    parser.add_argument("--gen", action="store_true", help="生成一把新的 Fernet 密钥")
    args = parser.parse_args()
    if args.gen:
        print(generate_key())
        print()
        print("用法：把它设为环境变量 MELEE_SECRET_KEY 后重启服务。")
        return 0
    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
