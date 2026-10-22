"""The visitor's IP address, for rate limits and analytics (SEC-03).

nginx (nginx/default.conf) sets ``X-Real-IP $remote_addr`` — overwriting anything the visitor sent — and APPENDS
the address it saw to ``X-Forwarded-For``. The FIRST X-Forwarded-For entry is therefore whatever the visitor typed,
which made every per-IP throttle bypassable. Forwarded headers are only believed when the request really came
through a proxy on a private network; then X-Real-IP wins, else the LAST X-Forwarded-For entry (the proxy's own).
"""
from __future__ import annotations

import ipaddress
from collections.abc import Mapping


def _from_proxy(peer: str) -> bool:
    try:
        ip = ipaddress.ip_address(peer)
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback


def resolve(headers: Mapping[str, str], peer: str | None) -> str:
    """``headers`` with lower-case names; ``peer`` = the TCP peer address (request.client.host)."""
    peer = peer or ""
    if peer and not _from_proxy(peer):
        return peer                                   # reached us directly: forwarded headers are the visitor's word
    real = (headers.get("x-real-ip") or "").strip()
    if real:
        return real
    hops = [h.strip() for h in (headers.get("x-forwarded-for") or "").split(",") if h.strip()]
    return hops[-1] if hops else (peer or "unknown")


def client_ip(request) -> str:
    return resolve(request.headers, request.client.host if request.client else None)
