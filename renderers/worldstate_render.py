from __future__ import annotations

import asyncio
from dataclasses import dataclass

from astrbot.api import logger

from .html_snapshot import render_html_to_png_file
from .template_loader import load_html_template


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


def _rgba_css(color: tuple[int, int, int, int] | None, fallback: str) -> str:
    if not color:
        return fallback
    r, g, b, a = color
    alpha = max(0, min(255, int(a))) / 255
    return f"rgba({int(r)}, {int(g)}, {int(b)}, {alpha:.4f})"


def _build_worldstate_html(
    *,
    title: str,
    header_lines: list[str],
    rows: list[WorldstateRow],
    reward_rows: list[WorldstateRow] | None,
    accent: tuple[int, int, int, int],
) -> str:
    row_items: list[dict[str, str]] = []
    for row in rows:
        row_items.append(
            {
                "title": (row.title or "").strip() or "-",
                "subtitle": (row.subtitle or "").strip(),
                "right": (row.right or "").strip(),
                "tag": (row.tag or "").strip(),
                "accent_css": _rgba_css(row.accent or accent, "rgba(79, 70, 229, 1)"),
            }
        )

    reward_items: list[dict[str, str]] = []
    for row in reward_rows or []:
        reward_items.append(
            {
                "title": (row.title or "").strip() or "-",
                "subtitle": (row.subtitle or "").strip(),
                "right": (row.right or "").strip(),
                "tag": (row.tag or "").strip(),
                "accent_css": _rgba_css(row.accent or accent, "rgba(79, 70, 229, 1)"),
            }
        )

    context: dict[str, object] = {
        "page": {
            "title": (title or "").strip() or "Warframe",
            "header_lines": [
                (x or "").strip() for x in header_lines[:3] if (x or "").strip()
            ],
            "rows": row_items,
            "reward_rows": reward_items,
        }
    }

    html = load_html_template(filename="status_list.html", context=context)
    if html:
        return html

    # Hard fallback if template is accidentally removed.
    return "<html><body><pre>worldstate template not found</pre></body></html>"


async def render_worldstate_rows_image_to_file(
    *,
    title: str,
    header_lines: list[str],
    rows: list[WorldstateRow],
    reward_rows: list[WorldstateRow] | None = None,
    accent: tuple[int, int, int, int] = (79, 70, 229, 255),
    render_timeout_sec: float = 6.0,
) -> RenderedImage | None:
    if not rows and not reward_rows:
        return None

    html = _build_worldstate_html(
        title=title,
        header_lines=header_lines,
        rows=rows,
        reward_rows=reward_rows,
        accent=accent,
    )

    timeout_sec = max(1.0, float(render_timeout_sec))

    try:
        path = await asyncio.wait_for(
            render_html_to_png_file(
                html=html,
                width=980,
                prefix="wf_worldstate",
                min_height=640,
            ),
            timeout=timeout_sec,
        )
    except TimeoutError:
        logger.warning(f"worldstate html render timeout after {timeout_sec:.1f}s")
        return None

    if not path:
        return None

    return RenderedImage(path=path)


async def render_worldstate_text_image_to_file(
    *,
    title: str,
    lines: list[str],
    accent: tuple[int, int, int, int] = (79, 70, 229, 255),
) -> RenderedImage | None:
    rows = [WorldstateRow(title=line) for line in lines[:10] if (line or "").strip()]
    return await render_worldstate_rows_image_to_file(
        title=title,
        header_lines=[],
        rows=rows,
        accent=accent,
    )
