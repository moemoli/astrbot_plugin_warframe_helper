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
    SYM_BASE_NICKNAMES,
    USER_ALIASES,
    normalize_alias_key,
)

WARFRAME_MARKET_V2_BASE_URL = "https://api.warframe.market/v2"
WARFRAME_MARKET_REQUEST_LANGUAGE = "zh-hans"
WARFRAME_MARKET_ITEMS_CACHE_FILE = "warframe_market_v2_items_zh_hans_cache.json"


@dataclass(frozen=True, slots=True)
class MarketItem:
    item_id: str | None
    slug: str
    name: str
    wiki_link: str | None = None
    tags: tuple[str, ...] = ()
    i18n_names: dict[str, str] = field(default_factory=dict)
    thumb: str | None = None
    icon: str | None = None

    def get_localized_name(self, lang: str | None) -> str:
        if not lang:
            return self.name
        lang_norm = str(lang).strip().lower()
        if not lang_norm:
            return self.name

        if self.i18n_names:
            candidates: list[str] = [
                lang_norm,
                lang_norm.replace("_", "-"),
                lang_norm.replace("-", "_"),
            ]

            locale_aliases: dict[str, tuple[str, ...]] = {
                "cn": ("zh", "zh-hans", "zh-cn"),
                "zh": ("zh", "zh-hans", "zh-cn"),
                "zh-cn": ("zh-cn", "zh-hans", "zh"),
                "zh-hans": ("zh-hans", "zh-cn", "zh"),
                "zh-tw": ("zh-tw", "zh-hant"),
                "zh-hant": ("zh-hant", "zh-tw"),
                "tw": ("zh-tw", "zh-hant"),
            }
            for alias in locale_aliases.get(lang_norm, ()):  # noqa: PERF402
                candidates.append(alias)
                candidates.append(alias.replace("_", "-"))
                candidates.append(alias.replace("-", "_"))

            seen: set[str] = set()
            for c in candidates:
                if not c or c in seen:
                    continue
                seen.add(c)
                if c in self.i18n_names and self.i18n_names[c]:
                    return self.i18n_names[c]

            if "en" in self.i18n_names and self.i18n_names["en"]:
                return self.i18n_names["en"]

        return self.name


@dataclass(frozen=True, slots=True)
class MarketResolveTrace:
    original_query: str
    alias_key: str | None
    canonical_full_name: str
    matched_item_name: str | None
    matched_slug: str | None


@dataclass(frozen=True, slots=True)
class _PreparedItemQuery:
    raw_query: str
    alias_key: str | None
    canonical_name: str
    wants_prime: bool
    wants_set: bool
    wants_blueprint: bool
    part_hint: str | None
    prefer_prime: bool


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
    return [t for t in key.split(" ") if t]


