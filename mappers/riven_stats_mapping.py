from __future__ import annotations

import json
import re
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import aiohttp

from astrbot.api import logger
from astrbot.core.utils.astrbot_path import get_astrbot_temp_path

from ..http_utils import fetch_json

WARFRAME_MARKET_V1_BASE_URL = "https://api.warframe.market/v1"


@dataclass(frozen=True, slots=True)
class RivenStat:
    url_name: str
    effect: str | None = None


class WarframeRivenStatMapper:
    """解析紫卡词条简写 -> warframe.market 的 riven attribute url_name。"""

    def __init__(
        self,
        *,
        http_timeout_sec: float = 10.0,
        cache_ttl_sec: float = 30 * 24 * 3600,
        ai_timeout_sec: float = 15.0,
    ) -> None:
        self._timeout = aiohttp.ClientTimeout(total=http_timeout_sec)
        self._cache_ttl_sec = cache_ttl_sec
        self._ai_timeout_sec = ai_timeout_sec

        self._temp_dir = Path(get_astrbot_temp_path())
        self._temp_dir.mkdir(parents=True, exist_ok=True)
        self._cache_path = (
            self._temp_dir / "warframe_market_v1_riven_attributes_cache.json"
        )

        self._loaded = False
        self._stats: dict[str, RivenStat] = {}

    def _normalize_key(self, text: str) -> str:
        text = unicodedata.normalize("NFKC", text)
        text = text.strip().lower()
        text = re.sub(r"\s+", "", text)
        return text

    async def initialize(self) -> None:
        if self._loaded:
            return

        cached = self._load_cache()
        if cached:
            self._stats = cached
            self._loaded = True
            return

        stats = await self._fetch_attributes()
        self._stats = {self._normalize_key(s.url_name): s for s in stats}
        self._save_cache(stats)
        self._loaded = True

    def _load_cache(self) -> dict[str, RivenStat] | None:
        if not self._cache_path.exists():
            return None
        try:
            raw = json.loads(self._cache_path.read_text(encoding="utf-8"))
        except Exception:
            return None
        if not isinstance(raw, dict):
            return None
        ts = raw.get("ts")
        if not isinstance(ts, (int, float)):
            return None
        if (time.time() - float(ts)) > self._cache_ttl_sec:
            return None
        items = raw.get("attributes")
        if not isinstance(items, list):
            return None

        out: dict[str, RivenStat] = {}
        for it in items:
            if not isinstance(it, dict):
                continue
            url_name = it.get("url_name")
            if not isinstance(url_name, str):
                continue
            effect = it.get("effect") if isinstance(it.get("effect"), str) else None
            st = RivenStat(url_name=url_name, effect=effect)
            out[self._normalize_key(url_name)] = st
        return out

    def _save_cache(self, stats: list[RivenStat]) -> None:
        try:
            payload = {
                "ts": time.time(),
                "attributes": [
                    {"url_name": s.url_name, "effect": s.effect} for s in stats
                ],
            }
            self._cache_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception as exc:
            logger.debug(f"Failed to save riven attributes cache: {exc!s}")

    async def _fetch_attributes(self) -> list[RivenStat]:
        url = f"{WARFRAME_MARKET_V1_BASE_URL}/riven/attributes"
        headers = {
            "User-Agent": "AstrBot/warframe_helper (+https://github.com/Soulter/AstrBot)",
            "Accept": "application/json",
        }

        payload = await fetch_json(
            url,
            timeout_sec=float(getattr(self._timeout, "total", 10.0) or 10.0),
            headers=headers,
        )
        if not isinstance(payload, dict):
            return []

        pl = payload.get("payload")
        if not isinstance(pl, dict):
            return []
        attrs = pl.get("attributes")
        if not isinstance(attrs, list):
            return []

        out: list[RivenStat] = []
        for a in attrs:
            if not isinstance(a, dict):
                continue
            url_name = a.get("url_name")
            if not isinstance(url_name, str):
                continue
            effect = a.get("effect") if isinstance(a.get("effect"), str) else None
            out.append(RivenStat(url_name=url_name, effect=effect))
        return out

    def is_valid_url_name(self, url_name: str) -> bool:
        if not url_name:
            return False
        return self._normalize_key(url_name) in self._stats

    def resolve_from_alias(
        self, token: str, *, alias_map: dict[str, str]
    ) -> str | None:
        if not token:
            return None
        key = self._normalize_key(token)
        if key in alias_map:
            cand = alias_map[key]
            return cand if self.is_valid_url_name(cand) else None
        return None

    def _parse_ai_stats(self, text: str) -> list[str]:
        candidates: list[str] = []
        try:
            obj = json.loads(text)
            arr = obj.get("stats") if isinstance(obj, dict) else None
            if isinstance(arr, list):
                candidates.extend([s for s in arr if isinstance(s, str)])
        except Exception:
            pass

        if not candidates:
            candidates.extend(re.findall(r"\b[a-z0-9_]{3,}\b", text.lower()))

        norm: list[str] = []
        seen: set[str] = set()
        for s in candidates:
            s = s.strip().lower()
            s = re.sub(r"[^a-z0-9_]+", "", s)
            if not s:
                continue
            if s not in seen:
                norm.append(s)
                seen.add(s)
        return norm

    async def suggest_url_names_via_ai(
        self,
        context: Any,
        event: Any,
        token: str,
        *,
        provider_id: str | None,
    ) -> list[str]:
        if not provider_id:
            try:
                provider_id = await context.get_current_chat_provider_id(
                    event.unified_msg_origin
                )
            except Exception:
                return []

        system_prompt = "You map Warframe Riven stat abbreviations into warframe.market riven attribute url_name. Return JSON only."
        prompt = (
            "Given a single user token (possibly Chinese abbreviation), output up to 5 candidate warframe.market riven attribute url_name.\n"
            "Rules:\n"
            '- Output MUST be valid JSON: {"stats": ["..."]}.\n'
            "- url_name format: lowercase snake_case with underscores.\n"
            "Examples:\n"
            '- 暴击率 -> {"stats":["critical_chance"]}\n'
            '- 暴伤 -> {"stats":["critical_damage"]}\n'
            '- G歧视 -> {"stats":["damage_vs_grineer"]}\n'
            f"Token: {token}\n"
            "JSON:"
        )

        try:
            llm_resp = await context.llm_generate(
                chat_provider_id=provider_id,
                prompt=prompt,
                system_prompt=system_prompt,
                temperature=0,
                timeout=self._ai_timeout_sec,
            )
        except TypeError:
            try:
                llm_resp = await context.llm_generate(
                    chat_provider_id=provider_id,
                    prompt=prompt,
                    system_prompt=system_prompt,
                    temperature=0,
                )
            except Exception:
                return []
        except Exception:
            return []

        text = (llm_resp.completion_text or "").strip()
        logger.info(f"LLM response for riven stat suggestion: {text or 'empty'}")
        return self._parse_ai_stats(text)

    async def resolve_with_ai(
        self,
        *,
        context: Any,
        event: Any,
        token: str,
        provider_id: str | None,
    ) -> str | None:
        await self.initialize()

        suggestions = await self.suggest_url_names_via_ai(
            context,
            event,
            token,
            provider_id=provider_id,
        )
        for s in suggestions:
            if self.is_valid_url_name(s):
                return s
        return None
