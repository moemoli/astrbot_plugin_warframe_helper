from __future__ import annotations

import json
import lzma
import re
import struct
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from astrbot.api import logger
from astrbot.api.star import StarTools

from ..http_utils import fetch_bytes, fetch_json

PUBLIC_EXPORT_BASE = "https://content.warframe.com/PublicExport"


def _normalize_query(s: str) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"\s+", " ", s)
    return s


def _slug(s: str) -> str:
    s = _normalize_query(s)
    s = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", s)
    return s


def _lzma_alone_decompress(data: bytes) -> bytes:
    """Decompress .lzma with the legacy LZMA-Alone header.

    content.warframe.com's PublicExport index_*.txt.lzma uses the LZMA-Alone
    container with RAW payload; Python's lzma.decompress(FORMAT_AUTO) may fail.
    """

    if len(data) < 13:
        raise ValueError("lzma-alone data too short")

    props = data[0]
    dict_size = struct.unpack("<I", data[1:5])[0]

    lc = props % 9
    remainder = props // 9
    lp = remainder % 5
    pb = remainder // 5

    filters = [
        {
            "id": lzma.FILTER_LZMA1,
            "dict_size": dict_size,
            "lc": lc,
            "lp": lp,
            "pb": pb,
        }
    ]

    # Skip LZMA-Alone header (13 bytes), remaining is RAW LZMA1.
    return lzma.decompress(data[13:], format=lzma.FORMAT_RAW, filters=filters)


@dataclass(frozen=True, slots=True)
class PublicExportIndex:
    language: str
    fetched_at: float
    file_tokens: dict[str, str]


