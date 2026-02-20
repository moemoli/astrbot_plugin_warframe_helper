from __future__ import annotations

import asyncio
import io
import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

from astrbot.api import logger
from astrbot.core.utils.astrbot_path import get_astrbot_temp_path

from ..clients.market_client import RivenAttribute, RivenAuction
from ..constants import RIVEN_STAT_CN
from ..http_utils import fetch_bytes
from ..mappers.riven_mapping import RivenWeapon

WARFRAME_MARKET_ASSETS_BASE_URL = "https://warframe.market/static/assets/"

_WFM_POLARITY_ICONS: dict[str, dict[str, str]] = {
    "madurai": {"symbol": "icon-madurai", "viewBox": "0 0 16.267 16.491"},
    "vazarin": {"symbol": "icon-vazarin", "viewBox": "0 0 13.546 16.345"},
    "naramon": {"symbol": "icon-naramon", "viewBox": "0 0 18 18"},
}

_polarity_icon_cache: dict[tuple[str, int], Image.Image] = {}
_polarity_svg_cache: dict[str, str] = {}

_POLARITY_ICON_COLOR = (71, 85, 105, 255)


_SVG_NUMBER_RE = re.compile(r"[-+]?(?:\d*\.\d+|\d+)(?:[eE][-+]?\d+)?")


def _parse_viewbox(svg_text: str) -> tuple[float, float, float, float] | None:
    m = re.search(r'viewBox\s*=\s*"([^"]+)"', svg_text)
    if not m:
        return None
    parts = [p for p in m.group(1).replace(",", " ").split() if p]
    if len(parts) != 4:
        return None
    try:
        x, y, w, h = (float(parts[0]), float(parts[1]), float(parts[2]), float(parts[3]))
    except Exception:
        return None
    if w <= 0 or h <= 0:
        return None
    return x, y, w, h


def _extract_path_ds(svg_text: str) -> list[str]:
    # Support multiple <path ... d="..."/> inside one svg.
    return [m.group(1) for m in re.finditer(r'<path[^>]*\sd\s*=\s*"([^"]+)"', svg_text)]


def _tokenize_svg_path(d: str) -> list[str | float]:
    tokens: list[str | float] = []
    i = 0
    n = len(d)
    while i < n:
        ch = d[i]
        if ch.isspace() or ch == ",":
            i += 1
            continue
        if ch in "MmLlCcSsZzHhVv":
            tokens.append(ch)
            i += 1
            continue
        m = _SVG_NUMBER_RE.match(d, i)
        if not m:
            i += 1
            continue
        try:
            tokens.append(float(m.group(0)))
        except Exception:
            pass
        i = m.end()
    return tokens


def _cubic_point(
    p0: tuple[float, float],
    p1: tuple[float, float],
    p2: tuple[float, float],
    p3: tuple[float, float],
    t: float,
) -> tuple[float, float]:
    # Bernstein polynomial form.
    x0, y0 = p0
    x1, y1 = p1
    x2, y2 = p2
    x3, y3 = p3
    mt = 1.0 - t
    a = mt * mt * mt
    b = 3.0 * mt * mt * t
    c = 3.0 * mt * t * t
    d = t * t * t
    return (
        a * x0 + b * x1 + c * x2 + d * x3,
        a * y0 + b * y1 + c * y2 + d * y3,
    )