def _slugify_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", str(text or ""))
    text = text.strip().lower()
    text = text.replace("'", "")
    text = re.sub(r"[^a-z0-9]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text


def _humanize_name(text: str) -> str:
    src = str(text or "").strip()
    if not src:
        return ""

    if re.fullmatch(r"[a-z0-9_\- ]+", src.lower()):
        words = re.sub(r"[_\-]+", " ", src).split()
        if not words:
            return src
        return " ".join(w.capitalize() for w in words)

    return re.sub(r"\s+", " ", src)


class WarframeTermMapper:
    """简称/全称 -> 本地缓存匹配 -> warframe.market slug。"""

    def __init__(
        self,
        *,
        http_timeout_sec: float = 8.0,
        cache_ttl_sec: float = 30 * 24 * 3600,
    ) -> None:
        self._timeout = aiohttp.ClientTimeout(total=http_timeout_sec)
        self._cache_ttl_sec = cache_ttl_sec

        self._plugin_data_dir = (
            Path(get_astrbot_plugin_data_path()) / "astrbot_plugin_warframe_helper"
        )
        self._plugin_data_dir.mkdir(parents=True, exist_ok=True)

        self._nickname_registry = NicknameRegistry()
        self._items_cache_path = self._plugin_data_dir / WARFRAME_MARKET_ITEMS_CACHE_FILE

        self._alias_full_names: dict[str, str] = {}
        self._base_alias_keys: set[str] = set()

        self._items: list[MarketItem] = []
        self._items_by_slug: dict[str, MarketItem] = {}
        self._items_by_name_key: dict[str, list[MarketItem]] = {}
        self._items_token_index: dict[str, set[str]] = {}

        self._loaded = False
        self._debug_logging_enabled = False

    def set_debug_logging_enabled(self, enabled: bool) -> None:
        self._debug_logging_enabled = bool(enabled)

    def _debug_log(self, action: str, **fields: object) -> None:
        if not self._debug_logging_enabled:
            return
        parts = [f"[WFHelperDebug][TermMapper] {action}"]
        for k, v in fields.items():
            s = str(v)
            if len(s) > 220:
                s = s[:217] + "..."
            parts.append(f"{k}={s}")
        logger.info(" | ".join(parts))

    @property
    def items_cache_path(self) -> Path:
        return self._items_cache_path

    @property
    def nickname_file_path(self) -> Path:
        return self._nickname_registry.path

    @property
    def nickname_default_file_path(self) -> Path:
        return self._nickname_registry.default_path

    async def initialize(self) -> None:
        if self._loaded:
            return

        self.reload_aliases()

        loaded = self._load_items_cache()
        if not loaded:
            await self.refresh_items_cache()
        self._loaded = True

    def reload_aliases(self) -> None:
        base_aliases = self._nickname_registry.get_alias_map(SYM_BASE_NICKNAMES)
        merged_aliases = self._nickname_registry.get_alias_map(
            SYM_BASE_NICKNAMES,
            USER_ALIASES,
        )
        self._base_alias_keys = set(base_aliases.keys())
        self._alias_full_names = merged_aliases
        self._debug_log(
            "reload_aliases",
            base_aliases=len(self._base_alias_keys),
            merged_aliases=len(self._alias_full_names),
        )

    def upsert_alias(self, *, alias: str, full_name: str) -> tuple[str, str]:
        key, value = self._nickname_registry.upsert_default_alias(
            alias=alias,
            full_name=full_name,
            section=SYM_BASE_NICKNAMES,
            sync_to_data=True,
        )
        self.reload_aliases()
        return key, value

    async def refresh_nickname_table_from_url(self, url: str) -> dict[str, Any]:
        result = await self._nickname_registry.refresh_default_from_url(url=url)
        self.reload_aliases()
        return result

    def _iter_item_names(self, item: MarketItem) -> list[str]:
        out: list[str] = [item.name, item.slug.replace("_", " ")]
        for name in item.i18n_names.values():
            if isinstance(name, str) and name:
                out.append(name)
        return out

    def _build_indexes(self, items: list[MarketItem]) -> None:
        self._items = items
        self._items_by_slug = {}
        self._items_by_name_key = {}
        self._items_token_index = {}

        for item in items:
            slug_key = normalize_alias_key(item.slug)
            if slug_key:
                self._items_by_slug[slug_key] = item

            token_set: set[str] = set()
            for name in self._iter_item_names(item):
                name_key = _normalize_name_key(name)
                if name_key:
                    self._items_by_name_key.setdefault(name_key, []).append(item)
                token_set.update(_tokenize_name(name))

            token_set.update(_tokenize_name(item.slug.replace("_", " ")))
            self._items_token_index[item.slug] = token_set

    def _row_to_item(self, row: dict[str, Any]) -> MarketItem | None:
        slug = row.get("slug")
        if not isinstance(slug, str) or not slug:
            return None

        item_id = row.get("id")
        if item_id is not None and not isinstance(item_id, str):
            item_id = None

        tags_raw = row.get("tags")
        if isinstance(tags_raw, list) and all(isinstance(t, str) for t in tags_raw):
            tags = tuple(tags_raw)
        else:
            tags = ()

        i18n_raw = row.get("i18n")
        i18n_names: dict[str, str] = {}
        wiki_link: str | None = None
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
                    wl = block.get("wikiLink")
                    if isinstance(wl, str) and wl:
                        wiki_link = wl
                    t = block.get("thumb")
                    if isinstance(t, str) and t:
                        thumb = t
                    ic = block.get("icon")
                    if isinstance(ic, str) and ic:
                        icon = ic

        en_name = i18n_names.get("en") or _humanize_name(slug)

        return MarketItem(
            item_id=item_id,
            slug=slug,
            name=en_name,
            wiki_link=wiki_link,
            tags=tags,
            i18n_names=i18n_names,
            thumb=thumb,
            icon=icon,
        )

    def _load_items_cache(self) -> bool:
        if not self._items_cache_path.exists():
            return False

        try:
            raw = json.loads(self._items_cache_path.read_text(encoding="utf-8"))
        except Exception:
            return False

        if not isinstance(raw, dict):
            return False

        ts = raw.get("ts")
        if not isinstance(ts, (int, float)):
            return False
        if (time.time() - float(ts)) > self._cache_ttl_sec:
            return False

        rows = raw.get("items")
        if not isinstance(rows, list):
            return False

        items: list[MarketItem] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            converted = self._row_to_item(row)
            if converted is not None:
                items.append(converted)

        if not items:
            return False

        self._build_indexes(items)
        return True

    def _save_items_cache(self) -> None:
        payload = {
            "ts": time.time(),
            "language": WARFRAME_MARKET_REQUEST_LANGUAGE,
            "items": [
                {
                    "id": item.item_id,
                    "slug": item.slug,
                    "name": item.name,
                    "wiki_link": item.wiki_link,
                    "tags": list(item.tags),
                    "i18n": {
                        locale: {"name": name}
                        for locale, name in item.i18n_names.items()
                    },
                    "thumb": item.thumb,
                    "icon": item.icon,
                }
                for item in self._items
            ],
        }
        try:
            self._items_cache_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as exc:
            logger.warning(f"Failed to save warframe.market items cache: {exc!s}")

    async def _fetch_items_v2(self) -> list[MarketItem]:
        url = f"{WARFRAME_MARKET_V2_BASE_URL}/items"
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

        out: list[MarketItem] = []
        for row in data:
            if not isinstance(row, dict):
                continue
            item = self._row_to_item(row)
            if item is not None:
                out.append(item)

        out.sort(key=lambda x: x.slug)
        return out

    async def refresh_items_cache(self) -> int:
        items = await self._fetch_items_v2()
        if not items:
            self._debug_log("refresh_items_cache", status="empty")
            return 0

        self._build_indexes(items)
        self._save_items_cache()
        self._debug_log("refresh_items_cache", status="ok", items=len(items))
        return len(items)

    def _resolve_alias(self, query: str) -> tuple[str | None, str, str]:
        q_norm = normalize_alias_key(query)
        if not q_norm:
            return None, "", ""

        if q_norm in self._alias_full_names:
            return q_norm, self._alias_full_names[q_norm], ""

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
            rest = q_norm[len(best_key) :]
            return best_key, best_name, rest

        return None, query, q_norm

    def _parse_modifiers(
        self,
        *,
        raw_query: str,
        alias_tail_norm: str,
    ) -> tuple[bool, bool, bool, str | None]:
        q_norm = normalize_alias_key(raw_query)
        lc_query = unicodedata.normalize("NFKC", raw_query).strip().lower()

        scope = alias_tail_norm or q_norm

        wants_prime = bool(
            ("prime" in scope)
            or ("圣装" in lc_query)
            or (scope.endswith("p") and len(scope) > 1)
        )
        wants_set = bool(("set" in scope) or ("组" in lc_query) or ("一套" in lc_query))
        wants_blueprint = bool(
            ("blueprint" in scope)
            or ("bp" in scope)
            or ("蓝图" in lc_query)
            or ("总图" in lc_query)
            or ("图纸" in lc_query)
        )

        part_hint: str | None = None
        part_map: tuple[tuple[str, tuple[str, ...]], ...] = (
            ("Neuroptics", ("neuro", "neuroptics", "头", "神经")),
            ("Chassis", ("chassis", "机体")),
            ("Systems", ("systems", "系统")),
        )
        for part_name, keywords in part_map:
            hit = False
            for kw in keywords:
                if kw in lc_query or kw in scope:
                    hit = True
                    break
            if hit:
                part_hint = part_name
                wants_blueprint = True
                break

        return wants_prime, wants_set, wants_blueprint, part_hint

    def _prepare_query(self, query: str) -> _PreparedItemQuery:
        alias_key, alias_full_name, alias_tail = self._resolve_alias(query)
        if not alias_full_name:
            alias_full_name = query

        (
            wants_prime,
            wants_set,
            wants_blueprint,
            part_hint,
        ) = self._parse_modifiers(raw_query=query, alias_tail_norm=alias_tail)

        canonical_name = _humanize_name(alias_full_name)
        if part_hint and part_hint.lower() not in canonical_name.lower():
            canonical_name = f"{canonical_name} {part_hint}".strip()

        prefer_prime = bool(alias_key and alias_key in self._base_alias_keys)

        return _PreparedItemQuery(
            raw_query=query,
            alias_key=alias_key,
            canonical_name=canonical_name,
            wants_prime=wants_prime,
            wants_set=wants_set,
            wants_blueprint=wants_blueprint,
            part_hint=part_hint,
            prefer_prime=prefer_prime,
        )

    def _extract_root_and_part(
        self,
        prepared: _PreparedItemQuery,
    ) -> tuple[str, str | None, bool, bool, bool]:
        words = [w for w in prepared.canonical_name.split(" ") if w]

        has_prime = False
        has_set = False
        has_blueprint = False
        part: str | None = None
        root_words: list[str] = []

        for w in words:
            lw = w.strip().lower()
            if lw == "prime":
                has_prime = True
                continue
            if lw == "set":
                has_set = True
                continue
            if lw == "blueprint":
                has_blueprint = True
                continue
            if lw in {"neuroptics", "chassis", "systems"}:
                part = lw.capitalize() if lw != "systems" else "Systems"
                continue
            root_words.append(w)

        if part is None and prepared.part_hint:
            part = prepared.part_hint

        root = " ".join(root_words).strip()
        if not root:
            root = prepared.canonical_name.strip()
        return root, part, has_prime, has_set, has_blueprint

    def _build_name_candidates(self, prepared: _PreparedItemQuery) -> list[str]:
        root, part, has_prime, has_set, has_blueprint = self._extract_root_and_part(
            prepared
        )

        candidates: list[str] = []

        def _add(name: str) -> None:
            name = re.sub(r"\s+", " ", str(name or "")).strip()
            if not name:
                return
            if name not in candidates:
                candidates.append(name)

        _add(prepared.canonical_name)

        if part:
            _add(f"{root} {part}")
            _add(f"{root} {part} Blueprint")
            _add(f"{root} Prime {part}")
            _add(f"{root} Prime {part} Blueprint")

        should_try_prime = prepared.wants_prime or has_prime or prepared.prefer_prime
        should_try_set = prepared.wants_set or has_set or prepared.prefer_prime

        if should_try_prime and not part:
            _add(f"{root} Prime")
        if should_try_set:
            _add(f"{root} Prime Set")
        if prepared.wants_set and not should_try_prime:
            _add(f"{root} Set")

        if prepared.wants_blueprint or has_blueprint:
            _add(f"{root} Blueprint")
            _add(f"{root} Prime Blueprint")

        _add(root)
        _add(prepared.raw_query)
        return candidates

    def _score_item_match(
        self,
        *,
        item: MarketItem,
        query_tokens: set[str],
        prepared: _PreparedItemQuery,
    ) -> int:
        item_tokens = self._items_token_index.get(item.slug, set())
        if not query_tokens:
            return -10_000

        if not query_tokens.issubset(item_tokens):
            return -10_000

        score = 100
        score -= max(0, len(item_tokens) - len(query_tokens))

        generic = {"prime", "set", "blueprint"}
        non_generic = [t for t in query_tokens if t not in generic]
        if not non_generic:
            return -10_000

        if (prepared.prefer_prime or prepared.wants_prime) and "prime" in item_tokens:
            score += 10
        elif prepared.wants_prime and "prime" not in item_tokens:
            score -= 8

        if prepared.wants_set and "set" in item_tokens:
            score += 8
        elif prepared.wants_set and "set" not in item_tokens:
            score -= 8

        if prepared.wants_blueprint and "blueprint" in item_tokens:
            score += 6

        if prepared.part_hint:
            if prepared.part_hint.lower() in item_tokens:
                score += 12
            else:
                score -= 12

        return score

    def _resolve_prepared(self, prepared: _PreparedItemQuery) -> MarketItem | None:
        if not self._items:
            self._debug_log("resolve_prepared", query=prepared.raw_query, status="no_items")
            return None

        candidates = self._build_name_candidates(prepared)
        self._debug_log(
            "resolve_prepared_start",
            query=prepared.raw_query,
            alias_key=prepared.alias_key,
            canonical=prepared.canonical_name,
            candidates=candidates[:8],
            candidate_count=len(candidates),
        )

        direct_slug_key = normalize_alias_key(prepared.raw_query)
        if direct_slug_key in self._items_by_slug:
            hit = self._items_by_slug[direct_slug_key]
            self._debug_log(
                "resolve_hit",
                stage="direct_slug",
                query=prepared.raw_query,
                slug=hit.slug,
                name=hit.name,
            )
            return self._items_by_slug[direct_slug_key]

        for cand in candidates:
            slug = _slugify_text(cand)
            if slug and slug in self._items_by_slug:
                hit = self._items_by_slug[slug]
                self._debug_log(
                    "resolve_hit",
                    stage="candidate_slug",
                    candidate=cand,
                    slug=hit.slug,
                    name=hit.name,
                )
                return self._items_by_slug[slug]

        for cand in candidates:
            key = _normalize_name_key(cand)
            if not key:
                continue
            matches = self._items_by_name_key.get(key)
            if not matches:
                continue
            if len(matches) == 1:
                self._debug_log(
                    "resolve_hit",
                    stage="name_exact_single",
                    candidate=cand,
                    slug=matches[0].slug,
                    name=matches[0].name,
                )
                return matches[0]

            best_match = None
            best_score = -10_000
            tokens = set(_tokenize_name(cand))
            for m in matches:
                score = self._score_item_match(
                    item=m,
                    query_tokens=tokens,
                    prepared=prepared,
                )
                if score > best_score:
                    best_score = score
                    best_match = m
            if best_match is not None:
                self._debug_log(
                    "resolve_hit",
                    stage="name_exact_scored",
                    candidate=cand,
                    best_score=best_score,
                    slug=best_match.slug,
                    name=best_match.name,
                )
                return best_match

        best_item: MarketItem | None = None
        best_score = -10_000
        for cand in candidates:
            tokens = set(_tokenize_name(cand))
            if not tokens:
                continue
            for item in self._items:
                score = self._score_item_match(
                    item=item,
                    query_tokens=tokens,
                    prepared=prepared,
                )
                if score > best_score:
                    best_score = score
                    best_item = item

        if best_item is None:
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
            slug=best_item.slug,
            name=best_item.name,
        )
        return best_item

    async def resolve(self, query: str) -> MarketItem | None:
        item, _ = await self.resolve_with_trace(query)
        return item

    async def resolve_with_trace(
        self,
        query: str,
    ) -> tuple[MarketItem | None, MarketResolveTrace]:
        await self.initialize()

        q = str(query or "").strip()
        if not q:
            trace = MarketResolveTrace(
                original_query=q,
                alias_key=None,
                canonical_full_name="",
                matched_item_name=None,
                matched_slug=None,
            )
            return None, trace

        prepared = self._prepare_query(q)
        item = self._resolve_prepared(prepared)

        trace = MarketResolveTrace(
            original_query=q,
            alias_key=prepared.alias_key,
            canonical_full_name=prepared.canonical_name,
            matched_item_name=(item.name if item else None),
            matched_slug=(item.slug if item else None),
        )
        self._debug_log(
            "resolve_with_trace",
            query=q,
            alias_key=trace.alias_key,
            canonical=trace.canonical_full_name,
            matched_name=trace.matched_item_name,
            matched_slug=trace.matched_slug,
        )
        return item, trace

