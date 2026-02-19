from __future__ import annotations

import time
from dataclasses import dataclass

import aiohttp

from astrbot.api import logger


WARFRAME_MARKET_V2_BASE_URL = "https://api.warframe.market/v2"


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
    def __init__(self, *, http_timeout_sec: float = 10.0, cache_ttl_sec: float = 30.0) -> None:
        self._timeout = aiohttp.ClientTimeout(total=http_timeout_sec)
        self._cache_ttl_sec = cache_ttl_sec
        self._cache: dict[str, tuple[float, list[MarketOrder]]] = {}

    async def fetch_orders_by_item_id(self, item_id: str) -> list[MarketOrder]:
        """从 warframe.market v2 拉取某个 item_id 的全部订单（包含所有平台）。"""

        if not item_id:
            return []

        now = time.time()
        cached = self._cache.get(item_id)
        if cached and (now - cached[0]) <= self._cache_ttl_sec:
            return cached[1]

        url = f"{WARFRAME_MARKET_V2_BASE_URL}/orders/item/{item_id}"
        headers = {
            "User-Agent": "AstrBot/warframe_helper (+https://github.com/Soulter/AstrBot)",
            "Accept": "application/json",
        }

        try:
            async with aiohttp.ClientSession(timeout=self._timeout, trust_env=True) as s:
                async with s.get(url, headers=headers) as resp:
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
                platform = user.get("platform") if isinstance(user.get("platform"), str) else None
                status = user.get("status") if isinstance(user.get("status"), str) else None
                ingame_name = (
                    user.get("ingameName") if isinstance(user.get("ingameName"), str) else None
                )
                avatar = user.get("avatar") if isinstance(user.get("avatar"), str) else None

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

        self._cache[item_id] = (now, orders)
        return orders
