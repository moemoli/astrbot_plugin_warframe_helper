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
from astrbot.core.utils.astrbot_path import (
    get_astrbot_plugin_data_path,
    get_astrbot_temp_path,
)

WARFRAME_MARKET_V2_BASE_URL = "https://api.warframe.market/v2"


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
        """按语言获取物品名称。

        warframe.market v2 的 i18n 不一定包含所有语言；缺失时会回退到英文/默认名。
        """

        if not lang:
            return self.name
        lang_norm = str(lang).strip().lower()
        if not lang_norm:
            return self.name

        # 常见别名归一化
        alias_map = {
            "cn": "zh",
            "zh-cn": "zh",
            "zh-hans": "zh",
            "zh-hant": "zh-tw",
            "tw": "zh-tw",
        }
        lang_norm = alias_map.get(lang_norm, lang_norm)

        if self.i18n_names:
            if lang_norm in self.i18n_names:
                return self.i18n_names[lang_norm]
            if lang_norm.replace("_", "-") in self.i18n_names:
                return self.i18n_names[lang_norm.replace("_", "-")]
            if lang_norm.replace("-", "_") in self.i18n_names:
                return self.i18n_names[lang_norm.replace("-", "_")]
            if "en" in self.i18n_names:
                return self.i18n_names["en"]
        return self.name


