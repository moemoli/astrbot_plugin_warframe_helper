from __future__ import annotations

from astrbot.api.event import AstrMessageEvent

from ..renderers.worldstate_render import (
    WorldstateRow,
    render_worldstate_rows_image_to_file,
)


async def render_worldstate_single_row(
    event: AstrMessageEvent,
    *,
    title: str,
    platform_norm: str,
    row_title: str,
    row_right: str | None,
    accent: tuple[int, int, int, int],
    plain_text: str,
):
    rendered = await render_worldstate_rows_image_to_file(
        title=title,
        header_lines=[f"平台：{platform_norm}"],
        rows=[WorldstateRow(title=row_title, right=row_right)],
        accent=accent,
    )
    if rendered:
        return event.image_result(rendered.path)
    return event.plain_result(plain_text)


async def render_worldstate_cycle(
    event: AstrMessageEvent,
    *,
    title: str,
    platform_norm: str,
    state_cn: str,
    left: str,
    start_time: str | None,
    end_time: str | None,
    accent: tuple[int, int, int, int],
    plain_prefix: str,
):
    rows: list[WorldstateRow] = [
        WorldstateRow(title=f"当前：{state_cn}", right=f"剩余{left}"),
    ]
    if start_time:
        rows.append(WorldstateRow(title=f"开始：{start_time}"))
    if end_time:
        rows.append(WorldstateRow(title=f"结束：{end_time}"))

    rendered = await render_worldstate_rows_image_to_file(
        title=title,
        header_lines=[f"平台：{platform_norm}"],
        rows=rows,
        accent=accent,
    )
    if rendered:
        return event.image_result(rendered.path)

    lines = [f"{plain_prefix}（{platform_norm}）当前：{state_cn} | 剩余{left}"]
    if start_time:
        lines.append(f"开始：{start_time}")
    if end_time:
        lines.append(f"结束：{end_time}")
    return event.plain_result("\n".join(lines))
