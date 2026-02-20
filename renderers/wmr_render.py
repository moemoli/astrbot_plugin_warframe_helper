from __future__ import annotations

import asyncio
import io
import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path

import aiohttp
from PIL import Image, ImageDraw, ImageFont

from astrbot.api import logger
from astrbot.core.utils.astrbot_path import get_astrbot_temp_path

from ..clients.market_client import RivenAttribute, RivenAuction
from ..mappers.riven_mapping import RivenWeapon

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
    timeout = aiohttp.ClientTimeout(total=timeout_sec)
    try:
        async with aiohttp.ClientSession(timeout=timeout, trust_env=True) as s:
            async with s.get(url, headers=headers) as resp:
                if resp.status != 200:
                    return None
                return await resp.read()
    except Exception as exc:
        logger.debug(f"download failed: {url}: {exc!s}")
        return None


def _load_font(
    size: int, *, weight: str = "regular"
) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates: list[str] = []

    fonts_dir = Path(__file__).resolve().parent.parent / "fonts"
    if fonts_dir.exists():
        if weight.lower() in {"medium", "bold", "semibold"}:
            candidates.append(str(fonts_dir / "NotoSansHans-Medium.otf"))
        candidates.append(str(fonts_dir / "NotoSansHans-Regular.otf"))

    windir = os.environ.get("WINDIR") or os.environ.get("SystemRoot")
    if windir:
        candidates.extend(
            [
                os.path.join(windir, "Fonts", "msyh.ttc"),
                os.path.join(windir, "Fonts", "msyhl.ttc"),
                os.path.join(windir, "Fonts", "simhei.ttf"),
                os.path.join(windir, "Fonts", "simsun.ttc"),
            ]
        )

    candidates.extend(
        [
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/System/Library/Fonts/PingFang.ttc",
        ]
    )

    for p in candidates:
        try:
            if p and os.path.exists(p):
                return ImageFont.truetype(p, size=size)
        except Exception:
            continue

    return ImageFont.load_default()


def _open_image_rgba(
    image_bytes: bytes, *, size: tuple[int, int]
) -> Image.Image | None:
    try:
        img = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
        img = img.resize(size, Image.Resampling.LANCZOS)
        return img
    except Exception:
        return None


def _circle_mask(size: int) -> Image.Image:
    mask = Image.new("L", (size, size), 0)
    d = ImageDraw.Draw(mask)
    d.ellipse((0, 0, size - 1, size - 1), fill=255)
    return mask


def _circle_avatar(img: Image.Image, *, size: int) -> Image.Image:
    if img.mode != "RGBA":
        img = img.convert("RGBA")
    img = img.resize((size, size), Image.Resampling.LANCZOS)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(img, (0, 0), _circle_mask(size))
    return out