def _svg_path_to_polygons(d: str) -> list[list[tuple[float, float]]]:
    toks = _tokenize_svg_path(d)
    polys: list[list[tuple[float, float]]] = []

    cmd: str | None = None
    i = 0
    cx = cy = 0.0
    sx = sy = 0.0
    cur: list[tuple[float, float]] = []
    prev_cmd: str | None = None
    prev_c2: tuple[float, float] | None = None

    def flush() -> None:
        nonlocal cur
        if cur:
            polys.append(cur)
            cur = []

    def ensure_start() -> None:
        if not cur:
            cur.append((cx, cy))

    while i < len(toks):
        t = toks[i]
        if isinstance(t, str):
            cmd = t
            i += 1
            continue
        if cmd is None:
            i += 1
            continue

        # Only implement the subset we actually need.
        if cmd in {"M", "m"}:
            if i + 1 >= len(toks) or not isinstance(toks[i + 1], float):
                break
            x = float(toks[i])
            y = float(toks[i + 1])
            i += 2
            if cmd == "m":
                x += cx
                y += cy
            flush()
            cx, cy = x, y
            sx, sy = x, y
            cur.append((cx, cy))
            prev_c2 = None
            prev_cmd = cmd

            # Subsequent pairs are treated as implicit lineto.
            cmd = "L" if cmd == "M" else "l"
            continue

        if cmd in {"L", "l"}:
            if i + 1 >= len(toks) or not isinstance(toks[i + 1], float):
                break
            x = float(toks[i])
            y = float(toks[i + 1])
            i += 2
            if cmd == "l":
                x += cx
                y += cy
            ensure_start()
            cx, cy = x, y
            cur.append((cx, cy))
            prev_c2 = None
            prev_cmd = cmd
            continue

        if cmd in {"H", "h"}:
            x = float(toks[i])
            i += 1
            if cmd == "h":
                x += cx
            ensure_start()
            cx = x
            cur.append((cx, cy))
            prev_c2 = None
            prev_cmd = cmd
            continue

        if cmd in {"V", "v"}:
            y = float(toks[i])
            i += 1
            if cmd == "v":
                y += cy
            ensure_start()
            cy = y
            cur.append((cx, cy))
            prev_c2 = None
            prev_cmd = cmd
            continue

        if cmd in {"C", "c"}:
            if i + 5 >= len(toks) or not all(isinstance(toks[i + k], float) for k in range(6)):
                break
            x1, y1, x2, y2, x, y = (float(toks[i]), float(toks[i + 1]), float(toks[i + 2]), float(toks[i + 3]), float(toks[i + 4]), float(toks[i + 5]))
            i += 6
            if cmd == "c":
                x1 += cx
                y1 += cy
                x2 += cx
                y2 += cy
                x += cx
                y += cy
            ensure_start()
            p0 = (cx, cy)
            p1 = (x1, y1)
            p2 = (x2, y2)
            p3 = (x, y)
            steps = 22
            for s in range(1, steps + 1):
                cur.append(_cubic_point(p0, p1, p2, p3, s / steps))
            cx, cy = x, y
            prev_c2 = (x2, y2)
            prev_cmd = cmd
            continue

        if cmd in {"S", "s"}:
            if i + 3 >= len(toks) or not all(isinstance(toks[i + k], float) for k in range(4)):
                break
            x2, y2, x, y = (float(toks[i]), float(toks[i + 1]), float(toks[i + 2]), float(toks[i + 3]))
            i += 4
            if cmd == "s":
                x2 += cx
                y2 += cy
                x += cx
                y += cy
            if prev_cmd in {"C", "c", "S", "s"} and prev_c2 is not None:
                x1 = 2.0 * cx - prev_c2[0]
                y1 = 2.0 * cy - prev_c2[1]
            else:
                x1, y1 = cx, cy
            ensure_start()
            p0 = (cx, cy)
            p1 = (x1, y1)
            p2 = (x2, y2)
            p3 = (x, y)
            steps = 22
            for s in range(1, steps + 1):
                cur.append(_cubic_point(p0, p1, p2, p3, s / steps))
            cx, cy = x, y
            prev_c2 = (x2, y2)
            prev_cmd = cmd
            continue

        if cmd in {"Z", "z"}:
            if cur:
                cur.append((sx, sy))
            flush()
            cx, cy = sx, sy
            prev_c2 = None
            prev_cmd = cmd
            i += 1
            continue

        # Unsupported command: skip one token to avoid infinite loop.
        i += 1

    flush()
    return polys


