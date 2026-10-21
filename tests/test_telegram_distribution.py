"""Telegram distribution: real error reasons, retry classification, caption limit, retry schedule.

No network: Telegram is a fake httpx transport and the worker's upload step is stubbed.
"""
import asyncio
import os
import tempfile

import httpx
import pytest

from app.core import telegram
from app.core.telegram import TelegramDistributor, TelegramError


@pytest.fixture
def audio_file():
    fd, path = tempfile.mkstemp(suffix=".mp3")
    os.write(fd, b"ID3" + b"\0" * 2000)
    os.close(fd)
    yield path
    os.unlink(path)


def _send(monkeypatch, path, handler, caption="Caption"):
    real_client = httpx.AsyncClient
    monkeypatch.setattr(telegram.httpx, "AsyncClient",
                        lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))

    async def creds(self):
        return "TOKEN", "@channel"

    monkeypatch.setattr(TelegramDistributor, "_get_credentials", creds)
    monkeypatch.setattr(telegram.app_settings, "TELEGRAM_LOCAL_API", False)
    return asyncio.run(TelegramDistributor(None).send_audio(path, "m.mp3", "Title", caption))


def test_success_returns_message_and_file_ids(monkeypatch, audio_file):
    ok = {"ok": True, "result": {"message_id": 42, "audio": {"file_id": "F1"}}}
    assert _send(monkeypatch, audio_file, lambda r: httpx.Response(200, json=ok)) == {"message_id": 42, "file_id": "F1"}


def test_flood_control_is_transient_and_keeps_retry_after(monkeypatch, audio_file):
    body = {"ok": False, "error_code": 429, "description": "Too Many Requests: retry after 7", "parameters": {"retry_after": 7}}
    with pytest.raises(TelegramError) as exc:
        _send(monkeypatch, audio_file, lambda r: httpx.Response(429, json=body))
    assert exc.value.transient and exc.value.retry_after == 7
    assert "Too Many Requests" in str(exc.value)


def test_telegram_server_error_is_transient(monkeypatch, audio_file):
    with pytest.raises(TelegramError) as exc:
        _send(monkeypatch, audio_file, lambda r: httpx.Response(502, text="Bad Gateway"))
    assert exc.value.transient and "HTTP 502" in str(exc.value)


def test_permanent_rejection_reports_telegrams_reason(monkeypatch, audio_file):
    body = {"ok": False, "error_code": 400, "description": "Bad Request: chat not found"}
    with pytest.raises(TelegramError) as exc:
        _send(monkeypatch, audio_file, lambda r: httpx.Response(400, json=body))
    assert not exc.value.transient and "chat not found" in str(exc.value)


def test_network_failure_is_transient(monkeypatch, audio_file):
    def boom(request):
        raise httpx.ConnectError("down", request=request)

    with pytest.raises(TelegramError) as exc:
        _send(monkeypatch, audio_file, boom)
    assert exc.value.transient


def test_long_caption_is_trimmed_to_telegrams_limit(monkeypatch, audio_file):
    seen = {}

    def handler(request):
        body = request.read().decode("latin-1")
        start = body.index('name="caption"') + len('name="caption"\r\n\r\n')
        seen["caption"] = body[start:body.index("\r\n--", start)].encode("latin-1").decode("utf-8")
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1, "audio": {"file_id": "F"}}})

    _send(monkeypatch, audio_file, handler, caption="x" * 5000)
    assert len(seen["caption"]) == telegram.CAPTION_LIMIT and seen["caption"].endswith("…")


def test_transient_failures_retry_then_give_up(monkeypatch):
    from app import tasks

    calls = []

    async def flaky(sermon_id, transcribe, final=True):
        calls.append(final)
        raise TelegramError("Telegram: HTTP 502", transient=True)

    monkeypatch.setattr(tasks, "_distribute", flaky)
    tasks.distribute_audio_task.apply(args=(1,))  # eager: retries run inline, countdowns ignored
    assert len(calls) == len(tasks._DISTRIBUTE_BACKOFF) + 1
    assert calls[-1] is True and not any(calls[:-1])  # only the last attempt records "failed"


def test_permanent_failure_is_not_retried(monkeypatch):
    from app import tasks

    calls = []

    async def rejected(sermon_id, transcribe, final=True):
        calls.append(final)
        raise TelegramError("Telegram: Bad Request: chat not found", transient=False)

    monkeypatch.setattr(tasks, "_distribute", rejected)
    tasks.distribute_audio_task.apply(args=(1,))
    assert len(calls) == 1
