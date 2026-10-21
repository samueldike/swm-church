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


def _send(monkeypatch, path, handler, caption="Caption", large_files=False):
    real_client = httpx.AsyncClient
    monkeypatch.setattr(telegram.httpx, "AsyncClient",
                        lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))

    async def creds(self):
        self.is_local = large_files
        return "TOKEN", "@channel"

    monkeypatch.setattr(TelegramDistributor, "_get_credentials", creds)
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


# ─── Large files (self-hosted Bot API) ─────────────────────────────

def test_large_files_send_by_path_to_the_local_server(monkeypatch, audio_file):
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["body"] = request.read().decode()
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 7, "audio": {"file_id": "F"}}})

    assert _send(monkeypatch, audio_file, handler, large_files=True)["message_id"] == 7
    assert seen["url"].startswith(telegram.app_settings.TELEGRAM_LOCAL_API_BASE.rstrip("/") + "/botTOKEN/sendAudio")
    assert "file%3A%2F%2F" in seen["body"] or "file://" in seen["body"]   # handed over by path, never streamed


def test_over_the_limit_is_refused_never_compressed(monkeypatch, audio_file):
    monkeypatch.setattr(telegram.telegram_local, "CLOUD_LIMIT_BYTES", 1000)   # the test file is 2003 bytes
    with pytest.raises(telegram.TelegramUploadTooLarge):
        _send(monkeypatch, audio_file, lambda r: pytest.fail("must not upload"))
    assert not hasattr(telegram, "shrink_audio")


def test_api_id_and_hash_validation():
    from app.core.telegram_local import valid_api_hash, valid_api_id

    assert valid_api_id("1234567") and not valid_api_id("12a4567") and not valid_api_id("")
    assert valid_api_hash("0123456789abcdef0123456789abcdef")
    assert valid_api_hash("0123456789ABCDEF0123456789ABCDEF")          # pasted in capitals
    assert not valid_api_hash("0123456789abcdef0123456789abcde")       # 31 characters
    assert not valid_api_hash("0123456789abcdef0123456789abcdeg")      # not hex


def test_api_keys_are_encrypted_settings():
    from app.core.settings_cache import ENCRYPTED_SETTING_KEYS

    assert {"telegram_api_id", "telegram_api_hash"} <= ENCRYPTED_SETTING_KEYS


def test_large_files_need_both_the_switch_and_the_keys():
    from app.core.telegram_local import enabled

    keys = {"telegram_api_id": "1234567", "telegram_api_hash": "0123456789abcdef0123456789abcdef"}
    assert enabled({**keys, "telegram_large_files": "on"})
    assert not enabled({**keys, "telegram_large_files": ""})
    assert not enabled({"telegram_large_files": "on"})


def test_credentials_are_handed_to_the_container_only_when_changed(monkeypatch, tmp_path):
    from app.core import telegram_local

    monkeypatch.setattr(telegram_local.app_settings, "TELEGRAM_BOT_API_CONF_DIR", str(tmp_path))
    keys = {"telegram_api_id": "1234567", "telegram_api_hash": "0123456789ABCDEF0123456789ABCDEF"}

    assert telegram_local.sync_credentials(keys)
    assert (tmp_path / "api_id").read_text() == "1234567"
    assert (tmp_path / "api_hash").read_text() == "0123456789abcdef0123456789abcdef"
    stamp = (tmp_path / "stamp").read_text()

    telegram_local.sync_credentials(keys)                       # unchanged: no restart
    assert (tmp_path / "stamp").read_text() == stamp

    telegram_local.sync_credentials({})                         # removed in Settings: withdrawn
    assert not (tmp_path / "api_id").exists() and not (tmp_path / "api_hash").exists()
    assert (tmp_path / "stamp").read_text() != stamp


def test_nothing_is_written_outside_docker(monkeypatch):
    from app.core import telegram_local

    monkeypatch.setattr(telegram_local.app_settings, "TELEGRAM_BOT_API_CONF_DIR", "")
    assert telegram_local.sync_credentials({"telegram_api_id": "1234567", "telegram_api_hash": "0" * 32}) is False


# ─── Transcription queueing ────────────────────────────────────────

def test_transcriptions_run_on_their_own_queue():
    from app.celery_app import celery_app

    assert celery_app.conf.task_routes["app.tasks.transcribe_audio_task"]["queue"] == "transcription"


def test_long_jobs_are_not_handed_out_twice():
    from app.celery_app import celery_app

    # acks_late + Redis: a task still running past the visibility timeout is redelivered
    assert celery_app.conf.broker_transport_options["visibility_timeout"] >= 6 * 3600


def test_telegram_send_does_not_chain_a_transcription_by_default(monkeypatch):
    from app import tasks

    seen = {}

    async def record(sermon_id, transcribe, final=True):
        seen["transcribe"] = transcribe

    monkeypatch.setattr(tasks, "_distribute", record)
    tasks.distribute_audio_task.apply(args=(1,))
    assert seen["transcribe"] is False      # uploads queue their own transcription, independent of Telegram
