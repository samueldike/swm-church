"""Queue Celery tasks from request handlers without stalling the server.

``task.delay()`` is synchronous and keeps retrying an unreachable broker; called from an
async handler it froze the whole event loop (every visitor's request) for minutes when
Redis was down. ``enqueue`` probes the broker for a second and publishes on a thread.
"""
from __future__ import annotations

import asyncio
import logging
import socket
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

_DEFAULT_PORTS = {"redis": 6379, "rediss": 6380, "amqp": 5672, "amqps": 5671}


def _broker_up(url: str) -> bool:
    u = urlparse(url)
    if not u.hostname:  # memory:// and similar: nothing to probe
        return True
    try:
        socket.create_connection((u.hostname, u.port or _DEFAULT_PORTS.get(u.scheme, 6379)), timeout=1).close()
        return True
    except OSError:
        return False


def _send(task, args: tuple) -> bool:
    if not _broker_up(task.app.conf.broker_url or ""):
        return False
    task.apply_async(args, retry=False)
    return True


async def enqueue(task, *args) -> bool:
    """Queue ``task(*args)``. False when the broker is unreachable, so callers can fall back."""
    try:
        if await asyncio.to_thread(_send, task, args):
            return True
        logger.warning("Broker unreachable; %s not queued", task.name)
    except Exception:
        logger.warning("Could not queue %s", task.name, exc_info=True)
    return False
