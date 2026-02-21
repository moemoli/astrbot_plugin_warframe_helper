from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from astrbot.api import logger
from astrbot.core.utils.astrbot_path import get_astrbot_plugin_data_path

from ..http_utils import fetch_json

_DROP_DATA_BASE_URLS: list[str] = [
    # Some environments may block drops.warframestat.us; keep GitHub raw/CDN fallbacks.
    "https://raw.githubusercontent.com/WFCD/warframe-drop-data/master/data",
    "https://raw.githubusercontent.com/WFCD/warframe-drop-data/main/data",
    "https://cdn.jsdelivr.net/gh/WFCD/warframe-drop-data@master/data",
    "https://cdn.jsdelivr.net/gh/WFCD/warframe-drop-data@main/data",
]


def _normalize_query(text: str) -> str:
    s = (text or "").strip().lower()
    s = re.sub(r"\s+", "", s)
    return s


def _safe_relic_name(text: str) -> str:
    # Axi A1 -> A1; keep alnum only.
    s = (text or "").strip()
    s = re.sub(r"\s+", "", s)
    s = re.sub(r"[^A-Za-z0-9]", "", s)
    return s


@dataclass(frozen=True, slots=True)
class DropDataCache:
    fetched_at: float
    data: Any


class DropDataClient:
    def __init__(
        self,
        *,
        cache_ttl_sec: float = 24 * 60 * 60,
        http_timeout_sec: float = 30.0,
    ) -> None:
        self._cache_ttl_sec = float(cache_ttl_sec)
        self._http_timeout_sec = float(http_timeout_sec)

        self._mem_all_slim: DropDataCache | None = None
        self._mem_relics_index: DropDataCache | None = None
        self._mem_relic_detail: dict[str, DropDataCache] = {}

    def _base_dir(self) -> Path:
        base = Path(get_astrbot_plugin_data_path())
        return base / "astrbot_plugin_warframe_helper" / "drop_data"

    def _cache_path(self, name: str) -> Path:
        safe = re.sub(r"[^A-Za-z0-9._\-]+", "_", (name or "").strip())
        return self._base_dir() / safe

    def _is_fresh(self, fetched_at: float) -> bool:
        return (time.time() - float(fetched_at)) <= self._cache_ttl_sec

    async def _get_cached_json(self, *, cache_key: str, urls: list[str]) -> Any | None:
        disk_path = self._cache_path(f"{cache_key}.json")

        try:
            if disk_path.exists():
                payload = json.loads(disk_path.read_text("utf-8"))
                if (
                    isinstance(payload, dict)
                    and isinstance(payload.get("fetched_at"), (int, float))
                    and self._is_fresh(float(payload["fetched_at"]))
                    and "data" in payload
                ):
                    return payload.get("data")
        except Exception:
            pass

        data = await fetch_json(urls, timeout_sec=self._http_timeout_sec)
        if data is None:
            return None

        try:
            disk_path.parent.mkdir(parents=True, exist_ok=True)
            disk_path.write_text(
                json.dumps(
                    {"fetched_at": time.time(), "data": data}, ensure_ascii=False
                ),
                "utf-8",
            )
        except Exception as exc:
            logger.debug(f"drop-data cache write failed: {exc!s}")

        return data

    def _urls(self, path: str) -> list[str]:
        p = (path or "").lstrip("/")
        return [f"{b}/{p}" for b in _DROP_DATA_BASE_URLS]

    async def get_all_slim(self) -> list[dict] | None:
        if self._mem_all_slim and self._is_fresh(self._mem_all_slim.fetched_at):
            if isinstance(self._mem_all_slim.data, list):
                return self._mem_all_slim.data

        data = await self._get_cached_json(
            cache_key="all.slim", urls=self._urls("all.slim.json")
        )
        if not isinstance(data, list):
            return None

        items = [x for x in data if isinstance(x, dict)]
        self._mem_all_slim = DropDataCache(fetched_at=time.time(), data=items)
        return items

    async def get_relics_index(self) -> list[dict] | None:
        if self._mem_relics_index and self._is_fresh(self._mem_relics_index.fetched_at):
            if isinstance(self._mem_relics_index.data, list):
                return self._mem_relics_index.data

        data = await self._get_cached_json(
            cache_key="relics", urls=self._urls("relics.json")
        )

        relics: list[dict] | None = None
        if isinstance(data, dict) and isinstance(data.get("relics"), list):
            relics = [x for x in data["relics"] if isinstance(x, dict)]
        elif isinstance(data, list):
            relics = [x for x in data if isinstance(x, dict)]

        if relics is None:
            return None

        self._mem_relics_index = DropDataCache(fetched_at=time.time(), data=relics)
        return relics

    async def find_relic_tiers(self, relic_name: str) -> list[str]:
        name = _safe_relic_name(relic_name)
        if not name:
            return []

        relics = await self.get_relics_index()
        if not relics:
            return []

        tiers: list[str] = []
        name_u = name.upper()
        for r in relics:
            rn = r.get("relicName")
            tier = r.get("tier")
            if not isinstance(rn, str) or not isinstance(tier, str):
                continue
            if _safe_relic_name(rn).upper() != name_u:
                continue
            t = tier.strip()
            if t and t not in tiers:
                tiers.append(t)

        return tiers

    async def get_relic_detail(self, *, tier: str, relic_name: str) -> dict | None:
        tier_s = (tier or "").strip()
        name = _safe_relic_name(relic_name)
        if not tier_s or not name:
            return None

        cache_key = f"relic_{tier_s}_{name}"
        mem = self._mem_relic_detail.get(cache_key)
        if mem and self._is_fresh(mem.fetched_at) and isinstance(mem.data, dict):
            return mem.data

        data = await self._get_cached_json(
            cache_key=cache_key,
            urls=self._urls(f"relics/{tier_s}/{name}.json"),
        )
        if not isinstance(data, dict):
            return None

        self._mem_relic_detail[cache_key] = DropDataCache(
            fetched_at=time.time(), data=data
        )
        return data

    async def search_drops(self, *, item_query: str, limit: int = 15) -> list[dict]:
        qn = _normalize_query(item_query)
        if not qn:
            return []

        all_slim = await self.get_all_slim()
        if not all_slim:
            return []

        matched: list[dict] = []
        for row in all_slim:
            item = row.get("item")
            if not isinstance(item, str) or not item.strip():
                continue
            if qn not in _normalize_query(item):
                continue
            matched.append(row)

        def chance_key(x: dict) -> float:
            v = x.get("chance")
            if isinstance(v, (int, float)):
                return float(v)
            if isinstance(v, str):
                try:
                    return float(v)
                except Exception:
                    return 0.0
            return 0.0

        matched.sort(
            key=lambda x: (
                -chance_key(x),
                str(x.get("rarity") or ""),
                str(x.get("place") or ""),
                str(x.get("item") or ""),
            )
        )

        limit = max(1, min(int(limit), 30))
        return matched[:limit]
