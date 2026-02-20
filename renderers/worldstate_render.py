from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from astrbot.core.utils.astrbot_path import get_astrbot_temp_path


@dataclass(frozen=True, slots=True)
class RenderedImage:
    path: str


@dataclass(frozen=True, slots=True)
class WorldstateRow:
    title: str
    subtitle: str | None = None
    right: str | None = None
    tag: str | None = None
    accent: tuple[int, int, int, int] | None = None


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


def _truncate(
    draw: ImageDraw.ImageDraw, text: str, *, font: ImageFont.ImageFont, max_w: int
) -> str:
    s = (text or "").strip()
    if not s:
        return ""

    bbox = draw.textbbox((0, 0), s, font=font)
    if (bbox[2] - bbox[0]) <= max_w:
        return s

    t = s
    while len(t) > 2:
        t = t[:-1]
        bbox = draw.textbbox((0, 0), t + "…", font=font)
        if (bbox[2] - bbox[0]) <= max_w:
            return t + "…"
    return "…"


def _render_rows_image(
    *,
    title: str,
    header_lines: list[str],
    rows: list[WorldstateRow],
    default_accent: tuple[int, int, int, int],
) -> Image.Image:
    margin = 24
    header_h = 132
    row_h = 86
    row_gap = 10
    width = 980

    # dynamic height
    height = margin * 2 + header_h + len(rows) * row_h + max(0, len(rows) - 1) * row_gap
    if header_lines:
        height += min(3, len(header_lines)) * 22

    bg = Image.new("RGBA", (width, height), (248, 250, 252, 255))
    d = ImageDraw.Draw(bg)

    font_title = _load_font(34, weight="medium")
    font_meta = _load_font(20, weight="regular")
    font_row_title = _load_font(24, weight="medium")
    font_row_sub = _load_font(18, weight="regular")
    font_tag = _load_font(16, weight="medium")

    header_grad = _linear_gradient(
        size=(width, margin + header_h + 8),
        left=(239, 246, 255, 255),
        right=(245, 243, 255, 255),
    )
    bg.alpha_composite(header_grad, (0, 0))

    # title
    x = margin
    y = margin
    d.text((x, y + 18), title, fill=(15, 23, 42, 255), font=font_title)

    # header lines
    hy = y + 68
    for line in header_lines[:3]:
        d.text((x, hy), line, fill=(51, 65, 85, 255), font=font_meta)
        hy += 22

    # divider
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
        accent = r.accent or default_accent

        # card
        d.rounded_rectangle(
            (row_x0, row_y, row_x1, row_y + row_h),
            radius=radius,
            fill=(255, 255, 255, 255),
            outline=(226, 232, 240, 255),
            width=1,
        )
        # accent bar
        d.rounded_rectangle(
            (row_x0, row_y, row_x0 + 8, row_y + row_h),
            radius=radius,
            fill=accent,
        )

        left_x = row_x0 + 18
        right_x = row_x1 - 18

        # tag pill (right top)
        pill_w = 0
        if r.tag:
            tag = str(r.tag).strip()
            bbox = d.textbbox((0, 0), tag, font=font_tag)
            tw = bbox[2] - bbox[0]
            th = bbox[3] - bbox[1]
            pill_w = tw + 18
            pill_h = th + 10
            px1 = right_x
            px0 = px1 - pill_w
            py0 = row_y + 14
            py1 = py0 + pill_h
            d.rounded_rectangle(
                (px0, py0, px1, py1),
                radius=999,
                fill=(241, 245, 249, 255),
                outline=(226, 232, 240, 255),
                width=1,
            )
            d.text((px0 + 9, py0 + 4), tag, fill=(30, 41, 59, 255), font=font_tag)

        # right text (right center)
        right_text = (r.right or "").strip()
        if right_text:
            max_w = 320
            rt = _truncate(d, right_text, font=font_meta, max_w=max_w)
            bbox = d.textbbox((0, 0), rt, font=font_meta)
            tw = bbox[2] - bbox[0]
            d.text(
                (right_x - tw, row_y + 46), rt, fill=(51, 65, 85, 255), font=font_meta
            )

        # left title/subtitle
        max_left_w = (row_x1 - row_x0) - 18 - 18 - 360
        if pill_w:
            max_left_w = min(max_left_w, (row_x1 - row_x0) - 18 - 18 - pill_w - 22)

        t = _truncate(d, r.title, font=font_row_title, max_w=max_left_w)
        d.text((left_x, row_y + 16), t, fill=(15, 23, 42, 255), font=font_row_title)

        sub = (r.subtitle or "").strip()
        if sub:
            st = _truncate(d, sub, font=font_row_sub, max_w=max_left_w)
            d.text((left_x, row_y + 52), st, fill=(71, 85, 105, 255), font=font_row_sub)

    return bg.convert("RGB")


async def render_worldstate_rows_image_to_file(
    *,
    title: str,
    header_lines: list[str],
    rows: list[WorldstateRow],
    accent: tuple[int, int, int, int] = (79, 70, 229, 255),
) -> RenderedImage | None:
    """Render a standard worldstate card image.

    Returns None on empty rows.
    """

    if not rows:
        return None

    try:
        img = _render_rows_image(
            title=title,
            header_lines=header_lines,
            rows=rows,
            default_accent=accent,
        )
        temp_dir = Path(get_astrbot_temp_path())
        temp_dir.mkdir(parents=True, exist_ok=True)
        out = temp_dir / f"wf_worldstate_{uuid.uuid4().hex}.png"
        img.save(out, format="PNG", optimize=True)
        path = str(out)
        return RenderedImage(path=path)
    except Exception:
        return None


async def render_worldstate_text_image_to_file(
    *,
    title: str,
    lines: list[str],
    accent: tuple[int, int, int, int] = (79, 70, 229, 255),
) -> RenderedImage | None:
    """Fallback renderer: render a small card with a few text lines."""

    header = []
    rows = [WorldstateRow(title=line) for line in lines[:10] if (line or "").strip()]
    return await render_worldstate_rows_image_to_file(
        title=title, header_lines=header, rows=rows, accent=accent
    )
