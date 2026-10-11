"""Thin Gemini client: JSON-schema output, disk cache, rate limiting, retries and model fallback."""
from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
from dataclasses import dataclass
from typing import Any

from google import genai
from google.genai import types

from .config import Config

log = logging.getLogger(__name__)


@dataclass
class Image:
    data: bytes
    mime_type: str = "image/jpeg"


class LLMError(RuntimeError):
    pass


class GeminiClient:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._client = genai.Client(
            api_key=cfg.api_key,
            http_options=types.HttpOptions(timeout=cfg.request_timeout_s * 1000),
        )
        self._lock = threading.Lock()
        self._last_call = 0.0
        cfg.cache_dir.mkdir(parents=True, exist_ok=True)
        self.calls_made = 0

    # ---------- public ----------
    def generate_json(self, parts: list[str | Image], schema: dict, *, model: str | None = None,
                      system: str | None = None, tag: str = "", use_cache: bool = True) -> dict:
        """Call Gemini and return parsed JSON that follows `schema`.

        Tries the requested model, then the fallback model, with exponential backoff.
        """
        primary = model or self.cfg.model
        models = [primary] + ([self.cfg.fallback_model] if self.cfg.fallback_model != primary else [])
        key = self._cache_key(primary, parts, schema, system)
        if use_cache and (hit := self._cache_get(key)) is not None:
            log.debug("cache hit %s", tag)
            return hit

        last_err: Exception | None = None
        for m in models:
            for attempt in range(self.cfg.max_retries):
                try:
                    result = self._call(m, parts, schema, system)
                    self._cache_put(key, result)
                    return result
                except Exception as e:  # network, quota, bad JSON ...
                    last_err = e
                    wait = min(60, 5 * 2 ** attempt)
                    if _is_quota(e):
                        wait = max(wait, _retry_after(e) or 20)
                    log.warning("[%s] %s attempt %d failed: %s (retry in %ss)", tag, m, attempt + 1,
                                str(e)[:160], wait)
                    if attempt + 1 < self.cfg.max_retries:
                        time.sleep(wait)
            log.warning("[%s] giving up on %s, switching model", tag, m)
        raise LLMError(f"All models failed for {tag}: {last_err}")

    # ---------- internals ----------
    def _call(self, model: str, parts, schema, system) -> dict:
        contents: list[Any] = []
        for p in parts:
            if isinstance(p, Image):
                contents.append(types.Part.from_bytes(data=p.data, mime_type=p.mime_type))
            else:
                contents.append(p)
        config = types.GenerateContentConfig(
            response_mime_type="application/json",
            response_json_schema=schema,
            system_instruction=system,
        )
        self._throttle()
        self.calls_made += 1
        resp = self._client.models.generate_content(model=model, contents=contents, config=config)
        text = resp.text or ""
        return _parse_json(text)

    def _throttle(self) -> None:
        # Free tier allows 15 requests/minute: keep a minimum spacing between request starts.
        with self._lock:
            now = time.monotonic()
            wait = self._last_call + self.cfg.min_interval_s - now
            if wait > 0:
                time.sleep(wait)
            self._last_call = time.monotonic()

    def _cache_key(self, model, parts, schema, system) -> str:
        h = hashlib.sha256()
        h.update(model.encode())
        h.update((system or "").encode())
        h.update(json.dumps(schema, sort_keys=True).encode())
        for p in parts:
            h.update(hashlib.sha256(p.data).digest() if isinstance(p, Image) else p.encode())
        return h.hexdigest()[:32]

    def _cache_get(self, key: str) -> dict | None:
        f = self.cfg.cache_dir / f"{key}.json"
        if f.exists():
            try:
                return json.loads(f.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                return None
        return None

    def _cache_put(self, key: str, value: dict) -> None:
        (self.cfg.cache_dir / f"{key}.json").write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _parse_json(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            return json.loads(m.group(0))
        raise


def _is_quota(e: Exception) -> bool:
    s = str(e)
    return "429" in s or "RESOURCE_EXHAUSTED" in s


def _retry_after(e: Exception) -> float | None:
    m = re.search(r"retry in ([\d.]+)s|retryDelay['\"]?:\s*['\"]?(\d+)", str(e))
    if m:
        return float(m.group(1) or m.group(2)) + 1
    return None