def _placeholder_avatar(*, size: int = 48) -> Image.Image:
    img = Image.new("RGBA", (size, size), (230, 230, 230, 255))
    d = ImageDraw.Draw(img)
    font = _load_font(max(12, size // 2), weight="medium")
    text = "?"
    bbox = d.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    d.text(
        ((size - tw) / 2, (size - th) / 2 - 2),
        text,
        fill=(120, 120, 120, 255),
        font=font,
    )
    return _circle_avatar(img, size=size)


def _status_dot_color(status: str | None) -> tuple[int, int, int, int]:
    s = (status or "").strip().lower()
    if s in {"ingame", "in_game", "in-game", "in game"}:
        return (34, 197, 94, 255)
    if s in {"online"}:
        return (234, 179, 8, 255)
    return (156, 163, 175, 255)


def _row_accent_color(status: str | None) -> tuple[int, int, int, int]:
    s = (status or "").strip().lower()
    if s in {"ingame", "in_game", "in-game", "in game"}:
        return (16, 185, 129, 255)
    if s in {"online"}:
        return (245, 158, 11, 255)
    return (209, 213, 219, 255)


def _linear_gradient(
    *,
    size: tuple[int, int],
    left: tuple[int, int, int, int],
    right: tuple[int, int, int, int],
) -> Image.Image:
    w, h = size
    img = Image.new("RGBA", (w, h), left)
    d = ImageDraw.Draw(img)
    for x in range(w):
        t = x / max(1, (w - 1))
        c = (
            int(left[0] * (1 - t) + right[0] * t),
            int(left[1] * (1 - t) + right[1] * t),
            int(left[2] * (1 - t) + right[2] * t),
            int(left[3] * (1 - t) + right[3] * t),
        )
        d.line((x, 0, x, h), fill=c)
    return img


_STAT_ZH: dict[str, str] = {
    "critical_chance": "暴击率",
    "critical_damage": "暴击伤害",
    "multishot": "多重射击",
    "base_damage_/_melee_damage": "伤害",
    "attack_speed": "攻速",
    "fire_rate_/_attack_speed": "射速/攻速",
    "toxin_damage": "毒",
    "cold_damage": "冰",
    "heat_damage": "火",
    "electric_damage": "电",
    "puncture_damage": "穿刺",
    "slash_damage": "切割",
    "impact_damage": "冲击",
    "ammo_maximum": "弹药上限",
    "magazine_capacity": "弹匣容量",
    "reload_speed": "装填速度",
    "zoom": "变焦",
    "damage_vs_grineer": "对Grineer伤害",
    "damage_vs_corpus": "对Corpus伤害",
    "damage_vs_infested": "对Infested伤害",
    "projectile_speed": "弹道速度",
    "range": "范围",
}


def _fmt_attr(a: RivenAttribute) -> str:
    label = _STAT_ZH.get(a.url_name, a.url_name)
    sign = "+" if a.positive else "-"

    # 部分字段 value 是倍率（0.74），部分是百分比（146.2）。这里用启发式：
    # - |v| < 10 => 认为是倍率，显示为 x?.??
    # - 其他 => 认为是百分比，显示为 ?.?%
    v = float(a.value)
    if abs(v) < 10:
        vv = abs(v)
        val = f"x{vv:.2f}"
    else:
        vv = abs(v)
        val = f"{vv:.1f}%"
    return f"{sign}{label}{val}"


def _fmt_polarity(p: str | None) -> str:
    p2 = (p or "").strip().lower()
    if p2 == "madurai":
        return "V"
    if p2 == "vazarin":
        return "D"
    if p2 == "naramon":
        return "-"
    if p2 == "zenurik":
        return "R"
    return p or "?"


def _render_image(
    *,
    title: str,
    weapon_img: Image.Image | None,
    rows: list[dict],
) -> bytes:
    margin = 24
    header_h = 132
    row_h = 104
    avatar_size = 52
    row_gap = 10

    width = 980
    height = margin * 2 + header_h + len(rows) * row_h + max(0, len(rows) - 1) * row_gap

    bg = Image.new("RGBA", (width, height), (248, 250, 252, 255))
    d = ImageDraw.Draw(bg)

    font_title = _load_font(34, weight="medium")
    font_name = _load_font(24, weight="medium")
    font_meta = _load_font(20, weight="regular")
    font_attr = _load_font(18, weight="regular")

    header_grad = _linear_gradient(
        size=(width, margin + header_h + 8),
        left=(239, 246, 255, 255),
        right=(245, 243, 255, 255),
    )
    bg.alpha_composite(header_grad, (0, 0))

    x = margin
    y = margin
    if weapon_img is not None:
        bg.alpha_composite(_circle_avatar(weapon_img, size=96), (x, y + 10))
        x += 96 + 16

    max_title_w = width - margin - x
    title_text = title
    while True:
        bbox = d.textbbox((0, 0), title_text, font=font_title)
        if (bbox[2] - bbox[0]) <= max_title_w or len(title_text) <= 6:
            break
        title_text = title_text[:-1]
    if title_text != title:
        title_text = title_text.rstrip() + "…"

    d.text((x, y + 18), title_text, fill=(15, 23, 42, 255), font=font_title)

    d.line(
        (margin, margin + header_h, width - margin, margin + header_h),
        fill=(226, 232, 240, 255),
        width=2,
    )

    start_y = margin + header_h + 18
    row_x0 = margin
    row_x1 = width - margin
    radius = 14

    for i, r in enumerate(rows):
        row_y = start_y + i * (row_h + row_gap)
        status = r.get("status")
        accent = _row_accent_color(status)

        d.rounded_rectangle(
            (row_x0, row_y, row_x1, row_y + row_h),
            radius=radius,
            fill=(255, 255, 255, 255),
            outline=(226, 232, 240, 255),
            width=1,
        )
        d.rounded_rectangle(
            (row_x0, row_y, row_x0 + 8, row_y + row_h),
            radius=radius,
            fill=accent,
        )

        avatar_img: Image.Image = r.get("avatar") or _placeholder_avatar(
            size=avatar_size
        )
        avatar_img = _circle_avatar(avatar_img, size=avatar_size)
        bg.alpha_composite(
            avatar_img, (row_x0 + 18, row_y + (row_h - avatar_size) // 2)
        )

        name = str(r.get("name") or "unknown")
        status_dot = _status_dot_color(status)
        name_x = row_x0 + 18 + avatar_size + 14
        name_y = row_y + 18

        d.text((name_x, name_y), name, fill=(15, 23, 42, 255), font=font_name)
        bbox = d.textbbox((0, 0), name, font=font_name)
        dot_x = name_x + (bbox[2] - bbox[0]) + 10
        dot_y = name_y + 10
        d.ellipse((dot_x, dot_y, dot_x + 10, dot_y + 10), fill=status_dot)

        meta = r.get("meta") or ""
        if meta:
            d.text((name_x, row_y + 50), meta, fill=(71, 85, 105, 255), font=font_meta)

        attrs = r.get("attrs") or ""
        if attrs:
            # 简单换行：按逗号断开
            parts = [p.strip() for p in re.split(r"[,，]", str(attrs)) if p.strip()]
            line1 = "，".join(parts[:2])
            line2 = "，".join(parts[2:4])
            d.text((name_x, row_y + 74), line1, fill=(51, 65, 85, 255), font=font_attr)
            if line2:
                d.text(
                    (name_x + 2, row_y + 94),
                    line2,
                    fill=(51, 65, 85, 255),
                    font=font_attr,
                )

        price = int(r.get("price") or 0)
        price_text = f"{price}p"
        bbox_p = d.textbbox((0, 0), price_text, font=font_title)
        pw = bbox_p[2] - bbox_p[0]
        d.text(
            (row_x1 - 18 - pw, row_y + 30),
            price_text,
            fill=(37, 99, 235, 255),
            font=font_title,
        )

    out = io.BytesIO()
    bg.convert("RGB").save(out, format="PNG", optimize=True)
    return out.getvalue()


async def render_wmr_auctions_image_to_file(
    *,
    weapon: RivenWeapon,
    auctions: list[RivenAuction],
    platform: str,
    summary: str,
    limit: int,
) -> RenderedImage | None:
    """渲染 /wmr 结果图并落盘到 AstrBot temp 目录。"""

    temp_dir = Path(get_astrbot_temp_path())
    temp_dir.mkdir(parents=True, exist_ok=True)

    # 武器图
    weapon_img: Image.Image | None = None
    weapon_asset = weapon.thumb or weapon.icon
    if weapon_asset:
        b = await _download_bytes(_asset_url(weapon_asset))
        if b:
            weapon_img = _open_image_rgba(b, size=(96, 96))

    # 并发下载头像
    avatar_urls: list[str] = []
    for a in auctions:
        if a.owner_avatar:
            avatar_urls.append(_asset_url(a.owner_avatar))
        else:
            avatar_urls.append("")

    async def fetch_avatar(url: str) -> Image.Image | None:
        if not url:
            return None
        b = await _download_bytes(url)
        if not b:
            return None
        return _open_image_rgba(b, size=(64, 64))

    avatars: list[Image.Image | None] = []
    try:
        avatars = await asyncio.gather(*[fetch_avatar(u) for u in avatar_urls])
    except Exception:
        avatars = [None for _ in avatar_urls]

    rows: list[dict] = []
    for i, a in enumerate(auctions):
        name = (a.owner_name or "unknown").strip() or "unknown"
        status = a.owner_status
        pol = _fmt_polarity(a.polarity)
        mr = a.mastery_level
        rr = a.re_rolls
        meta_parts: list[str] = []
        if mr is not None:
            meta_parts.append(f"MR{mr}")
        if pol:
            meta_parts.append(f"极性{pol}")
        if rr is not None:
            meta_parts.append(f"洗练{rr}")

        pos = [x for x in a.attributes if x.positive]
        neg = [x for x in a.attributes if not x.positive]
        attr_text = "，".join([_fmt_attr(x) for x in (pos + neg)][:4])

        rows.append(
            {
                "price": a.buyout_price,
                "name": name,
                "status": status,
                "meta": "  ".join(meta_parts),
                "attrs": attr_text,
                "avatar": avatars[i] if i < len(avatars) else None,
            }
        )

    title = f"紫卡 {weapon.item_name}（{platform}） {summary} 前{limit}"
    img_bytes = _render_image(title=title, weapon_img=weapon_img, rows=rows)

    file_path = temp_dir / f"wmr_{uuid.uuid4().hex}.png"
    try:
        file_path.write_bytes(img_bytes)
        return RenderedImage(path=str(file_path))
    except Exception as exc:
        logger.debug(f"Failed to write wmr image: {exc!s}")
        return None
