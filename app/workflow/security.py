"""SSRF 防护：HTTP 节点 URL 校验（spec-20）。.

拦截规则：
- scheme 仅允许 http/https
- host 解析后 IP 落入私网/环回/链路本地/元数据段则拒绝
- 可选白名单：WORKFLOW_HTTP_ALLOWED_HOSTS 非空时仅允许名单内 host
"""
import ipaddress
import socket
from urllib.parse import urlparse

from app.core.config import settings
from app.workflow.models import WorkflowValidationError


# 私网/保留段（spec-20 §3）
_PRIVATE_NETWORKS = [
    ipaddress.ip_network("127.0.0.0/8"),  # 环回
    ipaddress.ip_network("10.0.0.0/8"),  # A 类私网
    ipaddress.ip_network("172.16.0.0/12"),  # B 类私网
    ipaddress.ip_network("192.168.0.0/16"),  # C 类私网
    ipaddress.ip_network("169.254.0.0/16"),  # 链路本地（含云元数据 169.254.169.254）
    ipaddress.ip_network("::1/128"),  # IPv6 环回
    ipaddress.ip_network("fc00::/7"),  # IPv6 唯一本地
    ipaddress.ip_network("fe80::/10"),  # IPv6 链路本地
]

# 允许的 scheme（spec-20 §3）
_ALLOWED_SCHEMES = frozenset({"http", "https"})


def validate_http_url(url: str) -> None:
    """校验 HTTP URL 的 scheme + host，命中私网/环回/链路本地/元数据或不在白名单则抛 WorkflowValidationError。.

    Args:
        url: 待校验的 URL 字符串

    Raises:
        WorkflowValidationError: scheme 非法 / host 解析失败 / 命中私网段 / 不在白名单
    """
    # 1. 解析 URL
    try:
        parsed = urlparse(url)
    except Exception as e:
        raise WorkflowValidationError(f"invalid URL: {e}") from e

    # 2. scheme 校验
    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise WorkflowValidationError(
            f"scheme '{parsed.scheme}' not allowed; only http/https permitted"
        )

    # 3. 提取 host
    hostname = parsed.hostname
    if not hostname:
        raise WorkflowValidationError("URL missing host")

    # 4. DNS 解析 + IP 段校验
    try:
        # socket.getaddrinfo 返回 (family, type, proto, canonname, sockaddr)
        addr_infos = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
    except socket.gaierror as e:
        raise WorkflowValidationError(f"cannot resolve host '{hostname}': {e}") from e

    for addr_info in addr_infos:
        ip_str = addr_info[4][0]
        try:
            ip_addr = ipaddress.ip_address(ip_str)
        except ValueError:
            continue

        # 检查是否落入私网/保留段
        for network in _PRIVATE_NETWORKS:
            if ip_addr in network:
                raise WorkflowValidationError(
                    f"host '{hostname}' resolves to {ip_str} which is in "
                    f"private/loopback/link-local/reserved range {network}"
                )

    # 5. 白名单校验（仅当非空时生效）
    allowed_hosts = settings.WORKFLOW_HTTP_ALLOWED_HOSTS
    if allowed_hosts and hostname not in allowed_hosts:
        raise WorkflowValidationError(
            f"host '{hostname}' not in allowed hosts: {allowed_hosts}"
        )
