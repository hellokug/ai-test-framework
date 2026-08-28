"""Unified gateway for judge / embedding model calls.

Responsibilities (independent of the evaluated target's own rate limits):
- per-model registration (any openai_compatible / anthropic / gemini config)
- global + per-model rate limiting (token bucket via aiolimiter)
- token accounting + USD cost estimation, hard ``max_cost_usd`` circuit breaker
- optional on-disk cache (judge answers, temp=0 reproducibility)

Distributed-ready seams: RateLimiter / CacheBackend / TaskQueue abstractions are
kept as small protocols so a Redis backend can replace the in-process ones.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import os
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from aite.utils.cost import estimate_cost_usd

logger = logging.getLogger("aite.gateway")


class CostLimitExceeded(RuntimeError):
    """Raised when the global cost budget is exhausted (circuit breaker)."""


@dataclass
class GatewayResult:
    text: str
    model: str
    usage: Any | None = None
    cost_usd: float = 0.0
    cached: bool = False


@runtime_checkable
class RateLimiter(Protocol):
    async def acquire(self, key: str) -> None: ...


@runtime_checkable
class CacheBackend(Protocol):
    def get(self, key: str) -> str | None: ...
    def set(self, key: str, value: str) -> None: ...


class InProcessRateLimiter:
    """Token-bucket limiter keyed per model (requests/min). asyncio-only."""

    def __init__(self, rpm: int) -> None:
        self.rpm = max(rpm, 1)
        self.interval = 60.0 / self.rpm
        self._next_ok = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self, key: str = "") -> None:
        async with self._lock:
            now = time.monotonic()
            wait = self._next_ok - now
            self._next_ok = max(now, self._next_ok) + self.interval
        if wait > 0:
            await asyncio.sleep(wait)


class DiskCache:
    """Flat-file JSON cache under a directory (SQLite-free, zero middleware)."""

    def __init__(self, directory: str | Path | None = None) -> None:
        self.dir = Path(directory or os.environ.get("AITE_CACHE_DIR")
                        or Path(tempfile.gettempdir()) / "aite-cache")
        self.dir.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        return self.dir / hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]

    def get(self, key: str) -> str | None:
        p = self._path(key)
        try:
            return p.read_text(encoding="utf-8") if p.exists() else None
        except OSError:
            return None

    def set(self, key: str, value: str) -> None:
        try:
            self._path(key).write_text(value, encoding="utf-8")
        except OSError:  # pragma: no cover
            logger.debug("cache write failed", exc_info=True)


@dataclass
class _ModelSlot:
    config: Any                        # TargetConfig
    client: Any = None                 # lazy client per vendor
    limiter: RateLimiter | None = None
    calls: int = 0
    tokens: int = 0
    cost_usd: float = 0.0


@dataclass
class LLMGateway:
    max_cost_usd: float | None = None
    cache_ttl_ok: bool = True
    total_cost_usd: float = field(default=0.0)
    total_calls: int = field(default=0)

    def __post_init__(self) -> None:
        self._models: dict[str, _ModelSlot] = {}
        self._default_model: str | None = None
        self._global_limiter: RateLimiter | None = None
        self._cache: CacheBackend | None = DiskCache()
        self._builders = {
            "openai_compatible": self._build_openai,
            "anthropic": self._build_anthropic,
            "gemini": self._build_gemini,
        }

    # ------------------------------------------------------------- registration
    def register_model(self, config: Any, *, make_default: bool = False) -> str:
        alias = config.name or config.model or config.type
        rpm = (config.rate_limit.rpm if config.rate_limit else None) or 60
        self._models[alias] = _ModelSlot(
            config=config, limiter=InProcessRateLimiter(rpm),
        )
        if make_default or self._default_model is None:
            self._default_model = alias
        return alias

    @property
    def default_model(self) -> str | None:
        return self._default_model

    def stats(self) -> dict[str, Any]:
        return {
            "total_calls": self.total_calls,
            "total_cost_usd": round(self.total_cost_usd, 6),
            "models": {
                alias: {"calls": s.calls, "tokens": s.tokens,
                        "cost_usd": round(s.cost_usd, 6)}
                for alias, s in self._models.items()
            },
        }

    def _check_budget(self, extra_cost: float = 0.0) -> None:
        """Circuit breaker. ``>=`` so a fully-spent budget blocks before the call."""
        if self.max_cost_usd is not None and self.total_cost_usd + extra_cost >= self.max_cost_usd:
            raise CostLimitExceeded(
                f"cost budget exceeded: ${self.total_cost_usd:.4f} + ${extra_cost:.4f} "
                f">= ${self.max_cost_usd:.4f}"
            )

    # ------------------------------------------------------------- main entry
    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 1024,
        seed: int | None = 0,
        json_mode: bool = False,
        cache: bool = True,
    ) -> GatewayResult:
        alias = model or self._default_model
        if alias is None or alias not in self._models:
            raise KeyError(f"gateway model '{alias}' not registered; known: {list(self._models)}")
        slot = self._models[alias]
        key = _cache_key(alias, messages, temperature, max_tokens, seed, json_mode)
        if cache and self._cache is not None and (text := self._cache.get(key)) is not None:
            return GatewayResult(text=text, model=alias, cached=True)

        if slot.limiter:
            await slot.limiter.acquire(alias)
        self._check_budget()

        text, usage = await self._call(slot, messages, temperature, max_tokens, seed, json_mode)
        cost = estimate_cost_usd(slot.config.model, usage) or 0.0
        self._check_budget(cost)
        slot.calls += 1
        slot.tokens += usage.total_tokens if usage else 0
        slot.cost_usd += cost
        self.total_calls += 1
        self.total_cost_usd += cost
        if cache and self._cache is not None:
            self._cache.set(key, text)
        return GatewayResult(text=text, model=alias, usage=usage, cost_usd=cost)

    async def embed(self, texts: list[str], *, model: str | None = None) -> list[list[float]]:
        """Embedding via sentence-transformers when installed, else hash fallback.

        Judge models registered as embedding-capable (params.embedding=true) use
        the vendor embeddings endpoint instead.
        """  # noqa: D401
        slot = self._models.get(model or "") if model else None
        if slot is not None and slot.config.params.get("embedding"):
            return await self._embed_api(slot, texts)
        return await _embed_local(texts)

    # ------------------------------------------------------------- vendor calls
    async def _call(self, slot: _ModelSlot, messages, temperature, max_tokens, seed, json_mode):
        builder = self._builders.get(slot.config.type, self._build_openai)
        call = await builder(slot)
        return await call(messages, temperature, max_tokens, seed, json_mode)

    async def _build_openai(self, slot: _ModelSlot):
        import httpx

        if slot.client is None:
            cfg = slot.config
            base = cfg.base_url or "https://api.openai.com/v1"
            headers = {}
            if cfg.effective_api_key:
                headers["Authorization"] = f"Bearer {cfg.effective_api_key}"
            slot.client = httpx.AsyncClient(
                timeout=cfg.timeout_s, headers=headers, base_url=base.rstrip("/"))
        client: httpx.AsyncClient = slot.client

        async def call(messages, temperature, max_tokens, seed, json_mode):
            body: dict[str, Any] = {
                "model": slot.config.model, "messages": messages,
                "temperature": temperature, "max_tokens": max_tokens,
            }
            if seed is not None:
                body["seed"] = seed
            if json_mode:
                body["response_format"] = {"type": "json_object"}
            resp = await client.post("/chat/completions", json=body)
            resp.raise_for_status()
            payload = resp.json()
            text = payload["choices"][0]["message"]["content"] or ""
            usage = payload.get("usage") or {}
            from aite.core.types import TokenUsage

            tokens = TokenUsage(
                prompt_tokens=usage.get("prompt_tokens", 0),
                completion_tokens=usage.get("completion_tokens", 0),
                total_tokens=usage.get("total_tokens", 0),
            )
            return text, tokens

        return call

    async def _build_anthropic(self, slot: _ModelSlot):
        import httpx

        if slot.client is None:
            cfg = slot.config
            headers = {"anthropic-version": "2023-06-01"}
            if cfg.effective_api_key:
                headers["x-api-key"] = cfg.effective_api_key
            slot.client = httpx.AsyncClient(timeout=cfg.timeout_s, headers=headers)
        client: httpx.AsyncClient = slot.client

        async def call(messages, temperature, max_tokens, seed, json_mode):
            _ = seed, json_mode
            system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
            body = {
                "model": slot.config.model,
                "messages": [m for m in messages if m["role"] != "system"],
                "max_tokens": max_tokens, "temperature": temperature,
            }
            if system:
                body["system"] = system
            resp = await client.post(
                f"{slot.config.base_url or 'https://api.anthropic.com/v1'}/messages", json=body)
            resp.raise_for_status()
            payload = resp.json()
            text = "".join(b.get("text", "") for b in payload.get("content", [])
                           if b.get("type") == "text")
            from aite.core.types import TokenUsage

            usage = TokenUsage(
                prompt_tokens=payload.get("usage", {}).get("input_tokens", 0),
                completion_tokens=payload.get("usage", {}).get("output_tokens", 0),
                total_tokens=payload.get("usage", {}).get("input_tokens", 0)
                + payload.get("usage", {}).get("output_tokens", 0),
            )
            return text, usage

        return call

    async def _build_gemini(self, slot: _ModelSlot):
        import httpx

        if slot.client is None:
            cfg = slot.config
            slot.client = httpx.AsyncClient(timeout=cfg.timeout_s)
        client: httpx.AsyncClient = slot.client

        async def call(messages, temperature, max_tokens, seed, json_mode):
            _ = seed
            contents = [{"role": "user" if m["role"] != "assistant" else "model",
                         "parts": [{"text": m["content"]}]}
                        for m in messages if m["role"] != "system"]
            body = {"contents": contents, "generationConfig": {
                "temperature": temperature, "maxOutputTokens": max_tokens,
                "responseMimeType": "application/json" if json_mode else "text/plain"}}
            url = (f"{slot.config.base_url or 'https://generativelanguage.googleapis.com/v1beta'}"
                   f"/models/{slot.config.model}:generateContent")
            params = {"key": slot.config.effective_api_key} if slot.config.effective_api_key else {}
            resp = await client.post(url, json=body, params=params)
            resp.raise_for_status()
            payload = resp.json()
            text = ""
            with contextlib.suppress(KeyError, IndexError, TypeError):
                text = "".join(p.get("text", "")
                               for p in payload["candidates"][0]["content"]["parts"])
            from aite.core.types import TokenUsage

            meta = payload.get("usageMetadata", {})
            usage = TokenUsage(prompt_tokens=meta.get("promptTokenCount", 0),
                               completion_tokens=meta.get("candidatesTokenCount", 0),
                               total_tokens=meta.get("totalTokenCount", 0))
            return text, usage

        return call

    async def _embed_api(self, slot: _ModelSlot, texts: list[str]) -> list[list[float]]:
        import httpx

        if slot.client is None:
            cfg = slot.config
            headers = {}
            if cfg.effective_api_key:
                headers["Authorization"] = f"Bearer {cfg.effective_api_key}"
            slot.client = httpx.AsyncClient(
                timeout=cfg.timeout_s, headers=headers,
                base_url=(cfg.base_url or "https://api.openai.com/v1").rstrip("/"))
        client: httpx.AsyncClient = slot.client
        resp = await client.post("/embeddings",
                                 json={"model": slot.config.model, "input": texts})
        resp.raise_for_status()
        data = resp.json()["data"]
        return [item["embedding"] for item in data]

    async def close(self) -> None:
        for slot in self._models.values():
            if slot.client is not None:
                await slot.client.aclose()
                slot.client = None


async def _embed_local(texts: list[str]) -> list[list[float]]:
    """sentence-transformers if available, else deterministic hash embeddings."""
    global _ST_MODEL
    try:
        if _ST_MODEL is None:
            from sentence_transformers import SentenceTransformer
            _ST_MODEL = SentenceTransformer("paraphrase-multilingual-MiniLM-L12-v2")
        vectors = _ST_MODEL.encode(texts, show_progress_bar=False)
        return [list(map(float, v)) for v in vectors]
    except ImportError:
        from aite.utils.text import hash_embedding
        return [hash_embedding(t) for t in texts]


_ST_MODEL = None


def _cache_key(alias, messages, temperature, max_tokens, seed, json_mode) -> str:
    payload = json.dumps(
        [alias, messages, temperature, max_tokens, seed, json_mode],
        ensure_ascii=False, sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
