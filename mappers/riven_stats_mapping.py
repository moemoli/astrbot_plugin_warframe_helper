from __future__ import annotations

import json
import re
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import aiohttp

from astrbot.core.utils.astrbot_path import get_astrbot_plugin_data_path

from ..http_utils import fetch_json
from .nickname_registry import (
    NicknameRegistry,
    SYM_RIVEN_STAT_NICKNAMES,
    USER_ALIASES,
    normalize_alias_key,
)

WARFRAME_MARKET_V2_BASE_URL = "https://api.warframe.market/v2"
WARFRAME_MARKET_REQUEST_LANGUAGE = "zh-hans"
WARFRAME_MARKET_RIVEN_ATTRIBUTES_CACHE_FILE = (
    "warframe_market_v2_riven_attributes_zh_hans_cache.json"
)


@dataclass(frozen=True, slots=True)
class RivenStat:
    url_name: str
    effect: str | None = None
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


class WarframeRivenStatMapper:
    """简称/全称 -> 本地缓存匹配 -> riven attribute slug。"""

    def __init__(
        self,
        *,
        http_timeout_sec: float = 10.0,
        cache_ttl_sec: float = 30 * 24 * 3600,
    ) -> None:
        self._timeout = aiohttp.ClientTimeout(total=http_timeout_sec)
        self._cache_ttl_sec = cache_ttl_sec

        self._plugin_data_dir = (
            Path(get_astrbot_plugin_data_path()) / "astrbot_plugin_warframe_helper"
        )
        self._plugin_data_dir.mkdir(parents=True, exist_ok=True)

        self._nickname_registry = NicknameRegistry()
        self._cache_path = (
            self._plugin_data_dir / WARFRAME_MARKET_RIVEN_ATTRIBUTES_CACHE_FILE
        )

        self._loaded = False
        self._alias_full_names: dict[str, str] = {}

        self._stats: list[RivenStat] = []
        self._stats_by_slug: dict[str, RivenStat] = {}
        self._stats_by_name: dict[str, list[RivenStat]] = {}
        self._stats_tokens: dict[str, set[str]] = {}
        self._compound_keys: list[str] = []

    @property
    def cache_path(self) -> Path:
        return self._cache_path

    def reload_aliases(self) -> None:
        self._alias_full_names = self._nickname_registry.get_alias_map(
            SYM_RIVEN_STAT_NICKNAMES,
            USER_ALIASES,
        )
        self._compound_keys = sorted(
            [k for k in self._alias_full_names.keys() if len(k) >= 2],
            key=len,
            reverse=True,
        )

    async def initialize(self) -> None:
        if self._loaded:
            return

        self.reload_aliases()
        loaded = self._load_cache()
        if not loaded:
            await self.refresh_cache()
        self._loaded = True

    def _row_to_stat(self, row: dict[str, Any]) -> RivenStat | None:
        slug = row.get("slug")
        if not isinstance(slug, str) or not slug:
            return None

        i18n_raw = row.get("i18n")
        i18n_names: dict[str, str] = {}
        if isinstance(i18n_raw, dict):
            for locale, block in i18n_raw.items():
                if not isinstance(locale, str) or not isinstance(block, dict):
                    continue
                name = block.get("name")
                if isinstance(name, str) and name:
                    i18n_names[locale] = name

        effect = i18n_names.get("en") or _humanize_name(slug)
        return RivenStat(url_name=slug, effect=effect, i18n_names=i18n_names)

    def _build_indexes(self, stats: list[RivenStat]) -> None:
        self._stats = stats
        self._stats_by_slug = {}
        self._stats_by_name = {}
        self._stats_tokens = {}

        for st in stats:
            key = normalize_alias_key(st.url_name)
            if key:
                self._stats_by_slug[key] = st

            names = [st.url_name.replace("_", " "), st.effect or ""] + list(
                st.i18n_names.values()
            )
            token_set: set[str] = set()
            for n in names:
                nk = _normalize_name_key(n)
                if nk:
                    self._stats_by_name.setdefault(nk, []).append(st)
                token_set.update(_tokenize_name(n))
            self._stats_tokens[st.url_name] = token_set

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

        items = raw.get("attributes")
        if not isinstance(items, list):
            return False

        stats: list[RivenStat] = []
        for row in items:
            if not isinstance(row, dict):
                continue
            converted = self._row_to_stat(row)
            if converted is not None:
                stats.append(converted)

        if not stats:
            return False

        self._build_indexes(stats)
        return True

    def _save_cache(self) -> None:
        payload = {
            "ts": time.time(),
            "language": WARFRAME_MARKET_REQUEST_LANGUAGE,
            "attributes": [
                {
                    "slug": s.url_name,
                    "effect": s.effect,
                    "i18n": {
                        locale: {"name": name}
                        for locale, name in s.i18n_names.items()
                    },
                }
                for s in self._stats
            ],
        }
        self._cache_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    async def _fetch_attributes(self) -> list[RivenStat]:
        url = f"{WARFRAME_MARKET_V2_BASE_URL}/riven/attributes"
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
            return []

        data = payload.get("data")
        if not isinstance(data, list):
            return []

        out: list[RivenStat] = []
        for row in data:
            if not isinstance(row, dict):
                continue
            converted = self._row_to_stat(row)
            if converted is not None:
                out.append(converted)

        out.sort(key=lambda x: x.url_name)
        return out

    async def refresh_cache(self) -> int:
        stats = await self._fetch_attributes()
        if not stats:
            return 0

        self._build_indexes(stats)
        self._save_cache()
        return len(stats)

    def is_valid_url_name(self, url_name: str) -> bool:
        if not url_name:
            return False
        return normalize_alias_key(url_name) in self._stats_by_slug

    def _score_stat(self, *, stat: RivenStat, query_tokens: set[str]) -> int:
        tokens = self._stats_tokens.get(stat.url_name, set())
        if not query_tokens:
            return -10_000
        if not query_tokens.issubset(tokens):
            return -10_000

        score = 100
        score -= max(0, len(tokens) - len(query_tokens))
        return score

    def resolve_token(self, token: str) -> str | None:
        q = str(token or "").strip()
        if not q:
            return None

        key = normalize_alias_key(q)
        if key in self._stats_by_slug:
            return self._stats_by_slug[key].url_name

        name_key = _normalize_name_key(q)
        if name_key in self._stats_by_name:
            return self._stats_by_name[name_key][0].url_name

        alias_full = self._alias_full_names.get(key)
        candidates = [q, _humanize_name(q)]
        if alias_full:
            candidates.insert(0, alias_full)

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
            if slug in self._stats_by_slug:
                return self._stats_by_slug[slug].url_name

            nk = _normalize_name_key(c)
            if nk in self._stats_by_name:
                return self._stats_by_name[nk][0].url_name

        best: RivenStat | None = None
        best_score = -10_000
        for c in dedup_candidates:
            query_tokens = set(_tokenize_name(c))
            if not query_tokens:
                continue
            for stat in self._stats:
                score = self._score_stat(stat=stat, query_tokens=query_tokens)
                if score > best_score:
                    best_score = score
                    best = stat

        if best is None:
            return None
        if best_score < 80:
            return None
        return best.url_name

    def resolve_from_alias(
        self,
        token: str,
        *,
        alias_map: dict[str, str] | None = None,
    ) -> str | None:
        key = normalize_alias_key(token)
        if not key:
            return None

        if alias_map and key in alias_map:
            return self.resolve_token(alias_map[key])

        if key in self._alias_full_names:
            return self.resolve_token(self._alias_full_names[key])

        return None

    def split_compound_token(self, token: str) -> list[str]:
        tok = str(token or "").strip()
        if not tok:
            return []

        direct_parts = [
            p.strip()
            for p in re.split(r"[,+/|，、\s]+", tok)
            if isinstance(p, str) and p.strip()
        ]
        if len(direct_parts) > 1:
            return direct_parts

        norm = normalize_alias_key(tok)
        if not norm:
            return []

        out: list[str] = []
        i = 0
        while i < len(norm):
            matched: str | None = None
            for key in self._compound_keys:
                if norm.startswith(key, i):
                    matched = key
                    break
            if matched:
                out.append(matched)
                i += len(matched)
                continue
            i += 1

        if out:
            return out
        return [tok]

