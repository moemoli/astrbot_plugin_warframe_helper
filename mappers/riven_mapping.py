from __future__ import annotations

import json
import re
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import aiohttp

from astrbot.api import logger
from astrbot.core.utils.astrbot_path import get_astrbot_plugin_data_path

from ..http_utils import fetch_json
from .nickname_registry import (
    NicknameRegistry,
    SYM_RIVEN_WEAPON_NICKNAMES,
    USER_ALIASES,
    normalize_alias_key,
)

WARFRAME_MARKET_V2_BASE_URL = "https://api.warframe.market/v2"
WARFRAME_MARKET_REQUEST_LANGUAGE = "zh-hans"
WARFRAME_MARKET_LICH_WEAPONS_CACHE_FILE = "warframe_market_v2_lich_weapons_zh_hans_cache.json"
WARFRAME_MARKET_RIVEN_WEAPON_ENDPOINTS: tuple[str, ...] = (
    "/riven/weapons",
    "/lich/weapons",
    "/sister/weapons",
)


@dataclass(frozen=True, slots=True)
class RivenWeapon:
    url_name: str
    item_name: str
    riven_type: str | None
    mastery_level: int | None
    thumb: str | None
    icon: str | None
    i18n_names: dict[str, str] = field(default_factory=dict)


def _normalize_name_key(text: str) -> str:
    text = unicodedata.normalize("NFKC", str(text or ""))
    text = text.strip().lower().replace("_", " ").replace("-", " ")
    text = re.sub(r"[^0-9a-z\u4e00-\u9fff ]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _tokenize_name(text: str) -> list[str]:
    key = _normalize_name_key(text)
    if not key:
        return []
    return [x for x in key.split(" ") if x]


def _humanize_name(text: str) -> str:
    src = str(text or "").strip()
    if not src:
        return ""
    if re.fullmatch(r"[a-z0-9_\- ]+", src.lower()):
        words = re.sub(r"[_\-]+", " ", src).split()
        return " ".join(w.capitalize() for w in words)
    return re.sub(r"\s+", " ", src)


def _slugify_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", str(text or ""))
    text = text.strip().lower().replace("'", "")
    text = re.sub(r"[^a-z0-9]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text


class WarframeRivenWeaponMapper:
    """简称/全称 -> 本地缓存匹配 -> merged riven/lich/sister weapon slug。"""

    def __init__(
        self,
        *,
        http_timeout_sec: float = 10.0,
        cache_ttl_sec: float = 7 * 24 * 3600,
    ) -> None:
        self._timeout = aiohttp.ClientTimeout(total=http_timeout_sec)
        self._cache_ttl_sec = cache_ttl_sec

        self._plugin_data_dir = (
            Path(get_astrbot_plugin_data_path()) / "astrbot_plugin_warframe_helper"
        )
        self._plugin_data_dir.mkdir(parents=True, exist_ok=True)

        self._nickname_registry = NicknameRegistry()
        self._cache_path = (
            self._plugin_data_dir / WARFRAME_MARKET_LICH_WEAPONS_CACHE_FILE
        )

        self._loaded = False
        self._alias_full_names: dict[str, str] = {}

        self._weapons: list[RivenWeapon] = []
        self._weapons_by_url: dict[str, RivenWeapon] = {}
        self._weapons_by_name: dict[str, list[RivenWeapon]] = {}
        self._weapon_tokens: dict[str, set[str]] = {}
        self._debug_logging_enabled = False

    def set_debug_logging_enabled(self, enabled: bool) -> None:
        self._debug_logging_enabled = bool(enabled)

    def _debug_log(self, action: str, **fields: object) -> None:
        if not self._debug_logging_enabled:
            return
        parts = [f"[WFHelperDebug][RivenWeaponMapper] {action}"]
        for k, v in fields.items():
            s = str(v)
            if len(s) > 220:
                s = s[:217] + "..."
            parts.append(f"{k}={s}")
        logger.info(" | ".join(parts))

    @property
    def cache_path(self) -> Path:
        return self._cache_path

    def reload_aliases(self) -> None:
        self._alias_full_names = self._nickname_registry.get_alias_map(
            SYM_RIVEN_WEAPON_NICKNAMES,
            USER_ALIASES,
        )
        self._debug_log("reload_aliases", aliases=len(self._alias_full_names))

    async def initialize(self) -> None:
        if self._loaded:
            return

        self.reload_aliases()
        loaded = self._load_cache()
        if not loaded:
            await self.refresh_cache()
        self._loaded = True

    def _row_to_weapon(self, row: dict[str, Any]) -> RivenWeapon | None:
        slug = row.get("slug")
        if not isinstance(slug, str) or not slug:
            return None

        i18n_raw = row.get("i18n")
        i18n_names: dict[str, str] = {}
        thumb: str | None = None
        icon: str | None = None

        if isinstance(i18n_raw, dict):
            for locale, block in i18n_raw.items():
                if not isinstance(locale, str) or not isinstance(block, dict):
                    continue
                name = block.get("name")
                if isinstance(name, str) and name:
                    i18n_names[locale] = name

                if locale == "en":
                    t = block.get("thumb")
                    if isinstance(t, str) and t:
                        thumb = t
                    ic = block.get("icon")
                    if isinstance(ic, str) and ic:
                        icon = ic

        item_name = i18n_names.get("en") or _humanize_name(slug)
        mastery_level = row.get("reqMasteryRank")
        if not isinstance(mastery_level, int):
            mastery_level = None

        return RivenWeapon(
            url_name=slug,
            item_name=item_name,
            riven_type=None,
            mastery_level=mastery_level,
            thumb=thumb,
            icon=icon,
            i18n_names=i18n_names,
        )

    def _build_indexes(self, weapons: list[RivenWeapon]) -> None:
        self._weapons = weapons
        self._weapons_by_url = {}
        self._weapons_by_name = {}
        self._weapon_tokens = {}

        for w in weapons:
            key = normalize_alias_key(w.url_name)
            if key:
                self._weapons_by_url[key] = w

            names = [w.item_name, w.url_name.replace("_", " ")] + list(
                w.i18n_names.values()
            )
            token_set: set[str] = set()
            for n in names:
                nk = _normalize_name_key(n)
                if nk:
                    self._weapons_by_name.setdefault(nk, []).append(w)
                token_set.update(_tokenize_name(n))
            self._weapon_tokens[w.url_name] = token_set

    def _load_cache(self) -> bool:
        if not self._cache_path.exists():
            return False
        try:
            raw = json.loads(self._cache_path.read_text(encoding="utf-8"))
        except Exception:
            return False

        if not isinstance(raw, dict):
            return False
        ts = raw.get("ts")
        if not isinstance(ts, (int, float)):
            return False
        if (time.time() - float(ts)) > self._cache_ttl_sec:
            return False

        items = raw.get("items")
        if not isinstance(items, list):
            return False

        weapons: list[RivenWeapon] = []
        for row in items:
            if not isinstance(row, dict):
                continue
            converted = self._row_to_weapon(row)
            if converted is not None:
                weapons.append(converted)

        if not weapons:
            return False

        self._build_indexes(weapons)
        return True

    def _save_cache(self) -> None:
        payload = {
            "ts": time.time(),
            "language": WARFRAME_MARKET_REQUEST_LANGUAGE,
            "items": [
                {
                    "slug": w.url_name,
                    "item_name": w.item_name,
                    "reqMasteryRank": w.mastery_level,
                    "i18n": {
                        locale: {"name": name}
                        for locale, name in w.i18n_names.items()
                    },
                    "thumb": w.thumb,
                    "icon": w.icon,
                }
                for w in self._weapons
            ],
        }
        self._cache_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    async def _fetch_endpoint_weapons(self, endpoint: str) -> list[RivenWeapon]:
        ep = str(endpoint or "").strip()
        if not ep:
            return []

        url = f"{WARFRAME_MARKET_V2_BASE_URL}{ep}"
        headers = {
            "User-Agent": "AstrBot/warframe_helper (+https://github.com/Soulter/AstrBot)",
            "Accept": "application/json",
            "Language": WARFRAME_MARKET_REQUEST_LANGUAGE,
        }

        payload = await fetch_json(
            url,
            timeout_sec=float(getattr(self._timeout, "total", 10.0) or 10.0),
            headers=headers,
        )
        if not isinstance(payload, dict):
            self._debug_log("fetch_endpoint", endpoint=ep, status="bad_payload")
            return []

        data = payload.get("data")
        if not isinstance(data, list):
            self._debug_log("fetch_endpoint", endpoint=ep, status="bad_data")
            return []

        out: list[RivenWeapon] = []
        for row in data:
            if not isinstance(row, dict):
                continue
            weapon = self._row_to_weapon(row)
            if weapon is not None:
                out.append(weapon)

        out.sort(key=lambda x: x.url_name)
        self._debug_log("fetch_endpoint", endpoint=ep, status="ok", count=len(out))
        return out

    async def _fetch_merged_weapons(self) -> list[RivenWeapon]:
        merged_by_slug: dict[str, RivenWeapon] = {}

        for ep in WARFRAME_MARKET_RIVEN_WEAPON_ENDPOINTS:
            weapons = await self._fetch_endpoint_weapons(ep)
            for weapon in weapons:
                slug_key = normalize_alias_key(weapon.url_name)
                if not slug_key:
                    continue

                existed = merged_by_slug.get(slug_key)
                if existed is None:
                    merged_by_slug[slug_key] = weapon
                    continue

                # Prefer richer localized names when duplicate slugs appear across endpoints.
                if len(weapon.i18n_names) > len(existed.i18n_names):
                    merged_by_slug[slug_key] = weapon

        out = sorted(merged_by_slug.values(), key=lambda x: x.url_name)
        self._debug_log(
            "fetch_merged_weapons",
            endpoints=len(WARFRAME_MARKET_RIVEN_WEAPON_ENDPOINTS),
            merged_count=len(out),
        )
        return out

    async def refresh_cache(self) -> int:
        weapons = await self._fetch_merged_weapons()
        if not weapons:
            self._debug_log("refresh_cache", status="empty")
            return 0

        self._build_indexes(weapons)
        self._save_cache()
        self._debug_log("refresh_cache", status="ok", weapons=len(weapons))
        return len(weapons)

    def _resolve_alias(self, query: str) -> tuple[str | None, str]:
        q_norm = normalize_alias_key(query)
        if not q_norm:
            return None, query

        if q_norm in self._alias_full_names:
            return q_norm, self._alias_full_names[q_norm]

        best_key = ""
        best_name = ""
        for k, v in self._alias_full_names.items():
            if not q_norm.startswith(k):
                continue
            if len(k) <= len(best_key):
                continue
            best_key = k
            best_name = v

        if best_key:
            return best_key, best_name

        return None, query

    def _score_weapon(self, *, weapon: RivenWeapon, query_tokens: set[str]) -> int:
        tokens = self._weapon_tokens.get(weapon.url_name, set())
        if not query_tokens:
            return -10_000
        if not query_tokens.issubset(tokens):
            return -10_000

        score = 100
        score -= max(0, len(tokens) - len(query_tokens))
        return score

    def _resolve_weapon_from_local_indexes(self, query: str) -> RivenWeapon | None:
        q = str(query or "").strip()
        if not q:
            self._debug_log("resolve_weapon", query=q, status="empty_query")
            return None

        key = normalize_alias_key(q)
        if key in self._weapons_by_url:
            hit = self._weapons_by_url[key]
            self._debug_log(
                "resolve_hit",
                stage="direct_slug",
                query=q,
                slug=hit.url_name,
                name=hit.item_name,
            )
            return self._weapons_by_url[key]

        name_key = _normalize_name_key(q)
        if name_key in self._weapons_by_name:
            hit = self._weapons_by_name[name_key][0]
            self._debug_log(
                "resolve_hit",
                stage="name_exact",
                query=q,
                slug=hit.url_name,
                name=hit.item_name,
            )
            return self._weapons_by_name[name_key][0]

        alias_key, alias_name = self._resolve_alias(q)
        self._debug_log(
            "resolve_prepare",
            query=q,
            alias_key=alias_key,
            canonical=_humanize_name(alias_name),
        )

        canonical = _humanize_name(alias_name)
        candidates = [
            canonical,
            q,
            _humanize_name(q),
            _slugify_text(canonical).replace("_", " "),
        ]

        dedup_candidates: list[str] = []
        seen: set[str] = set()
        for c in candidates:
            c2 = re.sub(r"\s+", " ", str(c or "")).strip()
            if not c2:
                continue
            if c2 in seen:
                continue
            seen.add(c2)
            dedup_candidates.append(c2)

        for c in dedup_candidates:
            slug = normalize_alias_key(_slugify_text(c))
            if slug in self._weapons_by_url:
                hit = self._weapons_by_url[slug]
                self._debug_log(
                    "resolve_hit",
                    stage="candidate_slug",
                    candidate=c,
                    slug=hit.url_name,
                    name=hit.item_name,
                )
                return self._weapons_by_url[slug]
            nk = _normalize_name_key(c)
            if nk in self._weapons_by_name:
                hit = self._weapons_by_name[nk][0]
                self._debug_log(
                    "resolve_hit",
                    stage="candidate_name",
                    candidate=c,
                    slug=hit.url_name,
                    name=hit.item_name,
                )
                return self._weapons_by_name[nk][0]

        best: RivenWeapon | None = None
        best_score = -10_000
        for c in dedup_candidates:
            query_tokens = set(_tokenize_name(c))
            if not query_tokens:
                continue
            for weapon in self._weapons:
                score = self._score_weapon(
                    weapon=weapon,
                    query_tokens=query_tokens,
                )
                if score > best_score:
                    best_score = score
                    best = weapon

        if best is None:
            self._debug_log("resolve_miss", stage="global_score", reason="no_best_item")
            return None
        if best_score < 80:
            self._debug_log(
                "resolve_miss",
                stage="global_score",
                reason="score_below_threshold",
                best_score=best_score,
                threshold=80,
            )
            return None
        self._debug_log(
            "resolve_hit",
            stage="global_score",
            best_score=best_score,
            slug=best.url_name,
            name=best.item_name,
        )
        return best

    async def resolve_weapon(
        self,
        *,
        context: Any,
        event: Any,
        query: str,
        provider_id: str | None = None,
    ) -> RivenWeapon | None:
        del context, event, provider_id
        await self.initialize()
        result = self._resolve_weapon_from_local_indexes(query)
        self._debug_log(
            "resolve_weapon_final",
            query=query,
            matched_slug=(result.url_name if result else None),
            matched_name=(result.item_name if result else None),
        )
        return result

    async def resolve_weapon_local(self, *, query: str) -> RivenWeapon | None:
        await self.initialize()
        return self._resolve_weapon_from_local_indexes(query)
