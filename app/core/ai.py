"""Multi-provider LLM client (httpx, no vendor SDKs) used by Books Pub.

Providers: Anthropic, OpenRouter, Groq and Google Gemini. Which one is the
default, and every key and model, are admin-managed in Settings → AI drafting
(keys encrypted in the database) and are read on every call, so the web app and
the Celery worker follow a change immediately.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from urllib.parse import quote
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

ANTHROPIC_VERSION = "2023-06-01"
_RETRY_STATUS = {408, 409, 429, 500, 502, 503, 504, 529}

# key_setting / model_setting are the site_settings keys; the key one is encrypted at rest.
PROVIDERS: dict[str, dict[str, str]] = {
    "anthropic": {
        "label": "Anthropic (Claude)", "key_setting": "anthropic_api_key", "model_setting": "anthropic_model",
        "default_model": "claude-sonnet-5", "key_hint": "sk-ant-…", "docs": "console.anthropic.com",
    },
    "openrouter": {
        "label": "OpenRouter", "key_setting": "openrouter_api_key", "model_setting": "openrouter_model",
        "default_model": "anthropic/claude-sonnet-4.5", "key_hint": "sk-or-…", "docs": "openrouter.ai/keys",
    },
    "groq": {
        "label": "Groq", "key_setting": "groq_api_key", "model_setting": "groq_model",
        "default_model": "llama-3.3-70b-versatile", "key_hint": "gsk_…", "docs": "console.groq.com/keys",
    },
    "gemini": {
        "label": "Google Gemini", "key_setting": "gemini_api_key", "model_setting": "gemini_model",
        "default_model": "gemini-2.5-flash", "key_hint": "AIza…", "docs": "aistudio.google.com/apikey",
    },
}
# Suggested models shown before a live list has been fetched. Providers retire models
# often, so Settings can replace this with the provider's own current list ("Refresh").
SUGGESTED_MODELS: dict[str, list[tuple[str, str]]] = {
    "anthropic": [
        ("claude-sonnet-5", "Claude Sonnet 5"), ("claude-opus-5-5", "Claude Opus 5.5"),
        ("claude-fable-5-1", "Claude Fable 5.1"), ("claude-haiku-4-5-20251001", "Claude Haiku 4.5"),
    ],
    "openrouter": [
        ("anthropic/claude-sonnet-4.5", "Claude Sonnet 4.5"), ("openai/gpt-5", "GPT-5"), ("openai/gpt-5-mini", "GPT-5 mini"),
        ("google/gemini-2.5-pro", "Gemini 2.5 Pro"), ("google/gemini-2.5-flash", "Gemini 2.5 Flash"),
        ("meta-llama/llama-3.3-70b-instruct", "Llama 3.3 70B Instruct"), ("deepseek/deepseek-chat", "DeepSeek Chat"),
    ],
    "groq": [
        ("llama-3.3-70b-versatile", "Llama 3.3 70B Versatile"), ("llama-3.1-8b-instant", "Llama 3.1 8B Instant"),
        ("openai/gpt-oss-120b", "GPT-OSS 120B"), ("openai/gpt-oss-20b", "GPT-OSS 20B"),
    ],
    "gemini": [
        ("gemini-2.5-flash", "Gemini 2.5 Flash"), ("gemini-2.5-pro", "Gemini 2.5 Pro"), ("gemini-2.5-flash-lite", "Gemini 2.5 Flash-Lite"),
    ],
}
MODELS_CACHE_PREFIX = "ai_models_"          # site_settings key: JSON {"fetched_at": iso, "models": [[id, label], ...]}
_MODEL_ID_OK = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,119}$")
DEFAULT_PROVIDER = "anthropic"
DEFAULT_PROVIDER_SETTING = "ai_default_provider"

OPENROUTER_BASE = "https://openrouter.ai/api/v1"
GROQ_BASE = "https://api.groq.com/openai/v1"
GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"


class AIError(Exception):
    """Raised when the model call fails or returns something unusable."""


@dataclass
class AIConfig:
    provider: str
    api_key: str
    model: str

    @property
    def label(self) -> str:
        return PROVIDERS[self.provider]["label"]


def _env_key(provider: str) -> str:
    return settings.ANTHROPIC_API_KEY.strip() if provider == "anthropic" else ""


async def get_config(provider: str | None = None) -> AIConfig:
    """Config for ``provider`` (default: the admin-selected default provider)."""
    from app.core.settings_cache import get_settings
    from app.database import async_session

    stored: dict[str, str] = {}
    try:
        async with async_session() as db:
            stored = await get_settings(db)
    except Exception:
        logger.exception("Could not read AI settings from the database")

    chosen = provider or (stored.get(DEFAULT_PROVIDER_SETTING) or "").strip() or DEFAULT_PROVIDER
    if chosen not in PROVIDERS:
        chosen = DEFAULT_PROVIDER
    meta = PROVIDERS[chosen]
    key = (stored.get(meta["key_setting"]) or "").strip() or _env_key(chosen)
    model = (stored.get(meta["model_setting"]) or "").strip()
    if not model:
        model = settings.ANTHROPIC_MODEL if chosen == "anthropic" else meta["default_model"]
    return AIConfig(chosen, key, model)


async def is_configured() -> bool:
    return bool((await get_config()).api_key)


# ─── Per-provider request/response shapes ────────────────────────────

def build_request(cfg: AIConfig, system: str, blocks: list[str], max_tokens: int, cache_first_block: bool = False):
    """Return ``(url, headers, json_payload, extract_fn)`` for one provider."""
    if cfg.provider == "anthropic":
        content: list[dict[str, Any]] = []
        for i, text in enumerate(blocks):
            block: dict[str, Any] = {"type": "text", "text": text}
            if cache_first_block and i == 0:
                block["cache_control"] = {"type": "ephemeral"}
            content.append(block)
        return (
            f"{settings.ANTHROPIC_API_BASE.rstrip('/')}/v1/messages",
            {"x-api-key": cfg.api_key, "anthropic-version": ANTHROPIC_VERSION, "content-type": "application/json"},
            {"model": cfg.model, "max_tokens": max_tokens, "system": system, "messages": [{"role": "user", "content": content}]},
            _extract_anthropic,
        )

    if cfg.provider in ("openrouter", "groq"):
        payload: dict[str, Any] = {
            "model": cfg.model,
            "max_tokens": max_tokens,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": "\n\n".join(blocks)}],
        }
        headers = {"Authorization": f"Bearer {cfg.api_key}", "content-type": "application/json"}
        if cfg.provider == "groq":
            payload["response_format"] = {"type": "json_object"}  # every Books Pub prompt asks for JSON
            return f"{GROQ_BASE}/chat/completions", headers, payload, _extract_openai
        headers["HTTP-Referer"] = settings.APP_URL
        headers["X-Title"] = settings.APP_NAME
        return f"{OPENROUTER_BASE}/chat/completions", headers, payload, _extract_openai

    if cfg.provider == "gemini":
        payload = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": t} for t in blocks]}],
            # Gemini 2.5 spends part of this budget on hidden reasoning, so give it room.
            "generationConfig": {"maxOutputTokens": max_tokens * 3, "responseMimeType": "application/json"},
        }
        return (
            f"{GEMINI_BASE}/models/{quote(cfg.model, safe='')}:generateContent",
            {"x-goog-api-key": cfg.api_key, "content-type": "application/json"},
            payload,
            _extract_gemini,
        )

    raise AIError(f"Unknown AI provider: {cfg.provider}")


def _extract_anthropic(data: dict) -> str:
    return "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")


def _extract_openai(data: dict) -> str:
    choices = data.get("choices") or []
    if not choices:
        return ""
    return (choices[0].get("message") or {}).get("content") or ""


def _extract_gemini(data: dict) -> str:
    cands = data.get("candidates") or []
    if not cands:
        reason = (data.get("promptFeedback") or {}).get("blockReason")
        raise AIError(f"Gemini blocked the request ({reason})." if reason else "Gemini returned no candidates.")
    parts = (cands[0].get("content") or {}).get("parts") or []
    return "".join(p.get("text", "") for p in parts if not p.get("thought"))


def _http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=15.0))


async def complete(
    system: str,
    user_blocks: list[str],
    *,
    max_tokens: int = 4096,
    cache_first_block: bool = False,
    attempts: int = 4,
    provider: str | None = None,
) -> str:
    """Send one request to the default (or the given) provider; return the reply text.

    ``user_blocks`` are the pieces of one user turn. ``cache_first_block`` marks the
    transcript for prompt caching where the provider supports it (Anthropic).
    """
    cfg = await get_config(provider)
    if not cfg.api_key:
        raise AIError(f"No {cfg.label} API key is saved. Add it under Settings → AI drafting.")
    url, headers, payload, extract = build_request(cfg, system, user_blocks, max_tokens, cache_first_block)

    last_error = "unknown error"
    async with _http_client() as client:
        for attempt in range(1, attempts + 1):
            try:
                resp = await client.post(url, headers=headers, json=payload)
            except httpx.HTTPError as exc:
                last_error = f"network error: {exc}"
            else:
                if resp.status_code == 200:
                    try:
                        text = extract(resp.json())
                    except AIError:
                        raise
                    except Exception as exc:
                        raise AIError(f"{cfg.label} sent an unexpected reply ({type(exc).__name__}).") from exc
                    if not text.strip():
                        raise AIError(f"{cfg.label} returned an empty reply.")
                    return text
                last_error = _describe_error(cfg, resp)
                if resp.status_code not in _RETRY_STATUS:
                    raise AIError(last_error)
            if attempt < attempts:
                delay = min(2 ** attempt * 2, 30)
                logger.warning("%s call failed (%s); retry %d/%d in %ds", cfg.label, last_error, attempt, attempts - 1, delay)
                await asyncio.sleep(delay)
    raise AIError(last_error)


def _describe_error(cfg: AIConfig, resp: httpx.Response) -> str:
    try:
        err = resp.json().get("error", {})
        msg = err.get("message") if isinstance(err, dict) else str(err)
    except Exception:
        msg = ""
    msg = msg or resp.text[:200]
    text = f"{cfg.label} error {resp.status_code}: {msg}"
    if resp.status_code in (400, 404, 410) and re.search(r"model|not found|deprecat|decommission|retired", msg, re.I):
        text += f" — model “{cfg.model}” may be retired or misspelled. Pick another under Settings → AI drafting (use Refresh to see the current list)."
    return text


def valid_model_id(model: str) -> bool:
    return bool(_MODEL_ID_OK.match(model or "")) and ".." not in model


# ─── Model lists ─────────────────────────────────────────────────────

_NON_CHAT = re.compile(r"whisper|tts|playai|orpheus|guard|embed|moderation|image|imagen|veo|aqa|rerank|transcribe|speech|dall", re.I)


def parse_model_list(provider: str, data: dict) -> list[tuple[str, str]]:
    """Text-generation models from a provider's list-models response, as ``(id, label)``."""
    out: list[tuple[str, str]] = []
    if provider == "gemini":
        for m in data.get("models", []):
            if "generateContent" not in (m.get("supportedGenerationMethods") or []):
                continue
            mid = str(m.get("name", "")).removeprefix("models/")
            if mid and not _NON_CHAT.search(mid):
                out.append((mid, m.get("displayName") or mid))
    elif provider == "anthropic":
        for m in data.get("data", []):
            if m.get("id"):
                out.append((m["id"], m.get("display_name") or m["id"]))
    elif provider == "openrouter":
        for m in data.get("data", []):
            mods = (m.get("architecture") or {}).get("output_modalities") or ["text"]
            if m.get("id") and "text" in mods and not _NON_CHAT.search(m["id"]):
                out.append((m["id"], m.get("name") or m["id"]))
    else:  # groq (OpenAI-style)
        for m in data.get("data", []):
            if m.get("id") and m.get("active", True) and not _NON_CHAT.search(m["id"]):
                out.append((m["id"], m["id"]))
    seen, uniq = set(), []
    for mid, label in sorted(out):
        if mid not in seen and valid_model_id(mid):
            seen.add(mid)
            uniq.append((mid, label))
    return uniq


