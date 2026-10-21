"""Books Pub: pure-logic tests (no database, no network, no AI calls)."""
import io
import json
from datetime import date
from types import SimpleNamespace

import pytest

from app.core import ai, publication_prompts as prompts
from app.core.publication_export import build_docx, build_pdf
from app.core.publication_html import parse_blocks, plain_text, sanitize, split_blocks, word_count
from app.core.publications import build_document, compute_period, numbered_subtopics, progress


# ─── duration ────────────────────────────────────────────────────────

@pytest.mark.parametrize("kind,year,idx,days", [
    ("year", 2027, None, 365), ("year", 2028, None, 366),
    ("half", 2027, 1, 181), ("half", 2027, 2, 184),
    ("quarter", 2027, 1, 90), ("quarter", 2028, 1, 91), ("quarter", 2027, 3, 92),
    ("month", 2027, 2, 28), ("month", 2028, 2, 29),
])
def test_compute_period_days(kind, year, idx, days):
    start, end, label = compute_period(kind, year, idx, None, None)
    assert (end - start).days + 1 == days
    assert str(year) in label


def test_compute_period_custom_and_errors():
    start, end, _ = compute_period("custom", None, None, date(2027, 1, 1), date(2027, 1, 31))
    assert (end - start).days == 30
    for bad in (
        ("custom", None, None, date(2027, 2, 1), date(2027, 1, 1)),
        ("custom", None, None, None, None),
        ("custom", None, None, date(2027, 1, 1), date(2029, 1, 1)),
        ("quarter", 2027, 5, None, None),
        ("month", 2027, 13, None, None),
        ("year", 99, None, None, None),
        ("decade", 2027, None, None, None),
    ):
        with pytest.raises(ValueError):
            compute_period(*bad)


# ─── sanitiser ───────────────────────────────────────────────────────

def test_sanitize_strips_active_content_and_keeps_formatting():
    dirty = (
        '<script>alert(1)</script><p onclick="x()" style="text-align:center;background:url(javascript:x);color:#333">'
        'Hi <strong>there</strong> <a href="javascript:alert(1)">bad</a> <a href="https://ok.example">ok</a>'
        '<img src=x onerror=y><iframe src="//evil"></iframe></p>'
    )
    clean = sanitize(dirty)
    assert "script" not in clean and "onclick" not in clean and "onerror" not in clean
    assert "iframe" not in clean and "<img" not in clean and "javascript:" not in clean
    assert "url(" not in clean
    assert 'style="text-align: center; color: #333"' in clean
    assert "<strong>there</strong>" in clean and 'href="https://ok.example"' in clean


def test_sanitize_closes_unbalanced_tags_and_escapes_text():
    assert sanitize("<p>one <b>two").endswith("</b></p>")
    assert "&lt;b&gt;" in sanitize("<p>1 &lt;b&gt; 2</p>")


def test_sanitize_only_allows_known_block_keys_and_classes():
    out = sanitize('<div data-block="day:12" class="pub-day evil"><div data-block="x:1">a</div></div>')
    assert 'data-block="day:12"' in out and "evil" not in out and 'data-block="x:1"' not in out


# ─── document assembly ───────────────────────────────────────────────

def _pub(kind="devotionals", bodies=(("Title A", ["one", "two"]),), start=date(2027, 1, 1), end=date(2027, 1, 10), document_html=None):
    """A stand-in for a loaded Publication (only what build_document/progress read)."""
    sources, sub_id = [], 0
    for i, (main, subs) in enumerate(bodies, start=1):
        subtopics = []
        for title in subs:
            sub_id += 1
            body = f"Body of {title}." if title else None
            subtopics.append(SimpleNamespace(
                id=sub_id, title=title or "empty", body=body, has_content=bool(body), scripture="Ps 23:1 (KJV)" if body else None,
                action_plan="Act." if body else None, prayer_point="Pray." if body else None,
                word_count=3, content_status="done" if body else "none", points=[]))
        sources.append(SimpleNamespace(id=i, main_title=main, sermon=SimpleNamespace(title=main, transcript="x", transcription_status="completed"),
                                       subtopics=subtopics, topics_status="done"))
    return SimpleNamespace(
        kind=kind, is_devotional=kind == "devotionals", title="My Book", author_name="Pastor", duration_type="custom",
        year=None, period_index=None, start_date=start, end_date=end, sources=sources, document_html=document_html,
        total_days=(end - start).days + 1 if start and end else None)


