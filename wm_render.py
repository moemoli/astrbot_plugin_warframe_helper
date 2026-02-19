from __future__ import annotations

import asyncio
import io
import os
import uuid
from dataclasses import dataclass
from pathlib import Path

import aiohttp
from PIL import Image, ImageDraw, ImageFont

from astrbot.api import logger
from astrbot.core.utils.astrbot_path import get_astrbot_temp_path

from .market_client import MarketOrder
from .term_mapping import MarketItem


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


def _load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates: list[str] = []

    windir = os.environ.get("WINDIR") or os.environ.get("SystemRoot")
    if windir:
        candidates.extend(
            [
                os.path.join(windir, "Fonts", "msyh.ttc"),
                os.path.join(windir, "Fonts", "msyhl.ttc"),
                os.path.join(windir, "Fonts", "simhei.ttf"),
                os.path.join(windir, "Fonts", "simsun.ttc"),
            ],
        )

    # 常见 Linux 字体
    candidates.extend(
        [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/System/Library/Fonts/PingFang.ttc",
        ],
    )

    for p in candidates:
        try:
            if p and os.path.exists(p):
                return ImageFont.truetype(p, size=size)
        except Exception:
            continue

    return ImageFont.load_default()


def _open_image_rgba(image_bytes: bytes, *, size: tuple[int, int]) -> Image.Image | None:
    try:
        img = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
        img = img.resize(size, Image.Resampling.LANCZOS)
        return img
    except Exception:
        return None


def _placeholder_avatar(*, size: int = 48) -> Image.Image:
    img = Image.new("RGBA", (size, size), (230, 230, 230, 255))
    d = ImageDraw.Draw(img)
    font = _load_font(max(12, size // 2))
    text = "?"
    bbox = d.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    d.text(((size - tw) / 2, (size - th) / 2 - 2), text, fill=(120, 120, 120, 255), font=font)
    return img


def _render_image(
    *,
    title: str,
    item_img: Image.Image | None,
    rows: list[tuple[int, str, str, Image.Image]],
) -> bytes:
    # 简单的卡片式布局
    margin = 24
    header_h = 120
    row_h = 64
    avatar_size = 48

    width = 900
    height = margin * 2 + header_h + len(rows) * row_h

    bg = Image.new("RGBA", (width, height), (255, 255, 255, 255))
    d = ImageDraw.Draw(bg)

    font_title = _load_font(34)
    font_small = _load_font(22)

    # Header
    x = margin
    y = margin
    if item_img is not None:
        bg.alpha_composite(item_img, (x, y + 8))
        x += item_img.size[0] + 16

    # 标题换行截断（尽量不溢出）
    max_title_w = width - margin - x
    title_text = title
    while True:
        bbox = d.textbbox((0, 0), title_text, font=font_title)
        if (bbox[2] - bbox[0]) <= max_title_w or len(title_text) <= 6:
            break
        title_text = title_text[:-1]
    if title_text != title:
        title_text = title_text.rstrip() + "…"

    d.text((x, y + 18), title_text, fill=(0, 0, 0, 255), font=font_title)

    # 分割线
    d.line((margin, margin + header_h, width - margin, margin + header_h), fill=(220, 220, 220, 255), width=2)

    # Rows
    start_y = margin + header_h
    for idx, (price, player, status, avatar_img) in enumerate(rows, start=1):
        row_y = start_y + (idx - 1) * row_h
        # avatar
        ax = margin
        ay = row_y + (row_h - avatar_size) // 2
        bg.alpha_composite(avatar_img, (ax, ay))

        # texts
        tx = ax + avatar_size + 16
        d.text((tx, row_y + 18), f"{idx}. {price}p", fill=(0, 0, 0, 255), font=font_small)

        # player name
        name_x = tx + 130
        name_text = player or "unknown"
        bbox = d.textbbox((0, 0), name_text, font=font_small)
        if (bbox[2] - bbox[0]) > 360:
            # 简单截断
            while len(name_text) > 3:
                name_text = name_text[:-1]
                bbox = d.textbbox((0, 0), name_text + "…", font=font_small)
                if (bbox[2] - bbox[0]) <= 360:
                    name_text = name_text + "…"
                    break
        d.text((name_x, row_y + 18), name_text, fill=(0, 0, 0, 255), font=font_small)

        # status
        status_text = status or "unknown"
        d.text((width - margin - 220, row_y + 18), status_text, fill=(80, 80, 80, 255), font=font_small)

        # row separator
        if idx != len(rows):
            d.line((margin, row_y + row_h, width - margin, row_y + row_h), fill=(235, 235, 235, 255), width=1)

    out = io.BytesIO()
    bg.convert("RGB").save(out, format="PNG")
    return out.getvalue()


async def render_wm_orders_image_to_file(
    *,
    item: MarketItem,
    orders: list[MarketOrder],
    platform: str,
    action_cn: str,
    language: str,
    limit: int,
) -> RenderedImage | None:
    """渲染 /wm 的查询结果为一张图片并落盘，返回本地路径。"""

    if not orders:
        return None

    limit = int(limit)
    if limit <= 0:
        return None
    limit = min(limit, 20)

    item_name = item.get_localized_name(language)
    title = f"{item_name}（{platform}）{action_cn} 低->高 前{min(limit, len(orders))}"

    # 下载物品图
    item_img: Image.Image | None = None
    item_asset = item.thumb or item.icon
    if item_asset:
        item_url = _asset_url(item_asset)
        item_bytes = await _download_bytes(item_url, timeout_sec=10.0)
        if item_bytes:
            item_img = _open_image_rgba(item_bytes, size=(96, 96))

    # 并发下载头像
    avatar_urls: list[str | None] = [(_asset_url(o.avatar) if o.avatar else None) for o in orders[:limit]]

    async def dl(url: str | None) -> bytes | None:
        if not url:
            return None
        return await _download_bytes(url, timeout_sec=8.0)

    avatar_bytes_list = await asyncio.gather(*[dl(u) for u in avatar_urls])

    rows: list[tuple[int, str, str, Image.Image]] = []
    placeholder = _placeholder_avatar(size=48)
    for o, avatar_bytes in zip(orders[:limit], avatar_bytes_list, strict=False):
        avatar_img = placeholder
        if avatar_bytes:
            opened = _open_image_rgba(avatar_bytes, size=(48, 48))
            if opened is not None:
                avatar_img = opened

        rows.append(
            (
                int(o.platinum),
                (o.ingame_name or "unknown"),
                (o.status or "unknown"),
                avatar_img,
            ),
        )

    png = _render_image(title=title, item_img=item_img, rows=rows)

    out_dir = Path(get_astrbot_temp_path())
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"wm_{item.slug}_{uuid.uuid4().hex}.png"
    try:
        out_path.write_bytes(png)
    except Exception as exc:
        logger.warning(f"Failed to save wm image: {exc!s}")
        return None

    return RenderedImage(path=str(out_path))