class WarframeTermMapper:
    """将用户输入的别名/简写解析为 warframe.market 的官方词条。

    设计目标：
    - 内置别名在离线也能工作（例如：猴p -> Wukong Prime Set）。
    - 网络可用时，从 warframe.market v2 API 拉取标准数据。
    - 缓存已拉取的词条详情，减少重复请求。
    - 支持通过 plugin_data 下的 JSON 文件扩展别名。
    """

    _BUILTIN_BASE_NICKNAMES: dict[str, str] = {
        # 战甲别名/简称 -> warframe.market prime 词条 slug 的“基名”
        # 说明：这里的 value 不是完整 item slug，而是用于拼接：
        # - “p/prime” => f"{base}_prime_set"
        # - “p机体/蓝图/系统/头/总图” => f"{base}_prime_<part>"
        # 因此优先收录“确定无歧义”的官方中文名/常用外号；有歧义的建议放 aliases.json。
        # ---- 起始战甲 / 老牌常用 ----
        "excalibur": "excalibur",
        "圣剑": "excalibur",
        "剑男": "excalibur",
        "mag": "mag",
        "磁力": "mag",
        "磁妈": "mag",
        "volt": "volt",
        "伏特": "volt",
        "电男": "volt",
        "rhino": "rhino",
        "犀牛": "rhino",
        "loki": "loki",
        "洛基": "loki",
        "老洛": "loki",
        "ember": "ember",
        "灰烬": "ember",
        "火女": "ember",
        "frost": "frost",
        "冰霜": "frost",
        "冰男": "frost",
        "trinity": "trinity",
        "三位一体": "trinity",
        "奶妈": "trinity",
        "三妈": "trinity",
        "nova": "nova",
        "新星": "nova",
        "saryn": "saryn",
        "沙林": "saryn",
        "毒妈": "saryn",
        "mesa": "mesa",
        "魅莎": "mesa",
        "女枪": "mesa",
        "nekros": "nekros",
        "死灵": "nekros",
        "摸尸": "nekros",
        "hydroid": "hydroid",
        "海盗": "hydroid",
        "水男": "hydroid",
        "ivara": "ivara",
        "伊瓦拉": "ivara",
        "弓妹": "ivara",
        "inaros": "inaros",
        "伊纳罗斯": "inaros",
        "沙甲": "inaros",
        "wukong": "wukong",
        "猴": "wukong",
        "悟空": "wukong",
        "nezha": "nezha",
        "哪吒": "nezha",
        "nidus": "nidus",
        "尼德斯": "nidus",
        # ---- 其他常见 Prime 战甲（官方中文名/常用称呼） ----
        "ash": "ash",
        "灰烬之刃": "ash",
        "banshee": "banshee",
        "女妖": "banshee",
        "nyx": "nyx",
        "灵煞": "nyx",
        "脑溢血": "nyx",
        "vauban": "vauban",
        "瓦邦": "vauban",
        "valkyr": "valkyr",
        "女武神": "valkyr",
        "瓦尔基里": "valkyr",
        "zephyr": "zephyr",
        "狂啸": "zephyr",
        "oberon": "oberon",
        "奥伯龙": "oberon",
        "mirage": "mirage",
        "幻蝶": "mirage",
        "limbo": "limbo",
        "林波": "limbo",
        "小明": "limbo",
        "李明博": "limbo",
        "chroma": "chroma",
        "龙甲": "chroma",
        "atlas": "atlas",
        "阿特拉斯": "atlas",
        "石甲": "atlas",
        "equinox": "equinox",
        "阴阳双子": "equinox",
        "titania": "titania",
        "泰坦尼亚": "titania",
        "妖精": "titania",
        "octavia": "octavia",
        "奥克塔维亚": "octavia",
        "歌姬": "octavia",
        "harrow": "harrow",
        "哈洛": "harrow",
        "gara": "gara",
        "迦拉": "gara",
        "玻璃": "gara",
        "khora": "khora",
        "科拉": "khora",
        "鞭女": "khora",
        "revenant": "revenant",
        "亡魂": "revenant",
        "garuda": "garuda",
        "迦楼罗": "garuda",
        "baruuk": "baruuk",
        "巴鲁克": "baruuk",
        "hildryn": "hildryn",
        "希尔德琳": "hildryn",
        "gauss": "gauss",
        "高斯": "gauss",
        "grendel": "grendel",
        "格伦德尔": "grendel",
        "protea": "protea",
        "普罗蒂亚": "protea",
        "xaku": "xaku",
        "扎库": "xaku",
        "lavos": "lavos",
        "拉沃斯": "lavos",
        "sevagoth": "sevagoth",
        "塞瓦格斯": "sevagoth",
        "yareli": "yareli",
        "亚蕾丽": "yareli",
        "caliban": "caliban",
        "卡利班": "caliban",
        "gyre": "gyre",
        "吉尔": "gyre",
        "styanax": "styanax",
        "斯提亚纳克斯": "styanax",
        "voruna": "voruna",
        "沃鲁娜": "voruna",
        "citrine": "citrine",
        "西翠恩": "citrine",
        "kullervo": "kullervo",
        "库勒沃": "kullervo",
        "dagath": "dagath",
        "达加斯": "dagath",
        "qorvex": "qorvex",
        "科维克斯": "qorvex",
        "dante": "dante",
        "但丁": "dante",
        "jade": "jade",
        "翡翠": "jade",
    }

    def __init__(
        self,
        *,
        http_timeout_sec: float = 8.0,
        cache_ttl_sec: float = 30 * 24 * 3600,
        ai_timeout_sec: float = 15.0,
    ) -> None:
        self._timeout = aiohttp.ClientTimeout(total=http_timeout_sec)
        self._cache_ttl_sec = cache_ttl_sec
        self._ai_timeout_sec = ai_timeout_sec

        self._plugin_data_dir = (
            Path(get_astrbot_plugin_data_path()) / "astrbot_plugin_warframe_helper"
        )
        self._plugin_data_dir.mkdir(parents=True, exist_ok=True)
        self._user_alias_path = self._plugin_data_dir / "aliases.json"

        self._temp_dir = Path(get_astrbot_temp_path())
        self._temp_dir.mkdir(parents=True, exist_ok=True)
        self._item_cache_path = self._temp_dir / "warframe_market_v2_item_cache.json"

        self._user_aliases: dict[str, str] = {}
        self._item_cache: dict[str, dict[str, Any]] = {}
        self._loaded = False

    async def initialize(self) -> None:
        if self._loaded:
            return
        self._user_aliases = self._load_user_aliases()
        self._item_cache = self._load_item_cache()
        self._loaded = True

    def _normalize_key(self, text: str) -> str:
        text = unicodedata.normalize("NFKC", text)
        text = text.strip().lower()
        text = re.sub(r"\s+", "", text)
        return text

    def _load_user_aliases(self) -> dict[str, str]:
        if not self._user_alias_path.exists():
            return {}
        try:
            data = json.loads(self._user_alias_path.read_text(encoding="utf-8"))
            aliases = data.get("aliases", {})
            if not isinstance(aliases, dict):
                return {}
            normalized: dict[str, str] = {}
            for alias, slug in aliases.items():
                if not isinstance(alias, str) or not isinstance(slug, str):
                    continue
                normalized[self._normalize_key(alias)] = slug.strip()
            return normalized
        except Exception:
            logger.warning("Failed to load user aliases.json; ignoring.")
            return {}

    def _load_item_cache(self) -> dict[str, dict[str, Any]]:
        if not self._item_cache_path.exists():
            return {}
        try:
            data = json.loads(self._item_cache_path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return {}
            return data
        except Exception:
            logger.warning("Failed to load warframe.market item cache; ignoring.")
            return {}

    def _save_item_cache(self) -> None:
        try:
            self._item_cache_path.write_text(
                json.dumps(self._item_cache, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as exc:
            logger.warning(f"Failed to save item cache: {exc!s}")

    def _cache_get_item(self, slug: str) -> MarketItem | None:
        rec = self._item_cache.get(slug)
        if not isinstance(rec, dict):
            return None
        ts = rec.get("ts")
        if not isinstance(ts, (int, float)):
            return None
        if (time.time() - float(ts)) > self._cache_ttl_sec:
            return None
        name = rec.get("name")
        if not isinstance(name, str) or not name:
            return None
        wiki_link = rec.get("wiki_link")
        if wiki_link is not None and not isinstance(wiki_link, str):
            wiki_link = None
        tags = rec.get("tags")
        if isinstance(tags, list) and all(isinstance(t, str) for t in tags):
            tags_tuple: tuple[str, ...] = tuple(tags)
        else:
            tags_tuple = ()
        item_id = rec.get("item_id")
        if item_id is not None and not isinstance(item_id, str):
            item_id = None

        i18n_names: dict[str, str] = {}
        raw_i18n_names = rec.get("i18n_names")
        if isinstance(raw_i18n_names, dict):
            for k, v in raw_i18n_names.items():
                if isinstance(k, str) and isinstance(v, str) and v:
                    i18n_names[k] = v

        thumb = rec.get("thumb")
        if thumb is not None and not isinstance(thumb, str):
            thumb = None
        icon = rec.get("icon")
        if icon is not None and not isinstance(icon, str):
            icon = None
        return MarketItem(
            item_id=item_id,
            slug=slug,
            name=name,
            wiki_link=wiki_link,
            tags=tags_tuple,
            i18n_names=i18n_names,
            thumb=thumb,
            icon=icon,
        )

    def _cache_put_item(self, item: MarketItem) -> None:
        self._item_cache[item.slug] = {
            "ts": time.time(),
            "item_id": item.item_id,
            "name": item.name,
            "wiki_link": item.wiki_link,
            "tags": list(item.tags),
            "i18n_names": dict(item.i18n_names),
            "thumb": item.thumb,
            "icon": item.icon,
        }
        self._save_item_cache()

    def _parse_prime_and_set(self, raw: str) -> tuple[str, bool, bool]:
        text = unicodedata.normalize("NFKC", raw).strip()
        lower = text.lower()

        set_flag = False
        if "set" in lower or "组" in text:
            set_flag = True
            lower = lower.replace("set", "")
            text = re.sub(r"组$", "", text)

        prime_flag = False
        if lower.endswith("prime"):
            prime_flag = True
            lower = lower[: -len("prime")]
        elif lower.endswith("p") and len(lower) >= 2:
            prime_flag = True
            lower = lower[:-1]

        base = lower.strip()
        base = re.sub(r"\s+", " ", base)
        base = base.strip()
        return base, prime_flag, set_flag

    def _alias_to_slug(self, query: str) -> str | None:
        key = self._normalize_key(query)
        if key in self._user_aliases:
            return self._user_aliases[key]

        base, prime_flag, set_flag = self._parse_prime_and_set(query)
        base_key = self._normalize_key(base)

        if base_key in self._BUILTIN_BASE_NICKNAMES:
            slug_base = self._BUILTIN_BASE_NICKNAMES[base_key]
            if prime_flag:
                # warframe.market 上 Prime 的交易通常对应 "... Prime Set" 词条。
                return f"{slug_base}_prime_set"
            if set_flag:
                return f"{slug_base}_set"
            return slug_base

        if re.fullmatch(r"[a-z0-9 _\-']+", base_key):
            slug_base = (
                base_key.replace("'", "")
                .replace("-", "_")
                .replace(" ", "_")
                .replace("__", "_")
                .strip("_")
            )
            if not slug_base:
                return None
            if prime_flag:
                if slug_base.endswith("_prime"):
                    return f"{slug_base}_set"
                return f"{slug_base}_prime_set"
            if set_flag and not slug_base.endswith("_set"):
                return f"{slug_base}_set"
            return slug_base

        return None

    def _build_slug_candidates(self, query: str) -> list[str]:
        slug = self._alias_to_slug(query)
        if slug:
            return [slug]

        base, prime_flag, set_flag = self._parse_prime_and_set(query)
        base_key = self._normalize_key(base)
        if not re.fullmatch(r"[a-z0-9 _\-']+", base_key):
            return []

        slug_base = (
            base_key.replace("'", "")
            .replace("-", "_")
            .replace(" ", "_")
            .replace("__", "_")
            .strip("_")
        )
        if not slug_base:
            return []

        candidates: list[str] = []
        if prime_flag:
            candidates.append(f"{slug_base}_prime_set")
            candidates.append(f"{slug_base}_prime")
        if set_flag:
            candidates.append(f"{slug_base}_set")
        candidates.append(slug_base)

        # 去重但保留顺序
        dedup: list[str] = []
        seen: set[str] = set()
        for c in candidates:
            if c and c not in seen:
                dedup.append(c)
                seen.add(c)
        return dedup

    async def _fetch_item_v2(self, slug: str) -> MarketItem | None:
        cached = self._cache_get_item(slug)
        # 兼容旧缓存：如果 cached 没有 item_id，则强制重新拉取
        if cached and cached.item_id:
            return cached

        url = f"{WARFRAME_MARKET_V2_BASE_URL}/items/{slug}"
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
                        return None
                    payload = await resp.json()
        except Exception as exc:
            logger.warning(f"warframe.market request failed: {exc!s}")
            return None

        try:
            data = payload.get("data")
            if not isinstance(data, dict):
                return None
            item_id = data.get("id")
            if not isinstance(item_id, str) or not item_id:
                item_id = None
            i18n = data.get("i18n", {})
            if not isinstance(i18n, dict):
                return None

            names: dict[str, str] = {}
            wiki_links: dict[str, str] = {}
            thumbs: dict[str, str] = {}
            icons: dict[str, str] = {}
            for locale, block in i18n.items():
                if not isinstance(locale, str) or not isinstance(block, dict):
                    continue
                n = block.get("name")
                if isinstance(n, str) and n:
                    names[locale] = n
                w = block.get("wikiLink")
                if isinstance(w, str) and w:
                    wiki_links[locale] = w
                t = block.get("thumb")
                if isinstance(t, str) and t:
                    thumbs[locale] = t
                ic = block.get("icon")
                if isinstance(ic, str) and ic:
                    icons[locale] = ic

            name = names.get("en")
            if not isinstance(name, str) or not name:
                return None

            wiki_link = wiki_links.get("en")
            tags = data.get("tags")
            if isinstance(tags, list) and all(isinstance(t, str) for t in tags):
                tags_tuple = tuple(tags)
            else:
                tags_tuple = ()

            item = MarketItem(
                item_id=item_id,
                slug=slug,
                name=name,
                wiki_link=wiki_link,
                tags=tags_tuple,
                i18n_names=names,
                thumb=thumbs.get("en"),
                icon=icons.get("en"),
            )
            self._cache_put_item(item)
            return item
        except Exception:
            return None

    async def resolve(self, query: str) -> MarketItem | None:
        """将用户输入（别名/简写）解析为 warframe.market 词条。

        示例：
            - "猴p" -> MarketItem(slug="wukong_prime_set", name="Wukong Prime Set")
        """
        await self.initialize()

        query = query.strip()
        if not query:
            return None

        for slug in self._build_slug_candidates(query):
            item = await self._fetch_item_v2(slug)
            if item:
                return item
        return None

    def _extract_json_object(self, text: str) -> str | None:
        text = text.strip()
        if not text:
            return None
        if text.startswith("{") and text.endswith("}"):
            return text
        # 尝试截取一个 JSON object 子串
        m = re.search(r"\{[\s\S]*\}", text)
        if not m:
            return None
        return m.group(0)

    def _parse_ai_slugs(self, text: str) -> list[str]:
        candidates: list[str] = []
        obj = self._extract_json_object(text)
        if obj:
            try:
                data = json.loads(obj)
                slugs = data.get("slugs")
                if isinstance(slugs, str):
                    slugs = [slugs]
                if isinstance(slugs, list):
                    for s in slugs:
                        if isinstance(s, str):
                            candidates.append(s)
            except Exception:
                pass

        if not candidates:
            # 兜底：从文本里抓取类似 slug 的 token
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

    async def _suggest_slugs_via_ai(
        self,
        context: Any,
        event: Any,
        query: str,
        provider_id: str | None,
    ) -> list[str]:
        if not provider_id:
            try:
                provider_id = await context.get_current_chat_provider_id(
                    event.unified_msg_origin,
                )
            except Exception:
                return []

        system_prompt = (
            "You convert Warframe abbreviations/nicknames into warframe.market v2 item slugs. "
            "Return JSON only."
        )
        prompt = (
            "Given a user query, output up to 5 candidate warframe.market v2 item slugs.\n"
            "Rules:\n"
            '- Output MUST be valid JSON: {"slugs": ["..."]}.\n'
            "- Slug format: lowercase snake_case with underscores.\n"
            "- If the query implies Prime (e.g. ends with 'p' or contains 'prime'), prefer *_prime_set.\n"
            "- If the query is a Warframe nickname in Chinese, map to the corresponding Warframe.\n"
            "Examples:\n"
            '- 猴p -> {"slugs":["wukong_prime_set"]}\n'
            '- wukong p -> {"slugs":["wukong_prime_set"]}\n'
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
            # 某些 Provider 可能不支持 timeout 参数
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
        logger.info(
            f"LLM response for warframe.market slug suggestion: {llm_resp.completion_text or 'empty'}"
        )
        text = (llm_resp.completion_text or "").strip()
        return self._parse_ai_slugs(text)

    async def resolve_with_ai(
        self,
        *,
        context: Any,
        event: Any,
        query: str,
        provider_id: str | None = None,
    ) -> MarketItem | None:
        """先用内置规则解析，失败后用 AstrBot 的 LLM 做兜底。

        注意：LLM 的输出会被 warframe.market v2 API 二次校验，只有真实存在的 slug 才会返回。
        """
        item = await self.resolve(query)
        if item:
            return item

        ai_slugs = await self._suggest_slugs_via_ai(
            context,
            event,
            query,
            provider_id,
        )
        # 同时把确定性的候选也加进来
        candidates = ai_slugs + self._build_slug_candidates(query)
        seen: set[str] = set()
        for slug in candidates:
            if slug in seen:
                continue
            seen.add(slug)
            found = await self._fetch_item_v2(slug)
            if found:
                return found
        return None
