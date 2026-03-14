from __future__ import annotations

import asyncio
from dataclasses import dataclass

from ..clients.market_client import RivenAttribute, RivenAuction
from ..constants import RIVEN_STAT_CN
from ..constants import market_status_to_cn, normalize_market_status
from ..http_utils import fetch_bytes
from ..mappers.riven_mapping import RivenWeapon
from .html_snapshot import (
    image_bytes_to_data_uri,
    render_html_to_png_file,
    svg_text_to_data_uri,
)
from .template_loader import load_html_template

WARFRAME_MARKET_ASSETS_BASE_URL = "https://warframe.market/static/assets/"


_STAT_CN: dict[str, str] = {
    **RIVEN_STAT_CN,
    "attack_speed": "攻速",
    "fire_rate": "射速",
    "fire_rate_/_attack_speed": "射速/攻速",
    "status_chance": "触发几率",
    "status_duration": "触发持续时间",
    "punch_through": "穿透",
    "projectile_speed": "弹道速度",
    "flight_speed": "弹道速度",
    "recoil": "后坐力",
    "accuracy": "精准度",
    "range": "范围",
    "combo_duration": "连击持续时间",
    "initial_combo": "初始连击",
    "finisher_damage": "处决伤害",
    "melee_damage": "近战伤害",
    "slide_attack_critical_chance": "滑攻击暴击率",
    "critical_chance_on_slide_attack": "滑攻击暴击率",
    "channeling_efficiency": "引导效率",
    "channeling_damage": "引导伤害",
}


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


def _fmt_attr_parts(attr: RivenAttribute) -> tuple[str, str]:
    label = _STAT_CN.get(attr.url_name, attr.url_name)
    value = float(attr.value)
    if abs(value) < 10:
        return label, f"x{abs(value):.2f}"

    sign = "+" if attr.positive else "-"
    return label, f"{sign}{abs(value):.1f}%"


def _fmt_attr_line(attrs: list[RivenAttribute], *, empty_text: str) -> str:
    if not attrs:
        return empty_text
    parts = [_fmt_attr_parts(a) for a in attrs]
    return "，".join(f"{k} {v}" for k, v in parts)


def _fmt_polarity(p: str | None) -> str:
    m = {
        "madurai": "V",
        "vazarin": "D",
        "naramon": "-",
        "zenurik": "R",
    }
    p2 = (p or "").strip().lower()
    if not p2:
        return "-"
    return m.get(p2, p2)


def _placeholder_avatar_data_uri() -> str:
    return svg_text_to_data_uri(
        """
        <svg xmlns='http://www.w3.org/2000/svg' width='64' height='64' viewBox='0 0 64 64'>
          <circle cx='32' cy='32' r='32' fill='#e2e8f0'/>
          <text x='32' y='40' text-anchor='middle' font-size='28' fill='#64748b' font-family='Arial, sans-serif'>?</text>
        </svg>
        """.strip()
    )


def _build_wmr_html(
    *,
    title: str,
    summary: str,
    weapon_img_uri: str | None,
    rows: list[dict[str, str]],
) -> str:
    weapon_background_style = (
        f"background-image: linear-gradient(135deg, rgba(224,231,255,0.88), rgba(219,234,254,0.88)), url('{weapon_img_uri}');"
        if weapon_img_uri
        else "background-image: linear-gradient(135deg, rgba(224,231,255,0.92), rgba(219,234,254,0.92));"
    )

    context: dict[str, object] = {
        "page": {
            "title": title,
            "summary": summary,
            "weapon_img_uri": weapon_img_uri or "",
            "weapon_background_style": weapon_background_style,
            "rows": rows,
        }
    }

    html = load_html_template(filename="wmr.html", context=context)
    if html:
        return html

    return "<html><body><pre>wmr template not found</pre></body></html>"


async def render_wmr_auctions_image_to_file(
    *,
    weapon: RivenWeapon,
    weapon_display_name: str,
    auctions: list[RivenAuction],
    platform: str,
    summary: str,
    limit: int,
) -> RenderedImage | None:
    if not auctions:
        return None

    limit = min(max(int(limit), 1), 20)
    selected = auctions[:limit]

    name = (weapon_display_name or weapon.item_name or "").strip() or weapon.item_name
    title = f"紫卡 {name}（{platform}） 前{limit}"

    weapon_uri: str | None = None
    weapon_asset = weapon.thumb or weapon.icon
    if weapon_asset:
        w_bytes = await _download_bytes(_asset_url(weapon_asset))
        weapon_uri = image_bytes_to_data_uri(w_bytes, filename=weapon_asset)

    avatar_urls: list[str | None] = [
        (_asset_url(a.owner_avatar) if a.owner_avatar else None) for a in selected
    ]

    async def fetch_avatar(url: str | None) -> bytes | None:
        if not url:
            return None
        return await _download_bytes(url)

    avatar_bytes_list = await asyncio.gather(*[fetch_avatar(u) for u in avatar_urls])
    placeholder = _placeholder_avatar_data_uri()

    rows: list[dict[str, str]] = []
    for auction, avatar_bytes in zip(selected, avatar_bytes_list, strict=False):
        owner_name = (auction.owner_name or "unknown").strip() or "unknown"
        status = normalize_market_status(auction.owner_status)

        pos = [x for x in auction.attributes if x.positive]
        neg = [x for x in auction.attributes if not x.positive]

        rows.append(
            {
                "avatar": image_bytes_to_data_uri(avatar_bytes) or placeholder,
                "name": owner_name,
                "status_text": market_status_to_cn(status),
                "status_class": _status_class(status),
                "mr_text": str(int(auction.mastery_level or 0)),
                "polarity_text": _fmt_polarity(auction.polarity),
                "rr_text": str(int(auction.re_rolls or 0)),
                "pos_text": _fmt_attr_line(pos, empty_text="(无正面词条)"),
                "neg_text": _fmt_attr_line(neg, empty_text="无负面词条"),
                "price_text": f"{int(auction.buyout_price or 0)}p",
            }
        )

    html = _build_wmr_html(
        title=title,
        summary=summary,
        weapon_img_uri=weapon_uri,
        rows=rows,
    )

    path = await render_html_to_png_file(
        html=html,
        width=980,
        prefix="wmr",
        min_height=760,
    )
    if not path:
        return None

    return RenderedImage(path=path)
