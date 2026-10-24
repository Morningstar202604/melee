"""出站地址校验：BYOK 的 baseUrl 由调用方提供，必须挡住内网探测。

威胁模型：拿到 token 的人可以把 `baseUrl` 指向内网服务或云元数据地址，
让比赛服务带着自己的网络位置去请求，再把响应写进事件流——这就是 SSRF。

策略（fail-closed）：
- 只允许 http / https。
- 链接本地（169.254.0.0/16）、云元数据、保留地址：永远拒绝。
- 回环与私有网段：默认拒绝；本机跑 Ollama / vLLM 时用
  `MELEE_ALLOW_PRIVATE_ENDPOINTS=1` 显式放开。
- 域名解析出的每个地址都要过检查（防止一个域名同时解析到公网与内网）。
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit

# 云厂商元数据地址：即使放开私有网段也必须拒绝
_METADATA_HOSTS = {
    "metadata.google.internal",
    "metadata.goog",
    "instance-data",
}
_METADATA_IPS = {
    ipaddress.ip_address("169.254.169.254"),
    ipaddress.ip_address("169.254.170.2"),
    ipaddress.ip_address("fd00:ec2::254"),
    # 阿里云 / 腾讯云内网元数据
    ipaddress.ip_address("100.100.100.200"),
}

# 100.64.0.0/10（CGNAT）在 Python 里既不是 private 也不是 reserved，
# 但云厂商常把它用作元数据/内网地址，必须显式拒绝。
_CGNAT = ipaddress.ip_network("100.64.0.0/10")
# 部分云厂商的 IPv6 元数据/内网段
_ULA = ipaddress.ip_network("fc00::/7")


class OutboundAddressError(ValueError):
    """Base URL 指向了不允许的地址。"""


def _allow_private() -> bool:
    from .settings import settings

    return bool(settings().allow_private_endpoints)


def _classify(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> str | None:
    """返回拒绝原因；允许时返回 None。"""
    if address in _METADATA_IPS:
        return "云元数据地址"
    if address.is_link_local:
        return "链路本地地址"
    if address.is_multicast or address.is_reserved or address.is_unspecified:
        return "保留地址"
    if isinstance(address, ipaddress.IPv4Address) and address in _CGNAT:
        if _allow_private():
            return None
        return "运营商级 NAT 网段（云元数据常用）"
    if isinstance(address, ipaddress.IPv6Address) and address in _ULA:
        if _allow_private():
            return None
        return "IPv6 唯一本地地址"
    if address.is_loopback or address.is_private:
        if _allow_private():
            return None
        return "回环/私有网段（如确需连接本机模型，设置 MELEE_ALLOW_PRIVATE_ENDPOINTS=1）"
    return None


def check_base_url(url: str) -> str:
    """校验并返回规范化后的 URL；不合法就抛 OutboundAddressError。"""
    raw = (url or "").strip()
    host, port = _split_url(raw)
    if host.lower() in _METADATA_HOSTS:
        raise OutboundAddressError("baseUrl 指向云元数据地址")
    for literal in _resolve(host, port):
        reason = _classify_checked(literal)
        if reason:
            raise OutboundAddressError(f"baseUrl 指向{reason}：{host} → {literal}")
    return raw


def _split_url(raw: str) -> tuple[str, int]:
    if not raw:
        raise OutboundAddressError("baseUrl 不能为空")
    parts = urlsplit(raw)
    if parts.scheme not in {"http", "https"}:
        raise OutboundAddressError(f"baseUrl 只支持 http/https，收到 {parts.scheme or '空'}")
    host = parts.hostname
    if not host:
        raise OutboundAddressError("baseUrl 缺少主机名")
    return host, parts.port or (443 if parts.scheme == "https" else 80)


def _resolve(host: str, port: int) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise OutboundAddressError(f"baseUrl 无法解析：{host}（{exc}）") from None
    # 同一个域名常同时给出 IPv4/IPv6 多条记录，去重后逐条校验。
    return list(dict.fromkeys(info[4][0] for info in infos))


def _classify_checked(literal: str) -> str | None:
    try:
        return _classify(ipaddress.ip_address(literal))
    except ValueError:
        return None