class PublicExportClient:
    def __init__(
        self,
        *,
        cache_ttl_sec: float = 6 * 60 * 60,
        http_timeout_sec: float = 30.0,
        export_cache_max: int = 64,
        map_cache_max: int = 32,
    ) -> None:
        self._cache_ttl_sec = float(cache_ttl_sec)
        self._http_timeout_sec = float(http_timeout_sec)
        self._mem_index: dict[str, PublicExportIndex] = {}
        self._mem_exports: dict[str, tuple[float, Any]] = {}
        self._mem_unique_name_maps: dict[str, dict[str, str]] = {}
        self._mem_unique_name_norm_maps: dict[str, dict[str, str]] = {}
        self._mem_region_maps: dict[str, dict[str, str]] = {}
        self._mem_nightwave_map: dict[str, dict[str, tuple[str, int | None]]] = {}
        # localized slug -> [english names]
        self._mem_localized_to_en: dict[str, dict[str, list[str]]] = {}
        # english slug -> localized name
        self._mem_en_to_localized: dict[str, dict[str, str]] = {}
        self._export_cache_max = max(10, int(export_cache_max))
        self._map_cache_max = max(10, int(map_cache_max))

    def _evict_map_cache(self, cache: dict) -> None:
        over = len(cache) - self._map_cache_max
        if over <= 0:
            return
        for _ in range(over):
            cache.pop(next(iter(cache)), None)

    @staticmethod
    def _has_cjk(s: str) -> bool:
        return any("\u4e00" <= ch <= "\u9fff" for ch in (s or ""))

    @staticmethod
    def _normalize_unique_name_key(s: str) -> str:
        raw = (s or "").strip().replace("\\", "/").lower()
        raw = re.sub(r"/+", "/", raw)
        return raw

    async def _get_english_to_localized_map(self, *, language: str) -> dict[str, str]:
        """Build a best-effort mapping: English display name -> localized display name.

        Used for data sources that return English names even under zh worldstate.
        """

        lang = (language or "zh").strip().lower() or "zh"
        cached = self._mem_en_to_localized.get(lang)
        if cached is not None:
            return cached

        # Export types most likely to cover Duviri Circuit rewards.
        export_specs = [
            ("Weapons", "ExportWeapons"),
            ("Warframes", "ExportWarframes"),
            ("Upgrades", "ExportUpgrades"),
            ("Gear", "ExportGear"),
            ("Customs", "ExportCustoms"),
        ]

        out: dict[str, str] = {}
        for short, list_key in export_specs:
            en_data = await self.fetch_export(f"Export{short}_en.json", language="en")
            loc_data = await self.fetch_export(
                f"Export{short}_{lang}.json", language=lang
            )
            if not isinstance(en_data, dict) or not isinstance(loc_data, dict):
                continue

            en_list = en_data.get(list_key)
            loc_list = loc_data.get(list_key)
            if not isinstance(en_list, list) or not isinstance(loc_list, list):
                continue

            en_by_unique: dict[str, str] = {}
            for row in en_list:
                if not isinstance(row, dict):
                    continue
                uniq = row.get("uniqueName")
                name = row.get("name")
                if isinstance(uniq, str) and isinstance(name, str) and uniq and name:
                    en_by_unique.setdefault(uniq, name)

            for row in loc_list:
                if not isinstance(row, dict):
                    continue
                uniq = row.get("uniqueName")
                loc_name = row.get("name")
                if not isinstance(uniq, str) or not isinstance(loc_name, str):
                    continue
                en_name = en_by_unique.get(uniq)
                if not en_name:
                    continue
                key = _slug(en_name)
                if key and key not in out:
                    out[key] = loc_name

        self._mem_en_to_localized[lang] = out
        self._evict_map_cache(self._mem_en_to_localized)
        return out

    async def translate_display_name(
        self, name: str, *, language: str = "zh"
    ) -> str | None:
        """Translate a *display name* to a localized display name.

        Priority:
        1) If it looks like a uniqueName path, use uniqueName map.
        2) If already contains CJK characters, keep as-is.
        3) Otherwise, treat it as English name and map to localized via PublicExport.
        """

        lang = (language or "zh").strip().lower() or "zh"
        raw = (name or "").strip()
        if not raw:
            return None

        # uniqueName paths in PublicExport usually start with "/".
        if raw.startswith("/"):
            mapped = await self.translate_unique_name(raw, language=lang)
            if mapped:
                return mapped

        if self._has_cjk(raw):
            return raw

        mapping = await self._get_english_to_localized_map(language=lang)
        return mapping.get(_slug(raw))

    def _base_dir(self) -> Path:
        base = StarTools.get_data_dir("warframe_helper")
        return base / "public_export"

    def _index_cache_path(self, language: str) -> Path:
        lang = (language or "zh").strip().lower() or "zh"
        return self._base_dir() / f"index_{lang}.json"

    def _export_cache_path(self, language: str, filename: str, token: str) -> Path:
        lang = (language or "zh").strip().lower() or "zh"
        safe = filename.replace("/", "_")
        return self._base_dir() / "exports" / lang / f"{safe}!{token}.json"

    def _is_fresh(self, fetched_at: float) -> bool:
        return (time.time() - float(fetched_at)) <= self._cache_ttl_sec

    async def get_index(self, *, language: str = "zh") -> PublicExportIndex | None:
        lang = (language or "zh").strip().lower() or "zh"
        cached = self._mem_index.get(lang)
        if cached and self._is_fresh(cached.fetched_at):
            return cached

        disk_path = self._index_cache_path(lang)
        try:
            if disk_path.exists():
                payload = json.loads(disk_path.read_text("utf-8"))
                if (
                    isinstance(payload, dict)
                    and payload.get("language") == lang
                    and isinstance(payload.get("fetched_at"), (int, float))
                    and isinstance(payload.get("file_tokens"), dict)
                    and self._is_fresh(float(payload["fetched_at"]))
                ):
                    idx = PublicExportIndex(
                        language=lang,
                        fetched_at=float(payload["fetched_at"]),
                        file_tokens={
                            str(k): str(v)
                            for k, v in payload["file_tokens"].items()
                            if isinstance(k, str) and isinstance(v, str)
                        },
                    )
                    self._mem_index[lang] = idx
                    return idx
        except Exception as exc:
            logger.debug(f"PublicExport index cache read failed: {exc!s}")

        idx_url = f"{PUBLIC_EXPORT_BASE}/index_{lang}.txt.lzma"
        raw = await fetch_bytes(idx_url, timeout_sec=self._http_timeout_sec)
        if raw is None:
            return None

        try:
            text = _lzma_alone_decompress(raw).decode("utf-8", "replace")
        except Exception as exc:
            logger.warning(f"PublicExport index decompress failed: {exc!s}")
            return None

        file_tokens: dict[str, str] = {}
        for line in text.splitlines():
            line = line.strip()
            if not line or "!" not in line:
                continue
            filename, token = line.split("!", 1)
            filename = filename.strip()
            token = token.strip()
            if filename and token:
                file_tokens[filename] = token

        idx = PublicExportIndex(
            language=lang, fetched_at=time.time(), file_tokens=file_tokens
        )
        self._mem_index[lang] = idx

        try:
            disk_path.parent.mkdir(parents=True, exist_ok=True)
            disk_path.write_text(
                json.dumps(
                    {
                        "language": idx.language,
                        "fetched_at": idx.fetched_at,
                        "file_tokens": idx.file_tokens,
                    },
                    ensure_ascii=False,
                ),
                "utf-8",
            )
        except Exception as exc:
            logger.debug(f"PublicExport index cache write failed: {exc!s}")

        return idx

    async def fetch_export(self, filename: str, *, language: str = "zh") -> Any | None:
        lang = (language or "zh").strip().lower() or "zh"
        filename = (filename or "").strip()
        if not filename:
            return None

        index = await self.get_index(language=lang)
        if index is None:
            return None

        token = index.file_tokens.get(filename)
        if not token:
            logger.warning(f"PublicExport token not found: {filename} ({lang})")
            return None

        mem_key = f"{lang}:{filename}!{token}"
        cached = self._mem_exports.get(mem_key)
        if cached and self._is_fresh(cached[0]):
            return cached[1]

        disk_path = self._export_cache_path(lang, filename, token)
        try:
            if disk_path.exists():
                data = json.loads(disk_path.read_text("utf-8"))
                self._mem_exports[mem_key] = (time.time(), data)
                if len(self._mem_exports) > self._export_cache_max:
                    self._mem_exports.pop(next(iter(self._mem_exports)), None)
                return data
        except Exception as exc:
            logger.debug(f"PublicExport export cache read failed: {exc!s}")

        url = f"{PUBLIC_EXPORT_BASE}/Manifest/{filename}!{token}"
        data = await fetch_json(url, timeout_sec=self._http_timeout_sec)
        if data is None:
            return None

        self._mem_exports[mem_key] = (time.time(), data)
        if len(self._mem_exports) > self._export_cache_max:
            self._mem_exports.pop(next(iter(self._mem_exports)), None)
        try:
            disk_path.parent.mkdir(parents=True, exist_ok=True)
            disk_path.write_text(json.dumps(data, ensure_ascii=False), "utf-8")
        except Exception as exc:
            logger.debug(f"PublicExport export cache write failed: {exc!s}")
        return data

    async def get_region_name_map(self, *, language: str = "zh") -> dict[str, str]:
        lang = (language or "zh").strip().lower() or "zh"
        cached = self._mem_region_maps.get(lang)
        if cached is not None:
            return cached

        data = await self.fetch_export(f"ExportRegions_{lang}.json", language=lang)
        out: dict[str, str] = {}
        if isinstance(data, dict):
            regions = data.get("ExportRegions")
            if isinstance(regions, list):
                for r in regions:
                    if not isinstance(r, dict):
                        continue
                    uniq = r.get("uniqueName")
                    name = r.get("name")
                    system = r.get("systemName")
                    if not isinstance(uniq, str) or not isinstance(name, str):
                        continue
                    if isinstance(system, str) and system.strip():
                        out[uniq] = f"{system}·{name}"
                    else:
                        out[uniq] = name

        self._mem_region_maps[lang] = out
        self._evict_map_cache(self._mem_region_maps)
        return out

    async def get_unique_name_map(self, *, language: str = "zh") -> dict[str, str]:
        lang = (language or "zh").strip().lower() or "zh"
        cached = self._mem_unique_name_maps.get(lang)
        if cached is not None:
            return cached

        file_list = [
            f"ExportCustoms_{lang}.json",
            f"ExportResources_{lang}.json",
            f"ExportUpgrades_{lang}.json",
            f"ExportWeapons_{lang}.json",
            f"ExportWarframes_{lang}.json",
            f"ExportGear_{lang}.json",
            f"ExportKeys_{lang}.json",
            f"ExportDrones_{lang}.json",
            f"ExportSentinels_{lang}.json",
        ]

        out: dict[str, str] = {}
        for fname in file_list:
            data = await self.fetch_export(fname, language=lang)
            if not isinstance(data, dict):
                continue
            for key, arr in data.items():
                if not key.startswith("Export"):
                    continue
                if not isinstance(arr, list):
                    continue
                for row in arr:
                    if not isinstance(row, dict):
                        continue
                    uniq = row.get("uniqueName")
                    name = row.get("name")
                    if (
                        isinstance(uniq, str)
                        and isinstance(name, str)
                        and uniq
                        and name
                    ):
                        out.setdefault(uniq, name)

        self._mem_unique_name_maps[lang] = out
        self._evict_map_cache(self._mem_unique_name_maps)
        return out

    async def translate_unique_name(
        self, unique_name: str, *, language: str = "zh"
    ) -> str | None:
        unique_name = (unique_name or "").strip()
        if not unique_name:
            return None
        mapping = await self.get_unique_name_map(language=language)
        return mapping.get(unique_name)

    async def translate_unique_name_loose(
        self, unique_name: str, *, language: str = "zh"
    ) -> str | None:
        raw = (unique_name or "").strip()
        if not raw:
            return None

        exact = await self.translate_unique_name(raw, language=language)
        if exact:
            return exact

        lang = (language or "zh").strip().lower() or "zh"
        norm = self._normalize_unique_name_key(raw)
        if not norm:
            return None

        norm_map = self._mem_unique_name_norm_maps.get(lang)
        if norm_map is None:
            mapping = await self.get_unique_name_map(language=lang)
            norm_map = {}
            for k, v in mapping.items():
                if not isinstance(k, str) or not isinstance(v, str):
                    continue
                nk = self._normalize_unique_name_key(k)
                if not nk or nk in norm_map:
                    continue
                norm_map[nk] = v
            self._mem_unique_name_norm_maps[lang] = norm_map
            self._evict_map_cache(self._mem_unique_name_norm_maps)

        hit = norm_map.get(norm)
        if hit:
            return hit

        parts = [p for p in norm.split("/") if p]
        if not parts:
            return None

        tail = parts[-1]
        if not tail:
            return None

        candidates: list[tuple[int, str]] = []
        tail_suffix = f"/{tail}"
        for k, v in norm_map.items():
            if k.endswith(tail_suffix):
                candidates.append((len(k), v))

        if len(parts) >= 2:
            tail2_suffix = f"/{parts[-2]}/{parts[-1]}"
            for k, v in norm_map.items():
                if k.endswith(tail2_suffix):
                    candidates.append((len(k) - 1000, v))

        if not candidates:
            return None

        candidates.sort(key=lambda x: x[0])
        return candidates[0][1]

    async def translate_region(
        self, node_unique: str, *, language: str = "zh"
    ) -> str | None:
        node_unique = (node_unique or "").strip()
        if not node_unique:
            return None
        mapping = await self.get_region_name_map(language=language)
        return mapping.get(node_unique)

    async def get_nightwave_challenge_map(
        self, *, language: str = "zh"
    ) -> dict[str, tuple[str, int | None]]:
        lang = (language or "zh").strip().lower() or "zh"
        cached = self._mem_nightwave_map.get(lang)
        if cached is not None:
            return cached

        data = await self.fetch_export(
            f"ExportSortieRewards_{lang}.json", language=lang
        )
        out: dict[str, tuple[str, int | None]] = {}
        if isinstance(data, dict):
            nw = data.get("ExportNightwave")
            if isinstance(nw, dict):
                challenges = nw.get("challenges")
                if isinstance(challenges, list):
                    for c in challenges:
                        if not isinstance(c, dict):
                            continue
                        uniq = c.get("uniqueName")
                        name = c.get("name")
                        standing = c.get("standing")
                        if not isinstance(uniq, str) or not isinstance(name, str):
                            continue
                        out[uniq] = (
                            name,
                            standing if isinstance(standing, int) else None,
                        )

        self._mem_nightwave_map[lang] = out
        self._evict_map_cache(self._mem_nightwave_map)
        return out

    async def search_weapon(
        self,
        query: str,
        *,
        language: str = "zh",
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        return await self._search_export_list(
            export_filename=f"ExportWeapons_{(language or 'zh').strip().lower() or 'zh'}.json",
            list_key="ExportWeapons",
            query=query,
            language=language,
            limit=limit,
        )

    async def _search_export_list(
        self,
        *,
        export_filename: str,
        list_key: str,
        query: str,
        language: str = "zh",
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        lang = (language or "zh").strip().lower() or "zh"
        q = _normalize_query(query)
        if not q:
            return []

        data = await self.fetch_export(export_filename, language=lang)
        rows = data.get(list_key) if isinstance(data, dict) else None
        if not isinstance(rows, list):
            return []

        q_slug = _slug(q)

        scored: list[tuple[int, dict[str, Any]]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            name = row.get("name")
            uniq = row.get("uniqueName")
            if not isinstance(name, str) or not name:
                continue

            name_n = _normalize_query(name)
            name_slug = _slug(name)
            uniq_s = uniq if isinstance(uniq, str) else ""

            score = 0
            if q_slug and name_slug == q_slug:
                score = 0
            elif q and name_n == q:
                score = 1
            elif q_slug and q_slug in name_slug:
                score = 5
            elif q and q in name_n:
                score = 8
            elif q_slug and q_slug in _slug(uniq_s):
                score = 12
            else:
                continue

            scored.append((score, row))

        scored.sort(key=lambda x: x[0])
        return [r for _, r in scored[: max(1, min(int(limit), 20))]]

    async def search_warframe(
        self,
        query: str,
        *,
        language: str = "zh",
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        lang = (language or "zh").strip().lower() or "zh"
        return await self._search_export_list(
            export_filename=f"ExportWarframes_{lang}.json",
            list_key="ExportWarframes",
            query=query,
            language=lang,
            limit=limit,
        )

    async def search_mod(
        self,
        query: str,
        *,
        language: str = "zh",
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        lang = (language or "zh").strip().lower() or "zh"
        # ExportUpgrades contains mods and other upgrade items; we do best-effort filtering.
        rows = await self._search_export_list(
            export_filename=f"ExportUpgrades_{lang}.json",
            list_key="ExportUpgrades",
            query=query,
            language=lang,
            limit=max(1, min(int(limit), 20)),
        )
        out: list[dict[str, Any]] = []
        for r in rows:
            # Heuristic: Mods usually have 'fusionLimit' or 'modType' fields.
            if not isinstance(r, dict):
                continue
            if any(k in r for k in ["fusionLimit", "modType", "polarity", "rarity"]):
                out.append(r)
        return out

    async def _get_localized_to_en_map(self, *, language: str) -> dict[str, list[str]]:
        lang = (language or "zh").strip().lower() or "zh"
        cached = self._mem_localized_to_en.get(lang)
        if cached is not None:
            return cached

        # Build mapping via uniqueName intersection.
        local_map = await self.get_unique_name_map(language=lang)
        en_map = await self.get_unique_name_map(language="en")

        out: dict[str, list[str]] = {}
        for uniq, local_name in local_map.items():
            en_name = en_map.get(uniq)
            if not isinstance(en_name, str) or not en_name.strip():
                continue
            if not isinstance(local_name, str) or not local_name.strip():
                continue
            k = _slug(local_name)
            if not k:
                continue
            arr = out.setdefault(k, [])
            if en_name not in arr:
                arr.append(en_name)

        self._mem_localized_to_en[lang] = out
        self._evict_map_cache(self._mem_localized_to_en)
        return out

    async def resolve_localized_to_english_candidates(
        self,
        query: str,
        *,
        language: str = "zh",
        limit: int = 5,
    ) -> list[str]:
        """Resolve a localized item name (e.g. Chinese) to possible English names.

        This is a best-effort helper for data sources that primarily use English
        names (e.g. drop-data). It uses PublicExport uniqueName to align names
        across languages.
        """

        q = _slug(query)
        if not q:
            return []

        mapping = await self._get_localized_to_en_map(language=language)

        # Exact match first.
        exact = mapping.get(q)
        if exact:
            return exact[: max(1, min(int(limit), 10))]

        # Substring match fallback.
        scored: list[tuple[int, str]] = []
        for k, names in mapping.items():
            if q not in k:
                continue
            # Prefer prefix and shorter distance.
            score = 0
            if k.startswith(q):
                score += 50
            score -= abs(len(k) - len(q))
            for n in names:
                if isinstance(n, str) and n.strip():
                    scored.append((score, n.strip()))

        scored.sort(key=lambda x: (-int(x[0]), x[1]))
        out: list[str] = []
        for _, name in scored:
            if name in out:
                continue
            out.append(name)
            if len(out) >= max(1, min(int(limit), 10)):
                break
        return out
