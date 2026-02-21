from __future__ import annotations

import asyncio
import io
import os
import uuid
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from astrbot.api import logger
from astrbot.core.utils.astrbot_path import get_astrbot_temp_path

from ..clients.market_client import MarketOrder
from ..http_utils import fetch_bytes
from ..mappers.term_mapping import MarketItem

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


def _load_font(
    size: int,
    *,
    weight: str = "regular",
) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """加载字体。

    优先使用插件自带 fonts 下的 NotoSansHans 字体（保证中文不乱码）；
    若缺失，再回退系统字体，最后回退 Pillow 默认字体。
    """

    candidates: list[str] = []

    # 1) 插件自带字体（优先）
    fonts_dir = Path(__file__).resolve().parent.parent / "fonts"
    if fonts_dir.exists():
        if weight.lower() in {"medium", "bold", "semibold"}:
            candidates.append(str(fonts_dir / "NotoSansHans-Medium.otf"))
        candidates.append(str(fonts_dir / "NotoSansHans-Regular.otf"))

    # 2) 系统字体（兜底）
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

    candidates.extend(
        [
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
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


def _open_image_rgba(
    image_bytes: bytes,
    *,
    size: tuple[int, int],
    contain: bool = False,
) -> Image.Image | None:
    try:
        img = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
        if not contain:
            return img.resize(size, Image.Resampling.LANCZOS)

        tw, th = size
        if tw <= 0 or th <= 0:
            return img

        # Keep aspect ratio, center-pad into target size.
        img.thumbnail((tw, th), Image.Resampling.LANCZOS)
        out = Image.new("RGBA", (tw, th), (0, 0, 0, 0))
        x = (tw - img.width) // 2
        y = (th - img.height) // 2
        out.alpha_composite(img, (x, y))
        return out
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
    """根据 warframe.market 的用户在线状态返回圆点颜色。

    期望：
    - 游戏中(ingame) -> 绿点
    - 在线(online) -> 黄点
    - 离线(offline/unknown) -> 灰点
    """

    s = (status or "").strip().lower()
    if s in {"ingame", "in_game", "in-game", "in game"}:
        return (34, 197, 94, 255)  # green
    if s in {"online"}:
        return (234, 179, 8, 255)  # yellow
    return (156, 163, 175, 255)  # gray


def _row_accent_color(status: str | None) -> tuple[int, int, int, int]:
    s = (status or "").strip().lower()
    if s in {"ingame", "in_game", "in-game", "in game"}:
        return (16, 185, 129, 255)  # emerald
    if s in {"online"}:
        return (245, 158, 11, 255)  # amber
    return (209, 213, 219, 255)  # gray


def _linear_gradient(
    *,
    size: tuple[int, int],
    left: tuple[int, int, int, int],
    right: tuple[int, int, int, int],
) -> Image.Image:
    """生成一个简单的横向线性渐变 RGBA 图。"""

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


def _resize_cover(img: Image.Image, *, size: tuple[int, int]) -> Image.Image:
    """Resize image to cover target size while preserving aspect ratio, then center-crop."""

    tw, th = size
    if tw <= 0 or th <= 0:
        return img

    if img.mode != "RGBA":
        img = img.convert("RGBA")

    sw, sh = img.size
    if sw <= 0 or sh <= 0:
        return img

    scale = max(tw / sw, th / sh)
    nw = max(1, int(round(sw * scale)))
    nh = max(1, int(round(sh * scale)))
    resized = img.resize((nw, nh), Image.Resampling.LANCZOS)

    left = max(0, (nw - tw) // 2)
    top = max(0, (nh - th) // 2)
    return resized.crop((left, top, left + tw, top + th))


def _apply_alpha(img: Image.Image, *, factor: float) -> Image.Image:
    """Multiply the alpha channel by `factor` (0~1)."""

    f = max(0.0, min(float(factor), 1.0))
    if f >= 1.0:
        return img

    if img.mode != "RGBA":
        img = img.convert("RGBA")

    r, g, b, a = img.split()
    a2 = a.point(lambda p: int(p * f))
    return Image.merge("RGBA", (r, g, b, a2))


def _render_image(
    *,
    title: str,
    item_avatar_img: Image.Image | None,
    item_bg_img: Image.Image | None,
    rows: list[tuple[int, int, str, str | None, Image.Image]],
) -> bytes:
    # 轻量卡片布局（避免引入额外功能，仅做排版美化）
    margin = 24
    header_h = 128
    row_h = 76
    avatar_size = 52
    row_gap = 10

    width = 920
    height = margin * 2 + header_h + len(rows) * row_h + max(0, len(rows) - 1) * row_gap

    bg = Image.new("RGBA", (width, height), (248, 250, 252, 255))
    d = ImageDraw.Draw(bg)

    font_title = _load_font(34, weight="medium")
    font_name = _load_font(24, weight="medium")
    font_meta = _load_font(20, weight="regular")

    header_size = (width, margin + header_h + 8)
    if item_bg_img is not None:
        # Use the item background across the whole canvas.
        # Keep it subtle but clearly visible.
        icon_bg = _resize_cover(item_bg_img, size=(width, height))
        icon_bg = _apply_alpha(icon_bg, factor=0.36)
        bg.alpha_composite(icon_bg, (0, 0))

    # Header: 轻渐变背景（半透明，遮住背景图标但保留层次）
    header_grad = _linear_gradient(
        size=header_size,
        # Increase transparency to make the background less washed out.
        left=(239, 246, 255, 205),
        right=(245, 243, 255, 205),
    )
    bg.alpha_composite(header_grad, (0, 0))

    # Header
    x = margin
    y = margin
    if item_avatar_img is not None:
        bg.alpha_composite(_circle_avatar(item_avatar_img, size=96), (x, y + 10))
        x += 96 + 16

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

    d.text((x, y + 18), title_text, fill=(15, 23, 42, 255), font=font_title)

    # 分割线
    d.line(
        (margin, margin + header_h, width - margin, margin + header_h),
        fill=(226, 232, 240, 255),
        width=2,
    )

    # Rows
    start_y = margin + header_h + 18
    row_x0 = margin
    row_x1 = width - margin
    radius = 14

    card_alpha = 255

    for i, (price, qty, player, status, avatar_img) in enumerate(rows):
        row_y = start_y + i * (row_h + row_gap)

        accent = _row_accent_color(status)

        # row card
        d.rounded_rectangle(
            (row_x0, row_y, row_x1, row_y + row_h),
            radius=radius,
            fill=(255, 255, 255, card_alpha),
            outline=(226, 232, 240, 255),
            width=1,
        )

        # 左侧强调色条（根据状态）
        d.rounded_rectangle(
            (row_x0 + 2, row_y + 10, row_x0 + 8, row_y + row_h - 10),
            radius=3,
            fill=(accent[0], accent[1], accent[2], 200),
        )

        # avatar
        ax = row_x0 + 14
        ay = row_y + (row_h - avatar_size) // 2
        bg.alpha_composite(_circle_avatar(avatar_img, size=avatar_size), (ax, ay))

        # name + dot
        name_x = ax + avatar_size + 14
        name_text = (player or "unknown").strip() or "unknown"
        # 名字截断，避免挤到右侧价格
        max_name_w = 430
        bbox = d.textbbox((0, 0), name_text, font=font_name)
        if (bbox[2] - bbox[0]) > max_name_w:
            while len(name_text) > 3:
                name_text = name_text[:-1]
                bbox = d.textbbox((0, 0), name_text + "…", font=font_name)
                if (bbox[2] - bbox[0]) <= max_name_w:
                    name_text = name_text + "…"
                    break

        name_y = row_y + 18
        d.text((name_x, name_y), name_text, fill=(17, 24, 39, 255), font=font_name)

        # status dot: name 后方
        dot_color = _status_dot_color(status)
        name_w = d.textbbox((0, 0), name_text, font=font_name)[2]
        dot_r = 6
        dot_x = name_x + name_w + 10
        dot_y = name_y + 9
        d.ellipse(
            (dot_x, dot_y, dot_x + dot_r * 2, dot_y + dot_r * 2),
            fill=dot_color,
            outline=(255, 255, 255, 255),
            width=2,
        )

        # price (right)
        price_text = f"{price}p"
        qty_text = f"x{qty}" if qty > 1 else ""
        # 右对齐
        price_bbox = d.textbbox((0, 0), price_text, font=font_name)
        price_w = price_bbox[2] - price_bbox[0]
        price_x = row_x1 - 18 - price_w
        # 价格稍微用强调色，提升可读性
        d.text((price_x, name_y), price_text, fill=(37, 99, 235, 255), font=font_name)
        if qty_text:
            d.text(
                (price_x, name_y + 30),
                qty_text,
                fill=(107, 114, 128, 255),
                font=font_meta,
            )

    # Item badge stays inside the header area (no overlap into content).

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
    title = f"{item_name}（{platform}）{action_cn}"

    # 下载物品图
    item_avatar_img: Image.Image | None = None
    item_bg_img: Image.Image | None = None
    item_asset = item.thumb or item.icon
    if item_asset:
        item_url = _asset_url(item_asset)
        item_bytes = await _download_bytes(item_url, timeout_sec=10.0)
        if item_bytes:
            item_avatar_img = _open_image_rgba(item_bytes, size=(96, 96), contain=True)
            try:
                item_bg_img = Image.open(io.BytesIO(item_bytes)).convert("RGBA")
            except Exception:
                item_bg_img = None

    # 并发下载头像
    avatar_urls: list[str | None] = [
        (_asset_url(o.avatar) if o.avatar else None) for o in orders[:limit]
    ]

    async def dl(url: str | None) -> bytes | None:
        if not url:
            return None
        return await _download_bytes(url, timeout_sec=8.0)

    avatar_bytes_list = await asyncio.gather(*[dl(u) for u in avatar_urls])

    rows: list[tuple[int, int, str, str | None, Image.Image]] = []
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
                int(o.quantity),
                (o.ingame_name or "unknown"),
                o.status,
                avatar_img,
            ),
        )

    png = _render_image(
        title=title,
        item_avatar_img=item_avatar_img,
        item_bg_img=item_bg_img,
        rows=rows,
    )

    out_dir = Path(get_astrbot_temp_path())
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"wm_{item.slug}_{uuid.uuid4().hex}.png"
    try:
        out_path.write_bytes(png)
    except Exception as exc:
        logger.warning(f"Failed to save wm image: {exc!s}")
        return None

    return RenderedImage(path=str(out_path))
