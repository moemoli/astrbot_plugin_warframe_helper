from __future__ import annotations

import asyncio
from dataclasses import dataclass

from ..clients.market_client import MarketOrder
from ..constants import market_status_to_cn, normalize_market_status
from ..http_utils import fetch_bytes
from ..mappers.term_mapping import MarketItem
from .html_snapshot import image_bytes_to_data_uri, render_html_to_png_file, svg_text_to_data_uri
from .template_loader import load_html_template

WARFRAME_MARKET_ASSETS_BASE_URL = "https://warframe.market/static/assets/"


@dataclass(frozen=True, slots=True)
class RenderedImage:
    path: str


def _asset_url(asset_path: str) -> str:
    return WARFRAME_MARKET_ASSETS_BASE_URL + asset_path.lstrip("/")


async def _download_bytes(url: str, *, timeout_sec: float = 10.0) -> bytes | None:
    headers = {
        "User-Agent": "AstrBot/warframe_helper (+https://github.com/Soulter/AstrBot)",
        "Accept": "image/*,*/*;q=0.8",
    }
    return await fetch_bytes(url, timeout_sec=timeout_sec, headers=headers)


def _status_class(status: str | None) -> str:
    s = normalize_market_status(status)
    if s in {"ingame", "in_game", "in-game", "in game"}:
        return "ingame"
    if s == "online":
        return "online"
    return "offline"


def _placeholder_avatar_data_uri() -> str:
    return svg_text_to_data_uri(
        """
        <svg xmlns='http://www.w3.org/2000/svg' width='64' height='64' viewBox='0 0 64 64'>
          <circle cx='32' cy='32' r='32' fill='#e2e8f0'/>
          <text x='32' y='40' text-anchor='middle' font-size='28' fill='#64748b' font-family='Arial, sans-serif'>?</text>
        </svg>
        """.strip()
    )


def _build_wm_html(
    *,
    title: str,
    item_img_uri: str | None,
    rows: list[dict[str, str]],
) -> str:
    item_background_style = (
        f"background-image: linear-gradient(135deg, rgba(224,231,255,0.86), rgba(219,234,254,0.86)), url('{item_img_uri}');"
        if item_img_uri
        else "background-image: linear-gradient(135deg, rgba(224,231,255,0.92), rgba(219,234,254,0.92));"
    )

    context: dict[str, object] = {
        "page": {
            "title": title,
            "item_img_uri": item_img_uri or "",
            "item_background_style": item_background_style,
            "rows": rows,
        }
    }

    html = load_html_template(filename="wm.html", context=context)
    if html:
        return html

    return "<html><body><pre>wm template not found</pre></body></html>"


async def render_wm_orders_image_to_file(
    *,
    item: MarketItem,
    orders: list[MarketOrder],
    platform: str,
    action_cn: str,
    language: str,
    limit: int,
) -> RenderedImage | None:
    if not orders:
        return None

    limit = min(max(int(limit), 1), 20)

    item_name = item.get_localized_name(language)
    title = f"{item_name}（{platform}）{action_cn}"

    item_uri: str | None = None
    item_asset = item.thumb or item.icon
    if item_asset:
        item_bytes = await _download_bytes(_asset_url(item_asset), timeout_sec=10.0)
        item_uri = image_bytes_to_data_uri(item_bytes, filename=item_asset)

    selected = orders[:limit]
    avatar_urls: list[str | None] = [
        (_asset_url(order.avatar) if order.avatar else None) for order in selected
    ]

    async def dl(url: str | None) -> bytes | None:
        if not url:
            return None
        return await _download_bytes(url, timeout_sec=8.0)

    avatar_bytes_list = await asyncio.gather(*[dl(u) for u in avatar_urls])
    placeholder = _placeholder_avatar_data_uri()

    rows: list[dict[str, str]] = []
    for order, avatar_bytes in zip(selected, avatar_bytes_list, strict=False):
        avatar_uri = image_bytes_to_data_uri(avatar_bytes) or placeholder
        status = normalize_market_status(order.status)
        qty = int(order.quantity)

        rows.append(
            {
                "avatar": avatar_uri,
                "name": (order.ingame_name or "unknown").strip() or "unknown",
                "status_text": market_status_to_cn(status),
                "status_class": _status_class(status),
                "price_text": f"{int(order.platinum)}p",
                "qty_text": f"x{qty}" if qty > 1 else "",
            }
        )

    html = _build_wm_html(title=title, item_img_uri=item_uri, rows=rows)
    path = await render_html_to_png_file(
        html=html,
        width=920,
        prefix=f"wm_{item.slug}",
        min_height=680,
    )
    if not path:
        return None

    return RenderedImage(path=path)