def _rasterize_svg_paths_to_icon(svg_text: str, *, size: int) -> Image.Image | None:
    vb = _parse_viewbox(svg_text)
    ds = _extract_path_ds(svg_text)
    if vb is None or not ds:
        return None

    vb_x, vb_y, vb_w, vb_h = vb
    s = max(8, int(size))
    oversample = 4
    target = s * oversample
    pad = 2 * oversample

    scale = (target - 2 * pad) / max(vb_w, vb_h)
    if scale <= 0:
        return None

    content_w = vb_w * scale
    content_h = vb_h * scale
    off_x = (target - content_w) / 2.0 - vb_x * scale
    off_y = (target - content_h) / 2.0 - vb_y * scale

    mask = Image.new("L", (target, target), 0)
    md = ImageDraw.Draw(mask)

    for d in ds:
        for poly in _svg_path_to_polygons(d):
            if len(poly) < 3:
                continue
            pts = [
                (float(x) * scale + off_x, float(y) * scale + off_y)
                for (x, y) in poly
            ]
            try:
                md.polygon(pts, fill=255)
            except Exception:
                continue

    mask = mask.resize((s, s), Image.Resampling.LANCZOS)

    icon = Image.new("RGBA", (s, s), _POLARITY_ICON_COLOR)
    icon.putalpha(mask)
    return icon


def _plugin_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _polarity_assets_dir() -> Path:
    return _plugin_root() / "assets" / "polarity"


def _polarity_svg_path(polarity: str) -> Path:
    return _polarity_assets_dir() / f"{polarity.strip().lower()}.svg"


@dataclass(frozen=True, slots=True)
class RenderedImage:
    path: str


def _asset_url(asset_path: str) -> str:
    return WARFRAME_MARKET_ASSETS_BASE_URL + asset_path.lstrip("/")


async def _get_polarity_icon(
    polarity: str | None,
    *,
    size: int,
) -> Image.Image | None:
    """Get polarity icon image from locally vendored SVGs.

    Offline-only: it never downloads assets at runtime.
    """

    p = (polarity or "").strip().lower()
    if not p:
        return None

    meta = _WFM_POLARITY_ICONS.get(p)
    if not meta:
        return None

    s = max(8, int(size))
    cache_key = (p, s)
    cached = _polarity_icon_cache.get(cache_key)
    if cached is not None:
        return cached

    svg_text = _polarity_svg_cache.get(p)
    if not svg_text:
        svg_path = _polarity_svg_path(p)
        if not svg_path.exists() or svg_path.stat().st_size <= 0:
            # Known polarity but missing asset: return a transparent placeholder.
            return Image.new("RGBA", (s, s), (0, 0, 0, 0))
        try:
            svg_text = svg_path.read_text(encoding="utf-8")
            _polarity_svg_cache[p] = svg_text
        except Exception:
            return Image.new("RGBA", (s, s), (0, 0, 0, 0))

    icon = _rasterize_svg_paths_to_icon(svg_text, size=s)
    if icon is None:
        icon = Image.new("RGBA", (s, s), (0, 0, 0, 0))

    _polarity_icon_cache[cache_key] = icon
    return icon


async def _download_bytes(url: str, *, timeout_sec: float = 10.0) -> bytes | None:
    headers = {
        "User-Agent": "AstrBot/warframe_helper (+https://github.com/Soulter/AstrBot)",
        "Accept": "image/*,*/*;q=0.8",
    }
    return await fetch_bytes(url, timeout_sec=timeout_sec, headers=headers)


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