def test_devotional_numbering_skips_topics_without_content():
    pub = _pub(bodies=(("Main", ["a", "", "b"]),))
    assert [n for n, _, _ in numbered_subtopics(pub)] == [1, 2]
    doc = build_document(pub)
    assert "Day 1: Main" in doc and "Day 2: Main" in doc and "Day 3" not in doc
    assert "Action Plan:" in doc and "Prayer Point:" in doc and "Ps 23:1 (KJV)" in doc


def test_progress_reports_missing_and_overflow_days():
    p = progress(_pub(bodies=(("Main", ["a", "b", "c"]),), end=date(2027, 1, 10)))
    assert (p["days_total"], p["with_content"], p["days_missing"], p["overflow"]) == (10, 3, 7, 0)
    p = progress(_pub(bodies=(("Main", ["a", "b", "c"]),), end=date(2027, 1, 2)))
    assert (p["days_total"], p["days_filled"], p["days_missing"], p["overflow"]) == (2, 2, 0, 1)


def test_update_keeps_edits_and_renumbers_but_rebuild_discards_them():
    pub = _pub(bodies=(("Main", ["a", "b"]),))
    first = build_document(pub)
    edited = first.replace("Body of a.", "MY EDIT of a.")
    # a new message lands *before* the existing one, shifting every day down
    pub2 = _pub(bodies=(("New", ["n"]), ("Main", ["a", "b"])), document_html=edited)
    # ids are per-call counters; align the surviving subtopics with the ids used in `first`
    pub2.sources[1].subtopics[0].id, pub2.sources[1].subtopics[1].id, pub2.sources[0].subtopics[0].id = 1, 2, 3
    updated = build_document(pub2, edited)
    assert "MY EDIT of a." in updated
    assert "Day 1: New" in updated and "Day 2: Main" in updated and "Day 3: Main" in updated
    assert "Day 1: Main" not in updated
    rebuilt = build_document(pub2, edited, rebuild=True)
    assert "MY EDIT" not in rebuilt


def test_update_drops_days_whose_topic_was_deleted():
    pub = _pub(bodies=(("Main", ["a", "b"]),))
    doc = build_document(pub)
    pub.sources[0].subtopics.pop(0)
    updated = build_document(pub, doc)
    assert "Body of a." not in updated and "Day 1: Main" in updated and "Body of b." in updated


def test_book_document_has_chapters_and_sections_without_devotional_extras():
    pub = _pub(kind="books", bodies=(("Faith", ["Sec 1", "Sec 2"]), ("Hope", ["Sec 3"])))
    doc = build_document(pub)
    assert "Chapter 1: Faith" in doc and "Chapter 2: Hope" in doc
    assert "Sec 1" in doc and "Sec 3" in doc and "Prayer Point" not in doc and "Day 1" not in doc
    keys = [k for k, _ in split_blocks(doc)]
    assert keys[0] == "cover" and "chapter:1" in keys and "section:3" in keys


def test_split_blocks_round_trips_nested_markup():
    doc = '<div data-block="cover"><h1>T</h1></div><div data-block="day:1"><ul><li>x</li></ul><p>y</p></div>'
    assert split_blocks(doc) == [("cover", "<h1>T</h1>"), ("day:1", "<ul><li>x</li></ul><p>y</p>")]


# ─── export ──────────────────────────────────────────────────────────

