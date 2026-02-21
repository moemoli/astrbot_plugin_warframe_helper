from __future__ import annotations

import time
from dataclasses import dataclass

import aiohttp

from astrbot.api import logger

from ..http_utils import request_kwargs_for_url

WARFRAME_MARKET_V2_BASE_URL = "https://api.warframe.market/v2"
WARFRAME_MARKET_V1_BASE_URL = "https://api.warframe.market/v1"


@dataclass(frozen=True, slots=True)
class MarketOrder:
    order_id: str
    order_type: str  # "sell" | "buy"
    platinum: int
    quantity: int
    visible: bool
    platform: str | None
    status: str | None
    ingame_name: str | None
    avatar: str | None


class WarframeMarketClient:
    def __init__(
        self, *, http_timeout_sec: float = 10.0, cache_ttl_sec: float = 30.0
    ) -> None:
        self._timeout = aiohttp.ClientTimeout(total=http_timeout_sec)
        self._cache_ttl_sec = cache_ttl_sec
        self._orders_cache: dict[str, tuple[float, list[MarketOrder]]] = {}
        self._riven_cache: dict[str, tuple[float, list[RivenAuction]]] = {}

    async def fetch_orders_by_item_id(self, item_id: str) -> list[MarketOrder]:
        """从 warframe.market v2 拉取某个 item_id 的全部订单（包含所有平台）。"""

        if not item_id:
            return []

        now = time.time()
        cached = self._orders_cache.get(item_id)
        if cached and (now - cached[0]) <= self._cache_ttl_sec:
            return cached[1]

        url = f"{WARFRAME_MARKET_V2_BASE_URL}/orders/item/{item_id}"
        headers = {
            "User-Agent": "AstrBot/warframe_helper (+https://github.com/Soulter/AstrBot)",
            "Accept": "application/json",
        }

        try:
            async with aiohttp.ClientSession(
                timeout=self._timeout, trust_env=True
            ) as s:
                req_kw = request_kwargs_for_url(url)
                async with s.get(url, headers=headers, **req_kw) as resp:
                    if resp.status != 200:
                        return []
                    payload = await resp.json()
        except Exception as exc:
            logger.warning(f"warframe.market orders request failed: {exc!s}")
            return []

        data = payload.get("data")
        if not isinstance(data, list):
            return []

        orders: list[MarketOrder] = []
        for row in data:
            if not isinstance(row, dict):
                continue
            order_id = row.get("id")
            order_type = row.get("type")
            platinum = row.get("platinum")
            quantity = row.get("quantity")
            visible = row.get("visible")
            if not isinstance(order_id, str) or not isinstance(order_type, str):
                continue
            if not isinstance(platinum, int) or not isinstance(quantity, int):
                continue
            if not isinstance(visible, bool):
                visible = True

            user = row.get("user")
            platform = None
            status = None
            ingame_name = None
            avatar = None
            if isinstance(user, dict):
                platform = (
                    user.get("platform")
                    if isinstance(user.get("platform"), str)
                    else None
                )
                status = (
                    user.get("status") if isinstance(user.get("status"), str) else None
                )
                ingame_name = (
                    user.get("ingameName")
                    if isinstance(user.get("ingameName"), str)
                    else None
                )
                avatar = (
                    user.get("avatar") if isinstance(user.get("avatar"), str) else None
                )

            orders.append(
                MarketOrder(
                    order_id=order_id,
                    order_type=order_type,
                    platinum=platinum,
                    quantity=quantity,
                    visible=visible,
                    platform=platform,
                    status=status,
                    ingame_name=ingame_name,
                    avatar=avatar,
                ),
            )

        self._orders_cache[item_id] = (now, orders)
        return orders

    async def fetch_riven_auctions(
        self,
        weapon_url_name: str,
        *,
        platform: str = "pc",
        positive_stats: list[str] | None = None,
        negative_stats: list[str] | None = None,
        mastery_rank_min: int | None = None,
        polarity: str | None = None,
        buyout_policy: str = "direct",
    ) -> list[RivenAuction]:
        """从 warframe.market v1 拉取紫卡拍卖列表。"""

        if not weapon_url_name:
            return []

        positive_stats = [
            s for s in (positive_stats or []) if isinstance(s, str) and s.strip()
        ]
        negative_stats = [
            s for s in (negative_stats or []) if isinstance(s, str) and s.strip()
        ]

        cache_key = "|".join(
            [
                "riven",
                weapon_url_name.strip().lower(),
                (platform or "").strip().lower(),
                ",".join(sorted([s.strip().lower() for s in positive_stats])),
                ",".join(sorted([s.strip().lower() for s in negative_stats])),
                str(int(mastery_rank_min)) if isinstance(mastery_rank_min, int) else "",
                (polarity or "").strip().lower(),
                (buyout_policy or "").strip().lower(),
            ]
        )

        now = time.time()
        cached = self._riven_cache.get(cache_key)
        if cached and (now - cached[0]) <= self._cache_ttl_sec:
            return cached[1]

        params: list[tuple[str, str]] = [
            ("type", "riven"),
            ("weapon_url_name", weapon_url_name.strip().lower()),
        ]
        if platform:
            params.append(("platform", str(platform).strip().lower()))
        if positive_stats:
            params.append(
                (
                    "positive_stats",
                    ",".join([s.strip().lower() for s in positive_stats]),
                )
            )
        if negative_stats:
            params.append(
                (
                    "negative_stats",
                    ",".join([s.strip().lower() for s in negative_stats]),
                )
            )
        if isinstance(mastery_rank_min, int) and mastery_rank_min > 0:
            params.append(("mastery_rank_min", str(int(mastery_rank_min))))
        if polarity:
            params.append(("polarity", str(polarity).strip().lower()))
        if buyout_policy:
            params.append(("buyout_policy", str(buyout_policy).strip().lower()))

        query = "&".join(
            [f"{k}={aiohttp.helpers.quote(v, safe='')}" for k, v in params]
        )
        url = f"{WARFRAME_MARKET_V1_BASE_URL}/auctions/search?{query}"
        headers = {
            "User-Agent": "AstrBot/warframe_helper (+https://github.com/Soulter/AstrBot)",
            "Accept": "application/json",
        }

        try:
            async with aiohttp.ClientSession(
                timeout=self._timeout, trust_env=True
            ) as s:
                req_kw = request_kwargs_for_url(url)
                async with s.get(url, headers=headers, **req_kw) as resp:
                    if resp.status != 200:
                        return []
                    payload = await resp.json()
        except Exception as exc:
            logger.warning(f"warframe.market riven auctions request failed: {exc!s}")
            return []

        pl = payload.get("payload")
        if not isinstance(pl, dict):
            return []
        auctions = pl.get("auctions")
        if not isinstance(auctions, list):
            return []

        out: list[RivenAuction] = []
        for row in auctions:
            if not isinstance(row, dict):
                continue

            auction_id = row.get("id")
            buyout_price = row.get("buyout_price")
            starting_price = row.get("starting_price")
            visible = row.get("visible")
            closed = row.get("closed")
            is_direct_sell = row.get("is_direct_sell")
            platform_row = (
                row.get("platform") if isinstance(row.get("platform"), str) else None
            )

            if not isinstance(auction_id, str):
                continue
            if not isinstance(buyout_price, int):
                continue
            if not isinstance(visible, bool):
                visible = True
            if not isinstance(closed, bool):
                closed = False
            if not isinstance(is_direct_sell, bool):
                is_direct_sell = False

            if starting_price is not None and not isinstance(starting_price, int):
                starting_price = None

            owner = row.get("owner")
            owner_name = None
            owner_status = None
            owner_avatar = None
            if isinstance(owner, dict):
                owner_name = (
                    owner.get("ingame_name")
                    if isinstance(owner.get("ingame_name"), str)
                    else None
                )
                owner_status = (
                    owner.get("status")
                    if isinstance(owner.get("status"), str)
                    else None
                )
                owner_avatar = (
                    owner.get("avatar")
                    if isinstance(owner.get("avatar"), str)
                    else None
                )

            item = row.get("item")
            if not isinstance(item, dict):
                continue
            weapon = item.get("weapon_url_name")
            if not isinstance(weapon, str):
                continue

            riven_name = item.get("name") if isinstance(item.get("name"), str) else None
            mod_rank = (
                item.get("mod_rank") if isinstance(item.get("mod_rank"), int) else None
            )
            mastery_level = (
                item.get("mastery_level")
                if isinstance(item.get("mastery_level"), int)
                else None
            )
            polarity_val = (
                item.get("polarity") if isinstance(item.get("polarity"), str) else None
            )
            re_rolls = (
                item.get("re_rolls") if isinstance(item.get("re_rolls"), int) else None
            )

            attrs_raw = item.get("attributes")
            attrs: list[RivenAttribute] = []
            if isinstance(attrs_raw, list):
                for a in attrs_raw:
                    if not isinstance(a, dict):
                        continue
                    url_name = a.get("url_name")
                    value = a.get("value")
                    positive = a.get("positive")
                    if not isinstance(url_name, str):
                        continue
                    if not isinstance(value, (int, float)):
                        continue
                    if not isinstance(positive, bool):
                        continue
                    attrs.append(
                        RivenAttribute(
                            url_name=url_name,
                            value=float(value),
                            positive=positive,
                        )
                    )

            out.append(
                RivenAuction(
                    auction_id=auction_id,
                    buyout_price=buyout_price,
                    starting_price=starting_price,
                    platform=platform_row,
                    visible=visible,
                    closed=closed,
                    is_direct_sell=is_direct_sell,
                    owner_name=owner_name,
                    owner_status=owner_status,
                    owner_avatar=owner_avatar,
                    weapon_url_name=weapon.strip().lower(),
                    riven_name=riven_name,
                    mod_rank=mod_rank,
                    mastery_level=mastery_level,
                    polarity=polarity_val,
                    re_rolls=re_rolls,
                    attributes=tuple(attrs),
                )
            )

        self._riven_cache[cache_key] = (now, out)
        return out


@dataclass(frozen=True, slots=True)
class RivenAttribute:
    url_name: str
    value: float
    positive: bool


@dataclass(frozen=True, slots=True)
class RivenAuction:
    auction_id: str
    buyout_price: int
    starting_price: int | None
    platform: str | None
    visible: bool
    closed: bool
    is_direct_sell: bool

    owner_name: str | None
    owner_status: str | None
    owner_avatar: str | None

    weapon_url_name: str
    riven_name: str | None
    mod_rank: int | None
    mastery_level: int | None
    polarity: str | None
    re_rolls: int | None
    attributes: tuple[RivenAttribute, ...]