_STAT_CN: dict[str, str] = {
    **RIVEN_STAT_CN,
    # More common riven stats not covered by shared constants
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


def _truncate_text(
    draw: ImageDraw.ImageDraw, text: str, *, font: ImageFont.ImageFont, max_w: int
) -> str:
    s = str(text or "")
    if not s or max_w <= 0:
        return ""
    bbox = draw.textbbox((0, 0), s, font=font)
    if (bbox[2] - bbox[0]) <= max_w:
        return s

    lo, hi = 0, len(s)
    while lo < hi:
        mid = (lo + hi) // 2
        t = s[:mid].rstrip() + "…"
        bbox = draw.textbbox((0, 0), t, font=font)
        if (bbox[2] - bbox[0]) <= max_w:
            lo = mid + 1
        else:
            hi = mid

    t = s[: max(0, lo - 1)].rstrip() + "…"
    bbox = draw.textbbox((0, 0), t, font=font)
    if (bbox[2] - bbox[0]) <= max_w:
        return t
    return ""


def _fmt_attr(a: RivenAttribute) -> str:
    """Legacy formatter kept for compatibility."""

    label, val = _fmt_attr_parts(a)
    return f"{label}{val}"


def _fmt_attr_parts(a: RivenAttribute) -> tuple[str, str]:
    label = _STAT_CN.get(a.url_name, a.url_name)

    # 部分字段 value 是倍率（0.74），部分是百分比（146.2）。这里用启发式：
    # - |v| < 10 => 认为是倍率，显示为 x?.??（此时不加 +/- 符号）
    # - 其他 => 认为是百分比，显示为 +/-?.?%（符号放在数值前）
    v = float(a.value)
    if abs(v) < 10:
        vv = abs(v)
        val = f"x{vv:.2f}"
        return label, val

    vv = abs(v)
    sign = "+" if a.positive else "-"
    val = f"{sign}{vv:.1f}%"
    return label, val


def _text_w(draw: ImageDraw.ImageDraw, text: str, *, font: ImageFont.ImageFont) -> int:
    bbox = draw.textbbox((0, 0), text, font=font)
    return int(bbox[2] - bbox[0])


def _is_attr_part(obj: object) -> bool:
    return (
        isinstance(obj, tuple)
        and len(obj) == 2
        and isinstance(obj[0], str)
        and isinstance(obj[1], str)
    )


def _draw_attr_parts_line(
    draw: ImageDraw.ImageDraw,
    *,
    x: int,
    y: int,
    attrs: list[tuple[str, str]],
    max_w: int,
    font: ImageFont.ImageFont,
    label_color: tuple[int, int, int, int],
    value_color: tuple[int, int, int, int],
) -> None:
    if not attrs or max_w <= 0:
        return

    cur_x = int(x)
    remaining = int(max_w)
    first = True
    ell = "…"
    ell_w = _text_w(draw, ell, font=font)
    space_w = _text_w(draw, " ", font=font)

    for label, value in attrs:
        sep = "" if first else "，"
        label_text = f"{sep}{label}"
        value_text = str(value)

        label_w = _text_w(draw, label_text, font=font)
        value_w = _text_w(draw, value_text, font=font)

        total_w = label_w + space_w + value_w

        if total_w <= remaining:
            draw.text((cur_x, y), label_text, fill=label_color, font=font)
            cur_x += label_w
            cur_x += space_w
            draw.text((cur_x, y), value_text, fill=value_color, font=font)
            cur_x += value_w
            remaining -= total_w
            first = False
            continue

        # overflow handling
        if remaining <= ell_w:
            draw.text((cur_x, y), ell, fill=label_color, font=font)
            return

        if label_w >= remaining:
            t = _truncate_text(draw, label_text, font=font, max_w=remaining)
            draw.text((cur_x, y), t, fill=label_color, font=font)
            return

        # label fits, value doesn't
        draw.text((cur_x, y), label_text, fill=label_color, font=font)
        cur_x += label_w
        remaining -= label_w

        # Insert a single space between label and value.
        if remaining <= space_w:
            draw.text((cur_x, y), ell, fill=label_color, font=font)
            return
        cur_x += space_w
        remaining -= space_w

        t = _truncate_text(draw, value_text, font=font, max_w=remaining)
        draw.text((cur_x, y), t, fill=value_color, font=font)
        return


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
    weapon_bg_img: Image.Image | None,
    polarity_icons: dict[str, Image.Image] | None,
    rows: list[dict],
) -> bytes:
    margin = 24
    header_h = 132
    row_h = 124
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

    color_pos = (34, 197, 94, 255)
    color_neg = (239, 68, 68, 255)
    color_attr_label = (100, 116, 139, 255)
    color_meta = (71, 85, 105, 255)

    header_size = (width, margin + header_h + 8)
    if weapon_bg_img is not None:
        # Use the weapon background across the whole canvas.
        icon_bg = _resize_cover(weapon_bg_img, size=(width, height))
        icon_bg = _apply_alpha(icon_bg, factor=0.36)
        bg.alpha_composite(icon_bg, (0, 0))

    header_grad = _linear_gradient(
        size=header_size,
        left=(239, 246, 255, 205),
        right=(245, 243, 255, 205),
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

    card_alpha = 255

    for i, r in enumerate(rows):
        row_y = start_y + i * (row_h + row_gap)
        status = r.get("status")
        accent = _row_accent_color(status)

        d.rounded_rectangle(
            (row_x0, row_y, row_x1, row_y + row_h),
            radius=radius,
            fill=(255, 255, 255, card_alpha),
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
        price = int(r.get("price") or 0)
        price_text = f"{price}p"
        bbox_p = d.textbbox((0, 0), price_text, font=font_title)
        pw = bbox_p[2] - bbox_p[0]
        right_x = row_x1 - 18 - pw - 16
        max_left_w = max(10, right_x - name_x)

        name_y = row_y + 16
        name_text = _truncate_text(d, name, font=font_name, max_w=max_left_w)
        d.text((name_x, name_y), name_text, fill=(15, 23, 42, 255), font=font_name)
        bbox = d.textbbox((0, 0), name_text, font=font_name)
        dot_x = name_x + (bbox[2] - bbox[0]) + 10
        dot_y = name_y + 10
        if dot_x + 10 < right_x:
            d.ellipse((dot_x, dot_y, dot_x + 10, dot_y + 10), fill=status_dot)

        meta_y = row_y + 46
        mr_val = r.get("mr")
        rr_val = r.get("rr")
        pol_val = r.get("polarity")

        # Fixed columns so polarity & rerolls align vertically across rows.
        mr_x = name_x
        icon_size = 18
        mr_col_w = 70
        pol_col_w = icon_size + 14
        pol_x = mr_x + mr_col_w
        rr_x = pol_x + pol_col_w

        mr_max_w = max(0, pol_x - mr_x - 8)
        rr_max_w = max(0, right_x - rr_x)

        if mr_val is not None and mr_max_w > 0:
            mr_text = f"MR{int(mr_val)}"
            mr_text = _truncate_text(d, mr_text, font=font_meta, max_w=mr_max_w)
            if mr_text:
                d.text((mr_x, meta_y), mr_text, fill=color_meta, font=font_meta)

        if pol_val and rr_x < right_x:
            pol_key = str(pol_val).strip().lower()
            icon = (polarity_icons or {}).get(pol_key)
            if icon is not None and icon.getbbox() is not None:
                bg.alpha_composite(icon, (int(pol_x), int(meta_y + 2)))
            elif pol_key and pol_key not in _WFM_POLARITY_ICONS:
                # Keep non-offline polarities readable.
                pol_text = f"极性{_fmt_polarity(pol_key)}"
                pol_text = _truncate_text(
                    d,
                    pol_text,
                    font=font_meta,
                    max_w=max(0, rr_x - pol_x - 8),
                )
                if pol_text:
                    d.text((pol_x, meta_y), pol_text, fill=color_meta, font=font_meta)

        if rr_val is not None and rr_max_w > 0:
            rr_text = f"洗练{int(rr_val)}"
            rr_text = _truncate_text(d, rr_text, font=font_meta, max_w=rr_max_w)
            if rr_text:
                d.text((rr_x, meta_y), rr_text, fill=color_meta, font=font_meta)

        pos_attrs = r.get("pos_attrs")
        neg_attrs = r.get("neg_attrs")
        pos_list = list(pos_attrs) if isinstance(pos_attrs, list) else []
        neg_list = list(neg_attrs) if isinstance(neg_attrs, list) else []

        if pos_list:
            _draw_attr_parts_line(
                d,
                x=name_x,
                y=row_y + 72,
                attrs=[p for p in pos_list if _is_attr_part(p)],
                max_w=max_left_w,
                font=font_attr,
                label_color=color_attr_label,
                value_color=color_pos,
            )

        if not neg_list:
            neg_text = _truncate_text(d, "无负面词条", font=font_attr, max_w=max_left_w)
            d.text((name_x, row_y + 94), neg_text, fill=color_neg, font=font_attr)
        else:
            _draw_attr_parts_line(
                d,
                x=name_x,
                y=row_y + 94,
                attrs=[p for p in neg_list if _is_attr_part(p)],
                max_w=max_left_w,
                font=font_attr,
                label_color=color_attr_label,
                value_color=color_neg,
            )

        d.text(
            (row_x1 - 18 - pw, row_y + 30),
            price_text,
            fill=(37, 99, 235, 255),
            font=font_title,
        )

    # Weapon badge stays inside the header area (no overlap into content).

    out = io.BytesIO()
    bg.convert("RGB").save(out, format="PNG", optimize=True)
    return out.getvalue()


async def render_wmr_auctions_image_to_file(
    *,
    weapon: RivenWeapon,
    weapon_display_name: str,
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
    weapon_bg_img: Image.Image | None = None
    weapon_asset = weapon.thumb or weapon.icon
    if weapon_asset:
        b = await _download_bytes(_asset_url(weapon_asset))
        if b:
            weapon_img = _open_image_rgba(b, size=(96, 96), contain=True)
            try:
                weapon_bg_img = Image.open(io.BytesIO(b)).convert("RGBA")
            except Exception:
                weapon_bg_img = None

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
        mr = a.mastery_level
        rr = a.re_rolls
        pos = [x for x in a.attributes if x.positive]
        neg = [x for x in a.attributes if not x.positive]
        pos_parts = [_fmt_attr_parts(x) for x in pos]
        neg_parts = [_fmt_attr_parts(x) for x in neg]

        rows.append(
            {
                "price": a.buyout_price,
                "name": name,
                "status": status,
                "mr": mr,
                "polarity": a.polarity,
                "rr": rr,
                "pos_attrs": pos_parts,
                "neg_attrs": neg_parts,
                "avatar": avatars[i] if i < len(avatars) else None,
            }
        )

    name = (weapon_display_name or weapon.item_name or "").strip() or weapon.item_name
    title = f"紫卡 {name}（{platform}） 前{limit}"

    # Prefetch polarity icons (offline-only). Missing/invalid SVGs will result in a
    # transparent placeholder instead of falling back to text.
    polarity_icons: dict[str, Image.Image] = {}
    unique_polarities = {
        (a.polarity or "").strip().lower()
        for a in auctions
        if (a.polarity or "").strip()
    }
    for p in unique_polarities:
        if p not in _WFM_POLARITY_ICONS:
            continue
        icon = await _get_polarity_icon(p, size=18)
        if icon is not None:
            polarity_icons[p] = icon

    img_bytes = _render_image(
        title=title,
        weapon_img=weapon_img,
        weapon_bg_img=weapon_bg_img,
        polarity_icons=polarity_icons,
        rows=rows,
    )

    file_path = temp_dir / f"wmr_{uuid.uuid4().hex}.png"
    try:
        file_path.write_bytes(img_bytes)
        return RenderedImage(path=str(file_path))
    except Exception as exc:
        logger.debug(f"Failed to write wmr image: {exc!s}")
        return None