RICH = (
    '<div data-block="cover"><h1 style="text-align: center; margin-top: 100pt">Scent “Daily” — 2027</h1></div>'
    '<div data-block="day:1"><hr class="pb"><h2>Day 1: Faith</h2><p><em>Hab 2:4 (NKJV)</em></p>'
    '<p style="text-align: justify">Plain <strong>bold</strong> <u>under</u> <a href="https://x.example">link</a> ọ £5 ellipsis…</p>'
    '<ol><li>first</li><li>second</li></ol><ul><li>dot</li></ul>'
    '<table><tr><td>a</td><td>b</td></tr></table><hr class="pb"><p>Second page</p></div>'
)


def test_docx_export_is_a_valid_document():
    import docx

    data = build_docx("T", RICH, "Pastor")
    assert data[:2] == b"PK"
    text = "\n".join(p.text for p in docx.Document(io.BytesIO(data)).paragraphs)
    assert "Day 1: Faith" in text and "1. first" in text and "Second page" in text
    assert len(docx.Document(io.BytesIO(data)).tables) == 1


def test_pdf_export_is_a_valid_multipage_pdf():
    data = build_pdf("T", RICH, "Pastor")
    assert data.startswith(b"%PDF-") and data.count(b"/Type /Page\n") + data.count(b"/Type /Page ") >= 2


def test_exports_handle_an_empty_document():
    assert build_docx("T", "")[:2] == b"PK"
    assert build_pdf("T", "").startswith(b"%PDF-")


def test_plain_text_and_word_count():
    assert plain_text("<p>Hello <b>big</b> world</p><p>Again</p>") == "Hello big world\nAgain"
    assert word_count("God’s grace, it's enough — really") == 5
    assert [b.kind for b in parse_blocks('<h1>a</h1><hr class="pb"><p>b</p>')] == ["h1", "pagebreak", "p"]


# ─── AI plumbing ─────────────────────────────────────────────────────

def test_extract_json_tolerates_fences_and_chatter():
    assert ai.extract_json('Sure!\n```json\n{"a": [1, 2]}\n```\nDone.') == {"a": [1, 2]}
    for bad in ("no json here", "{broken: json}"):
        with pytest.raises(ai.AIError):
            ai.extract_json(bad)


def test_prompts_keep_the_ministry_wording():
    assert "AVOID GENERATING SUBTOPICS WHO'S CONTENTS COULD BE FOUND IN PREVIOUS SUBTOPICS" in prompts.DEVOTIONAL_TOPICS_SYSTEM
    assert "Ensure you use British English" in prompts.DEVOTIONAL_CONTENT_SYSTEM
    assert "I AM CONCERNED ABOUT WORD COUNT" in prompts.DEVOTIONAL_CONTENT_SYSTEM
    assert "Day 6: The Legality of Our Faith" in prompts.DEVOTIONAL_CONTENT_SYSTEM
    assert prompts.topics_system("books") is prompts.BOOK_TOPICS_SYSTEM
    assert prompts.content_system("devotionals") is prompts.DEVOTIONAL_CONTENT_SYSTEM


def test_unconfigured_ai_fails_clearly(monkeypatch):
    import asyncio

    async def none_saved(provider=None):
        return ai.AIConfig("groq", "", "llama")

    monkeypatch.setattr(ai, "get_config", none_saved)
    assert not asyncio.run(ai.is_configured())
    with pytest.raises(ai.AIError, match="Groq API key.*Settings"):
        asyncio.run(ai.complete("s", ["u"]))


# ─── secrets in Settings ─────────────────────────────────────────────

def test_secret_round_trip_is_encrypted_and_tamper_safe():
    from app.core import settings_cache as sc

    assert "anthropic_api_key" in sc.ENCRYPTED_SETTING_KEYS
    token = sc._encrypt("sk-ant-secret-1234")
    assert token.startswith("enc:v1:") and "secret" not in token
    assert sc._decrypt_strict(token) == "sk-ant-secret-1234"
    assert sc._decrypt_strict(token[:-4] + "AAAA") is None      # tampered -> unreadable, not garbage
    assert sc._decrypt_strict("plain-legacy") == "plain-legacy"   # pre-encryption rows still readable
    assert sc._decrypt_strict("") == ""