async def fetch_models(provider: str) -> list[tuple[str, str]]:
    """Ask the provider which models it currently offers (needs that provider's saved key)."""
    cfg = await get_config(provider)
    if not cfg.api_key:
        raise AIError(f"Save a {cfg.label} API key first, then refresh its model list.")
    if provider == "anthropic":
        url, headers = f"{settings.ANTHROPIC_API_BASE.rstrip('/')}/v1/models?limit=1000", {"x-api-key": cfg.api_key, "anthropic-version": ANTHROPIC_VERSION}
    elif provider == "openrouter":
        url, headers = f"{OPENROUTER_BASE}/models", {"Authorization": f"Bearer {cfg.api_key}"}
    elif provider == "groq":
        url, headers = f"{GROQ_BASE}/models", {"Authorization": f"Bearer {cfg.api_key}"}
    else:
        url, headers = f"{GEMINI_BASE}/models?pageSize=1000", {"x-goog-api-key": cfg.api_key}
    try:
        async with _http_client() as client:
            resp = await client.get(url, headers=headers)
    except httpx.HTTPError as exc:
        raise AIError(f"network error: {exc}") from exc
    if resp.status_code != 200:
        raise AIError(_describe_error(cfg, resp))
    models = parse_model_list(provider, resp.json())
    if not models:
        raise AIError(f"{cfg.label} returned no usable models.")
    return models


def model_options(provider: str, stored: dict[str, str]) -> dict:
    """What the settings page offers for ``provider``: live list if fetched, else suggestions."""
    raw = stored.get(MODELS_CACHE_PREFIX + provider) or ""
    try:
        cached = json.loads(raw) if raw else None
    except (json.JSONDecodeError, TypeError):
        cached = None
    if cached and cached.get("models"):
        return {"live": True, "fetched_at": cached.get("fetched_at", ""), "models": [tuple(m) for m in cached["models"]]}
    return {"live": False, "fetched_at": "", "models": SUGGESTED_MODELS.get(provider, [])}


def extract_json(text: str) -> dict:
    """Pull the first JSON object out of a model reply (tolerates code fences and chatter)."""
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.IGNORECASE)
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end <= start:
        raise AIError("The model did not return JSON.")
    try:
        return json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError as exc:
        raise AIError(f"The model returned malformed JSON ({exc.msg}).") from exc
