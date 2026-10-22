"""Telegram archive: pure logic (titles, file names, index, ZIP stream, private paths). No network."""
import csv
import io
import os
import time
import zipfile
from datetime import datetime
from types import SimpleNamespace

from app.core import telegram_archive as ta


def test_draft_title_prefers_audio_title_then_caption_then_file_name():
    when = datetime(2019, 3, 10)
    assert ta.draft_title("The Grace That Repositions", "ignored", "x.mp3", when) == "The Grace That Repositions"
    assert ta.draft_title(None, "Roots in Dry Ground\nPastor Samuel · Sunday", "x.mp3", when) == "Roots in Dry Ground"
    assert ta.draft_title("", "", "Knowledge_and_Inheritance.mp3", when) == "Knowledge and Inheritance"
    assert ta.draft_title(None, None, "a.mp3", when) == "Audio message 10 Mar 2019"


def test_safe_name_keeps_extension_and_strips_paths():
    assert ta.safe_name("../../etc/Sunday Service (1).mp3") == "etc_Sunday_Service_1.mp3"
    assert ta.safe_name("") == "audio.mp3"
    assert ta.safe_name("voice").endswith(".mp3")


def test_post_links_for_public_and_private_channels():
    assert ta.post_link("1234", "swmchannel", 55) == "https://t.me/swmchannel/55"
    assert ta.post_link("1234", None, 55) == "https://t.me/c/1234/55"


def test_is_audio_accepts_audio_voice_and_audio_documents_only():
    f = lambda mime: SimpleNamespace(mime_type=mime)
    assert ta.is_audio(SimpleNamespace(file=f("audio/mpeg"), audio=None, voice=None))
    assert ta.is_audio(SimpleNamespace(file=f("application/octet-stream"), audio=object(), voice=None))
    assert not ta.is_audio(SimpleNamespace(file=f("video/mp4"), audio=None, voice=None))
    assert not ta.is_audio(SimpleNamespace(file=None, audio=None, voice=None))


def _item(**kw):
    base = dict(channel_id="1234", message_id=7, posted_at=datetime(2020, 1, 5, 9, 30), audio_title=None, performer=None,
                caption="=HYPERLINK(\"http://evil\")", file_name="a.mp3", stored_path="1234/2020/a.mp3", size=10,
                duration=60, sha256="ab" * 32, status="saved", sermon_id=None)
    base.update(kw)
    return SimpleNamespace(**base)


def test_index_csv_neutralises_formulas_and_links_posts():
    rows = list(csv.reader(io.StringIO(ta.manifest_csv([_item()], None).lstrip("﻿"))))
    assert rows[0][0] == "posted_at_utc" and rows[1][2] == "https://t.me/c/1234/7"
    assert rows[0][6] == "caption" and rows[1][6].startswith("'=")   # caption cannot run as a spreadsheet formula
    assert rows[1][3] == ""                                     # private channel: no embed link


def test_zip_stream_is_a_valid_archive_with_index_and_identical_files(tmp_path):
    a, b = tmp_path / "a.mp3", tmp_path / "b.mp3"
    a.write_bytes(os.urandom(3 * 1024 * 1024 + 17))
    b.write_bytes(b"ID3short")
    data = b"".join(ta.zip_stream([(str(a), "2020/a.mp3", datetime(2020, 1, 5)), (str(b), "2021/b.mp3", datetime(1970, 1, 1))], "x,y\n"))
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        assert zf.testzip() is None
        assert zf.namelist() == ["index.csv", "2020/a.mp3", "2021/b.mp3"]
        assert zf.read("2020/a.mp3") == a.read_bytes() and zf.read("2021/b.mp3") == b"ID3short"
        assert zf.getinfo("2020/a.mp3").compress_type == zipfile.ZIP_STORED


def test_archive_paths_resolve_privately_and_cannot_escape(monkeypatch, tmp_path):
    from app.core import media

    root = tmp_path / "telegram-archive"
    (root / "1234").mkdir(parents=True)
    (root / "1234" / "a.mp3").write_bytes(b"x")
    (tmp_path / "secret.txt").write_text("no")
    monkeypatch.setattr(media.settings, "TELEGRAM_ARCHIVE_DIR", str(root))
    assert media.to_abspath("archive/1234/a.mp3") == os.path.realpath(root / "1234" / "a.mp3")
    assert media.to_abspath("archive/../secret.txt") is None
    assert media.to_abspath("archive/1234/missing.mp3") is None


def test_archive_dir_is_never_inside_the_public_media_root(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "TELEGRAM_ARCHIVE_DIR", "")
    media_root = os.path.realpath(settings.media_root)
    assert not os.path.realpath(settings.telegram_archive_dir).startswith(media_root + os.sep)


def test_a_job_without_a_heartbeat_is_not_running():
    assert ta.job_running({"state": "running", "heartbeat": time.time()})
    assert not ta.job_running({"state": "running", "heartbeat": time.time() - 3600})
    assert not ta.job_running({"state": "done", "heartbeat": time.time()})


def test_login_and_job_state_are_encrypted_or_plain_as_intended():
    from app.core.settings_cache import ENCRYPTED_SETTING_KEYS

    assert ta.SESSION_SETTING in ENCRYPTED_SETTING_KEYS       # a Telegram login: never stored readable
    assert ta.JOB_SETTING not in ENCRYPTED_SETTING_KEYS       # progress only


def test_sizes_read_naturally():
    assert ta.human_size(1003) == "1003 bytes"
    assert ta.human_size(3 * 1024 ** 2) == "3.0 MB" and ta.human_size(int(2.5 * 1024 ** 3)) == "2.5 GB"


def test_embed_links_only_for_public_channels():
    assert ta.embed_link("swmchannel", 9) == "https://t.me/swmchannel/9?embed=1"
    assert ta.embed_link(None, 9) is None


def test_each_file_carries_its_posts_caption_and_details():
    f = SimpleNamespace(title="Excellence as Worship", performer="Pastor Samuel", duration=1800.4, name="a.mp3",
                        mime_type="audio/mpeg", size=1003)
    msg = SimpleNamespace(id=12, file=f, message="Full caption\nwith lines", date=datetime(2019, 1, 1, 9),
                          edit_date=None, views=50, forwards=2, post_author="Media team", voice=None)
    meta = ta.post_metadata(msg, "HQ", "swmchannel", "1234", "ab" * 32)
    assert meta["caption"] == "Full caption\nwith lines" and meta["telegram_link"] == "https://t.me/swmchannel/12"
    assert meta["telegram_embed"] == "https://t.me/swmchannel/12?embed=1" and meta["duration_seconds"] == 1800
    assert meta["posted_at_utc"] == "2019-01-01 09:00:00" and meta["edited_at_utc"] is None
    assert {"title", "performer", "sha256", "views", "forwards", "post_author", "voice_note"} <= meta.keys()


def test_only_telegrams_own_errors_are_blamed_on_telegram():
    from telethon import errors

    ours = ta._explain(RuntimeError("got Future attached to a different loop"))
    assert not str(ours).startswith("Telegram said") and "try again" in str(ours).lower()
    theirs = ta._explain(errors.RPCError(request=None, message="CHANNEL_PRIVATE", code=400))
    assert str(theirs).startswith("Telegram said")