def test_secret_status_and_plaintext_migration_and_key_rotation(monkeypatch):
    import asyncio

    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.core import settings_cache as sc
    from app.database import Base
    from app.models.site_settings import SiteSetting

    async def run():
        eng = create_async_engine("sqlite+aiosqlite://")
        async with eng.begin() as c:
            await c.run_sync(Base.metadata.create_all)
        async with async_sessionmaker(eng, expire_on_commit=False)() as db:
            await sc.update_settings(db, {"anthropic_api_key": "sk-ant-abcdefgh1234", "telegram_bot_token": ""})
            db.add(SiteSetting(key="paystack_secret_key", value="sk_live_plain_legacy_9876"))
            await db.flush()

            st = await sc.get_secret_status(db)
            assert st["anthropic_api_key"] == {"state": "set", "hint": "1234"}
            assert st["telegram_bot_token"]["state"] == "unset"
            raw = {r.key: r.value for r in (await db.execute(select(SiteSetting))).scalars()}
            assert raw["anthropic_api_key"].startswith("enc:v1:") and "abcdefgh" not in raw["anthropic_api_key"]

            assert await sc.encrypt_plaintext_secrets(db) == 1          # legacy plaintext gets encrypted
            assert await sc.encrypt_plaintext_secrets(db) == 0          # idempotent
            assert (await sc.get_settings(db))["paystack_secret_key"] == "sk_live_plain_legacy_9876"

            from app.config import settings as cfg

            monkeypatch.setattr(cfg, "SECRET_KEY", "rotated")
            st = await sc.get_secret_status(db)
            assert st["anthropic_api_key"]["state"] == "unreadable"      # rotation is reported, never silently blank
        await eng.dispose()

    asyncio.run(run())


# ─── multi-provider ──────────────────────────────────────────────────

def _cfg(provider, key="k", model="m"):
    return ai.AIConfig(provider, key, model)


def test_request_shapes_per_provider():
    url, h, body, _ = ai.build_request(_cfg("anthropic"), "SYS", ["T", "Q"], 100, cache_first_block=True)
    assert url.endswith("/v1/messages") and h["x-api-key"] == "k" and body["system"] == "SYS"
    assert body["messages"][0]["content"][0]["cache_control"] == {"type": "ephemeral"} and "cache_control" not in body["messages"][0]["content"][1]

    url, h, body, _ = ai.build_request(_cfg("openrouter", model="a/b"), "SYS", ["T", "Q"], 100)
    assert url == "https://openrouter.ai/api/v1/chat/completions" and h["Authorization"] == "Bearer k" and "HTTP-Referer" in h
    assert body["model"] == "a/b" and body["messages"][0] == {"role": "system", "content": "SYS"} and "response_format" not in body

    url, h, body, _ = ai.build_request(_cfg("groq"), "SYS", ["T", "Q"], 100)
    assert url == "https://api.groq.com/openai/v1/chat/completions" and body["response_format"] == {"type": "json_object"}
    assert body["messages"][1]["content"] == "T\n\nQ"

    url, h, body, _ = ai.build_request(_cfg("gemini", model="gemini-2.5-flash"), "SYS", ["T", "Q"], 100)
    assert url.endswith("/models/gemini-2.5-flash:generateContent") and h["x-goog-api-key"] == "k" and "key=" not in url
    assert body["systemInstruction"]["parts"][0]["text"] == "SYS" and len(body["contents"][0]["parts"]) == 2
    assert body["generationConfig"]["responseMimeType"] == "application/json"

    with pytest.raises(ai.AIError):
        ai.build_request(_cfg("nope"), "s", ["u"], 1)


def test_response_extraction_per_provider():
    assert ai._extract_anthropic({"content": [{"type": "text", "text": "A"}, {"type": "tool_use"}]}) == "A"
    assert ai._extract_openai({"choices": [{"message": {"content": "B"}}]}) == "B"
    assert ai._extract_openai({"choices": []}) == ""
    assert ai._extract_gemini({"candidates": [{"content": {"parts": [{"text": "C"}, {"text": "hidden", "thought": True}]}}]}) == "C"
    with pytest.raises(ai.AIError, match="blocked"):
        ai._extract_gemini({"promptFeedback": {"blockReason": "SAFETY"}})


