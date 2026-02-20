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

WARFRAME_MARKET_V1_BASE_URL = "https://api.warframe.market/v1"


@dataclass(frozen=True, slots=True)
class RivenWeapon:
    url_name: str
    item_name: str
    riven_type: str | None
    mastery_level: int | None
    thumb: str | None
    icon: str | None


class WarframeRivenWeaponMapper:
    """将用户输入（可能是中文武器名/简称）解析为 warframe.market 的 weapon_url_name。"""

    # Common CN weapon names/nicknames -> warframe.market riven weapon_url_name.
    # Keep this minimal and high-confidence.
    _BUILTIN_WEAPON_ALIASES: dict[str, str] = {
        # PublicExport zh name: 绝路 (English: RUBICO)
        "绝路": "rubico",
        # warframe.market riven items: Shedu
        "舍杜": "shedu",
        # warframe.market riven items: Kuva Bramma
        "布拉玛": "kuva_bramma",
        "赤毒布拉玛": "kuva_bramma",
    }

    def __init__(
        self,
        *,
        http_timeout_sec: float = 10.0,
        cache_ttl_sec: float = 7 * 24 * 3600,
        ai_timeout_sec: float = 15.0,
    ) -> None:
        self._timeout = aiohttp.ClientTimeout(total=http_timeout_sec)
        self._cache_ttl_sec = cache_ttl_sec
        self._ai_timeout_sec = ai_timeout_sec

        self._temp_dir = Path(get_astrbot_temp_path())
        self._temp_dir.mkdir(parents=True, exist_ok=True)
        self._cache_path = self._temp_dir / "warframe_market_v1_riven_items_cache.json"

        self._loaded = False
        self._weapons_by_url: dict[str, RivenWeapon] = {}
        self._weapons_by_name: dict[str, RivenWeapon] = {}

    async def initialize(self) -> None:
        if self._loaded:
            return

        cached = self._load_cache()
        if cached:
            self._weapons_by_url = cached[0]
            self._weapons_by_name = cached[1]
            self._loaded = True
            return

        weapons = await self._fetch_riven_items()
        self._build_indexes(weapons)
        self._save_cache(weapons)
        self._loaded = True

    def _normalize_key(self, text: str) -> str:
        text = unicodedata.normalize("NFKC", text)
        text = text.strip().lower()
        text = re.sub(r"\s+", "", text)
        return text

    def _strip_prime_suffix(self, key: str) -> str:
        """Strip common prime indicators from a normalized key.

        Rivens are usually indexed by the weapon family url_name, so
        'xxx prime' / 'xxx p' should resolve to the same url_name.
        """

        k = (key or "").strip().lower()
        if k.endswith("prime") and len(k) > 5:
            return k[: -len("prime")]
        if k.endswith("p") and len(k) > 1:
            return k[:-1]
        return k

    def _load_cache(
        self,
    ) -> tuple[dict[str, RivenWeapon], dict[str, RivenWeapon]] | None:
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

        items = raw.get("items")
        if not isinstance(items, list):
            return None

        weapons: list[RivenWeapon] = []
        for it in items:
            if not isinstance(it, dict):
                continue
            url_name = it.get("url_name")
            item_name = it.get("item_name")
            if not isinstance(url_name, str) or not isinstance(item_name, str):
                continue
            riven_type = (
                it.get("riven_type") if isinstance(it.get("riven_type"), str) else None
            )
            mastery_level = (
                it.get("mastery_level")
                if isinstance(it.get("mastery_level"), int)
                else None
            )
            thumb = it.get("thumb") if isinstance(it.get("thumb"), str) else None
            icon = it.get("icon") if isinstance(it.get("icon"), str) else None
            weapons.append(
                RivenWeapon(
                    url_name=url_name,
                    item_name=item_name,
                    riven_type=riven_type,
                    mastery_level=mastery_level,
                    thumb=thumb,
                    icon=icon,
                )
            )

        by_url: dict[str, RivenWeapon] = {}
        by_name: dict[str, RivenWeapon] = {}
        for w in weapons:
            by_url[self._normalize_key(w.url_name)] = w
            by_name[self._normalize_key(w.item_name)] = w

        return by_url, by_name

    def _save_cache(self, weapons: list[RivenWeapon]) -> None:
        try:
            payload = {
                "ts": time.time(),
                "items": [
                    {
                        "url_name": w.url_name,
                        "item_name": w.item_name,
                        "riven_type": w.riven_type,
                        "mastery_level": w.mastery_level,
                        "thumb": w.thumb,
                        "icon": w.icon,
                    }
                    for w in weapons
                ],
            }
            self._cache_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception as exc:
            logger.debug(f"Failed to save riven items cache: {exc!s}")

    async def _fetch_riven_items(self) -> list[RivenWeapon]:
        url = f"{WARFRAME_MARKET_V1_BASE_URL}/riven/items"
        headers = {
            "User-Agent": "AstrBot/warframe_helper (+https://github.com/Soulter/AstrBot)",
            "Accept": "application/json",
        }

        try:
            async with aiohttp.ClientSession(
                timeout=self._timeout, trust_env=True
            ) as s:
                async with s.get(url, headers=headers) as resp:
                    if resp.status != 200:
                        return []
                    payload = await resp.json()
        except Exception as exc:
            logger.warning(f"warframe.market riven items request failed: {exc!s}")
            return []

        pl = payload.get("payload")
        if not isinstance(pl, dict):
            return []
        items = pl.get("items")
        if not isinstance(items, list):
            return []

        out: list[RivenWeapon] = []
        for it in items:
            if not isinstance(it, dict):
                continue
            url_name = it.get("url_name")
            item_name = it.get("item_name")
            if not isinstance(url_name, str) or not isinstance(item_name, str):
                continue
            riven_type = (
                it.get("riven_type") if isinstance(it.get("riven_type"), str) else None
            )
            mastery_level = (
                it.get("mastery_level")
                if isinstance(it.get("mastery_level"), int)
                else None
            )
            thumb = it.get("thumb") if isinstance(it.get("thumb"), str) else None
            icon = it.get("icon") if isinstance(it.get("icon"), str) else None

            out.append(
                RivenWeapon(
                    url_name=url_name,
                    item_name=item_name,
                    riven_type=riven_type,
                    mastery_level=mastery_level,
                    thumb=thumb,
                    icon=icon,
                )
            )

        return out

    def _build_indexes(self, weapons: list[RivenWeapon]) -> None:
        self._weapons_by_url = {self._normalize_key(w.url_name): w for w in weapons}
        self._weapons_by_name = {self._normalize_key(w.item_name): w for w in weapons}

    def _parse_ai_weapon_url_names(self, text: str) -> list[str]:
        candidates: list[str] = []
        try:
            obj = json.loads(text)
            arr = obj.get("weapons") if isinstance(obj, dict) else None
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

    async def _suggest_weapon_url_names_via_ai(
        self,
        context: Any,
        event: Any,
        query: str,
        provider_id: str | None,
    ) -> list[str]:
        if not provider_id:
            try:
                provider_id = await context.get_current_chat_provider_id(
                    event.unified_msg_origin
                )
            except Exception as e:
                logger.warning(
                    "AI fallback skipped: failed to get current chat provider_id for riven weapon suggestion. query=%r err=%r",
                    query,
                    e,
                )
                return []

        if not provider_id:
            logger.warning(
                "AI fallback skipped: provider_id is empty for riven weapon suggestion. query=%r",
                query,
            )
            return []

        system_prompt = "You convert Warframe weapon names/nicknames into warframe.market riven weapon_url_name. Return JSON only."
        prompt = (
            "Given a user query (possibly Chinese), output up to 5 candidate warframe.market weapon_url_name values for riven auctions.\n"
            "Rules:\n"
            '- Output MUST be valid JSON: {"weapons": ["..."]}.\n'
            "- weapon_url_name format: lowercase snake_case with underscores (no spaces).\n"
            "- Only output weapon_url_name (not item display name).\n"
            "Examples:\n"
            '- Soma -> {"weapons":["soma"]}\n'
            '- 圣装索玛 -> {"weapons":["soma"]}\n'
            f"Query: {query}\n"
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
            except Exception as e:
                logger.warning(
                    "AI fallback failed: llm_generate error (no-timeout retry) for riven weapon suggestion. provider_id=%r query=%r err=%r",
                    provider_id,
                    query,
                    e,
                )
                return []
        except Exception as e:
            logger.warning(
                "AI fallback failed: llm_generate error for riven weapon suggestion. provider_id=%r query=%r err=%r",
                provider_id,
                query,
                e,
            )
            return []

        text = (llm_resp.completion_text or "").strip()
        logger.info(
            f"LLM response for warframe.market riven weapon suggestion: {text or 'empty'}"
        )
        return self._parse_ai_weapon_url_names(text)

    async def resolve_weapon(
        self,
        *,
        context: Any,
        event: Any,
        query: str,
        provider_id: str | None = None,
    ) -> RivenWeapon | None:
        await self.initialize()

        q = (query or "").strip()
        if not q:
            return None

        key = self._normalize_key(q)

        # Direct matches (url_name / item_name)
        if key in self._weapons_by_url:
            return self._weapons_by_url[key]
        if key in self._weapons_by_name:
            return self._weapons_by_name[key]

        # Built-in CN aliases
        k2 = self._strip_prime_suffix(key)
        for cand_key in [key, k2]:
            mapped = self._BUILTIN_WEAPON_ALIASES.get(cand_key)
            if not mapped:
                continue
            mk = self._normalize_key(mapped)
            if mk in self._weapons_by_url:
                return self._weapons_by_url[mk]

        # LLM 兜底：生成候选 weapon_url_name，再用 items 列表校验
        candidates = await self._suggest_weapon_url_names_via_ai(
            context, event, q, provider_id
        )
        seen: set[str] = set()
        for c in candidates:
            if c in seen:
                continue
            seen.add(c)
            ck = self._normalize_key(c)
            if ck in self._weapons_by_url:
                return self._weapons_by_url[ck]

        return None
