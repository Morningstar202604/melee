"""凭据封存：让 API Key 不再以明文进入队列或数据库。

背景：写路径改成队列之后，带 Key 的选手声明必须序列化进 `jobs.payload`。
如果照原样存，Key 就落盘了，这与"Key 只在内存里活一次"的原始约束冲突。

做法：
- 用一个进程级密钥（`MELEE_SECRET_KEY`，Fernet 格式）对称加密。
- 队列里只存密文；worker 执行时在内存里解开，用完即弃。
- 没有配置密钥时退化为"不加密但显式标记"，并在启动时告警——
  宁可让运维看见告警，也不要假装安全。

这不是 KMS，也不防"能读进程内存的攻击者"。它解决的是**静态落盘**。
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
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
    key = os.environ.get("MELEE_SECRET_KEY", "").strip()
    cipher = SecretCipher(key or None)
    if not cipher.enabled:
        logger.warning(
            "未配置 MELEE_SECRET_KEY：选手 API Key 会以明文写入任务队列。"
            "生产环境请设置该变量（可用 python -m services.match.secrets --gen 生成）。"
        )
    return cipher


@dataclass
class SealedSpecs:
    """加密后的选手声明，可直接放进队列 payload。"""

    data: list[dict[str, Any]]
    encrypted: bool

    def as_dict(self) -> dict[str, Any]:
        return {"data": self.data, "encrypted": self.encrypted}


def seal_specs(specs: list[dict[str, Any]], cipher: SecretCipher) -> SealedSpecs:
    sealed: list[dict[str, Any]] = []
    for spec in specs:
        copy = json.loads(json.dumps(spec, ensure_ascii=False))
        model = copy.get("model")
        if isinstance(model, dict) and model.get("apiKey"):
            model["apiKey"] = cipher.seal(model["apiKey"])
        sealed.append(copy)
    return SealedSpecs(data=sealed, encrypted=cipher.enabled)


def open_specs(sealed: dict[str, Any], cipher: SecretCipher) -> list[dict[str, Any]]:
    payload = sealed.get("data") if isinstance(sealed, dict) else None
    if payload is None:
        payload = sealed if isinstance(sealed, list) else []
    out: list[dict[str, Any]] = []
    for spec in payload:
        copy = json.loads(json.dumps(spec, ensure_ascii=False))
        model = copy.get("model")
        if isinstance(model, dict) and model.get("apiKey"):
            model["apiKey"] = cipher.open(model["apiKey"])
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