@pytest.mark.parametrize("provider,reply", [
    ("anthropic", {"content": [{"type": "text", "text": '{"ok": 1}'}]}),
    ("openrouter", {"choices": [{"message": {"content": '{"ok": 1}'}}]}),
    ("groq", {"choices": [{"message": {"content": '{"ok": 1}'}}]}),
    ("gemini", {"candidates": [{"content": {"parts": [{"text": '{"ok": 1}'}]}}]}),
])
def test_complete_end_to_end_with_mock_transport(monkeypatch, provider, reply):
    import asyncio

    import httpx

    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200, json=reply)

    async def cfg(p=None):
        seen["asked"] = p
        return _cfg(provider)

    monkeypatch.setattr(ai, "get_config", cfg)
    monkeypatch.setattr(ai, "_http_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    assert ai.extract_json(asyncio.run(ai.complete("s", ["u"], provider=provider))) == {"ok": 1}
    assert seen["asked"] == provider and provider in ("anthropic", "openrouter", "groq", "gemini")


def test_complete_retries_then_reports_provider_errors(monkeypatch):
    import asyncio

    import httpx

    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, json={"error": {"message": "slow down"}})
        return httpx.Response(401, json={"error": {"message": "bad key"}})

    async def cfg(p=None):
        return _cfg("groq")

    async def no_sleep(_):
        return None

    monkeypatch.setattr(ai, "get_config", cfg)
    monkeypatch.setattr(ai.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(ai, "_http_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    with pytest.raises(ai.AIError, match="Groq error 401: bad key"):
        asyncio.run(ai.complete("s", ["u"]))
    assert calls["n"] == 2        # 429 retried, 401 is final


def test_default_provider_selection_and_fallbacks(monkeypatch):
    import asyncio

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.core import settings_cache as sc
    from app.database import Base

    async def run():
        eng = create_async_engine("sqlite+aiosqlite://")
        async with eng.begin() as c:
            await c.run_sync(Base.metadata.create_all)
        maker = async_sessionmaker(eng, expire_on_commit=False)
        monkeypatch.setattr("app.database.async_session", maker)
        monkeypatch.setattr(ai.settings, "ANTHROPIC_API_KEY", "")
        async with maker() as db:
            await sc.update_settings(db, {"gemini_api_key": "AIza-test", "groq_api_key": "gsk_x", "groq_model": "llama-x",
                                          ai.DEFAULT_PROVIDER_SETTING: "gemini"})
            await db.commit()
        cfg = await ai.get_config()
        assert (cfg.provider, cfg.api_key, cfg.model) == ("gemini", "AIza-test", "gemini-2.5-flash")
        g = await ai.get_config("groq")
        assert (g.provider, g.model) == ("groq", "llama-x")
        assert (await ai.get_config("openrouter")).api_key == ""           # no key for that one
        async with maker() as db:
            await sc.update_settings(db, {ai.DEFAULT_PROVIDER_SETTING: "bogus"})
            await db.commit()
        assert (await ai.get_config()).provider == "anthropic"            # unknown value -> safe default
        assert not await ai.is_configured()                               # ...which has no key
        await eng.dispose()

    asyncio.run(run())


# ─── model lists ─────────────────────────────────────────────────────

def test_every_provider_has_suggested_models_including_its_default():
    for key, meta in ai.PROVIDERS.items():
        ids = [m for m, _ in ai.SUGGESTED_MODELS[key]]
        assert ids and len(ids) == len(set(ids))
        assert all(ai.valid_model_id(i) for i in ids)
        if key != "anthropic":
            assert meta["default_model"] in ids
    assert "claude-sonnet-5" in [m for m, _ in ai.SUGGESTED_MODELS["anthropic"]]


def test_model_id_validation_blocks_url_and_header_tricks():
    for ok in ("gpt-4o", "openai/gpt-oss-120b", "gemini-2.5-flash", "meta-llama/llama-3.3-70b:free", "claude-haiku-4-5-20251001"):
        assert ai.valid_model_id(ok)
    for bad in ("", " x", "a b", "a?b=1", "a#b", "x/../y", "a\nb", "-lead", "a" * 121, "a;b", "é"):
        assert not ai.valid_model_id(bad)


def test_gemini_model_is_url_quoted():
    url, *_ = ai.build_request(_cfg("gemini", model="a/b"), "s", ["u"], 10)
    assert "/models/a%2Fb:generateContent" in url


def test_parse_live_model_lists():
    gem = {"models": [
        {"name": "models/gemini-2.5-pro", "displayName": "Gemini 2.5 Pro", "supportedGenerationMethods": ["generateContent"]},
        {"name": "models/text-embedding-004", "supportedGenerationMethods": ["embedContent"]},
        {"name": "models/imagen-3", "supportedGenerationMethods": ["generateContent"]},
    ]}
    assert ai.parse_model_list("gemini", gem) == [("gemini-2.5-pro", "Gemini 2.5 Pro")]
    grq = {"data": [{"id": "llama-3.3-70b-versatile", "active": True}, {"id": "whisper-large-v3"}, {"id": "old-model", "active": False},
                    {"id": "meta-llama/llama-guard-4-12b"}]}
    assert ai.parse_model_list("groq", grq) == [("llama-3.3-70b-versatile", "llama-3.3-70b-versatile")]
    orr = {"data": [{"id": "openai/gpt-5", "name": "GPT-5", "architecture": {"output_modalities": ["text"]}},
                    {"id": "black-forest/flux", "architecture": {"output_modalities": ["image"]}}]}
    assert ai.parse_model_list("openrouter", orr) == [("openai/gpt-5", "GPT-5")]
    ant = {"data": [{"id": "claude-sonnet-5", "display_name": "Claude Sonnet 5"}]}
    assert ai.parse_model_list("anthropic", ant) == [("claude-sonnet-5", "Claude Sonnet 5")]


def test_model_options_prefers_live_cache_and_falls_back():
    assert ai.model_options("groq", {})["live"] is False
    live = ai.model_options("groq", {"ai_models_groq": json.dumps({"fetched_at": "t", "models": [["m1", "M1"]]})})
    assert live == {"live": True, "fetched_at": "t", "models": [("m1", "M1")]}
    assert ai.model_options("groq", {"ai_models_groq": "not json"})["live"] is False


def test_fetch_models_calls_the_right_endpoint_and_reports_errors(monkeypatch):
    import asyncio

    import httpx

    seen = []

    def handler(request):
        seen.append((str(request.url), dict(request.headers)))
        if "gemini" in str(request.url) or "generativelanguage" in str(request.url):
            return httpx.Response(200, json={"models": [{"name": "models/g", "supportedGenerationMethods": ["generateContent"]}]})
        return httpx.Response(401, json={"error": {"message": "invalid key"}})

    async def cfg(p=None):
        return _cfg(p or "gemini")

    monkeypatch.setattr(ai, "get_config", cfg)
    monkeypatch.setattr(ai, "_http_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    assert asyncio.run(ai.fetch_models("gemini")) == [("g", "g")]
    assert seen[0][0].startswith("https://generativelanguage.googleapis.com/v1beta/models") and seen[0][1]["x-goog-api-key"] == "k"
    with pytest.raises(ai.AIError, match="invalid key"):
        asyncio.run(ai.fetch_models("groq"))


def test_retired_model_error_points_to_settings():
    import httpx

    resp = httpx.Response(404, json={"error": {"message": "The model `old-x` has been decommissioned"}})
    msg = ai._describe_error(_cfg("groq", model="old-x"), resp)
    assert "old-x" in msg and "Refresh" in msg
    assert "retired" not in ai._describe_error(_cfg("groq"), httpx.Response(401, json={"error": {"message": "bad key"}}))
