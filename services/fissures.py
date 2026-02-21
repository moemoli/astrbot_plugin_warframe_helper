from __future__ import annotations

from typing import cast

from ..clients.worldstate_client import Platform, WarframeWorldstateClient
from ..helpers import eta_key_zh
from ..renderers.worldstate_render import (
    WorldstateRow,
    render_worldstate_rows_image_to_file,
)


async def render_fissures_text(
    *,
    worldstate_client: WarframeWorldstateClient,
    platform_norm: Platform,
    fissure_kind: str,
) -> str:
    fissures = await worldstate_client.fetch_fissures(platform=platform_norm, language="zh")
    if fissures is None:
        return "未获取到裂缝信息（可能是网络限制或接口不可达）。"
    if not fissures:
        return f"当前无裂缝（{platform_norm}）。"

    def pick(f):
        if fissure_kind == "九重天":
            return f.is_storm
        if fissure_kind == "钢铁":
            return f.is_hard
        return (not f.is_storm) and (not f.is_hard)

    picked = [f for f in fissures if pick(f)]
    if not picked:
        return f"当前无{fissure_kind}裂缝（{platform_norm}）。"

    picked.sort(key=lambda x: eta_key_zh(x.eta))

    lines: list[str] = [f"裂缝（{platform_norm}）{fissure_kind} 共{len(picked)}条："]
    for f in picked:
        enemy = f" | {f.enemy}" if f.enemy else ""
        lines.append(f"- {f.tier} {f.mission_type} - {f.node} | 剩余{f.eta}{enemy}")
    return "\n".join(lines)


async def render_fissures_image(
    *,
    worldstate_client: WarframeWorldstateClient,
    platform_norm: Platform,
    fissure_kind: str,
):
    fissures = await worldstate_client.fetch_fissures(platform=platform_norm, language="zh")
    if fissures is None:
        return None
    if not fissures:
        return None

    def pick(f):
        if fissure_kind == "九重天":
            return f.is_storm
        if fissure_kind == "钢铁":
            return f.is_hard
        return (not f.is_storm) and (not f.is_hard)

    picked = [f for f in fissures if pick(f)]
    if not picked:
        return None

    picked.sort(key=lambda x: eta_key_zh(x.eta))

    def row_accent(f):
        if f.is_hard:
            return (100, 116, 139, 255)
        if f.is_storm:
            return (14, 165, 233, 255)
        return (139, 92, 246, 255)

    rows: list[WorldstateRow] = []
    for f in picked[:18]:
        enemy = f" | {f.enemy}" if f.enemy else ""
        tag = "钢铁" if f.is_hard else ("九重天" if f.is_storm else "普通")
        rows.append(
            WorldstateRow(
                title=f"{f.tier} {f.mission_type}",
                subtitle=f"{f.node}{enemy}",
                right=f"剩余{f.eta}",
                tag=tag,
                accent=cast(tuple[int, int, int, int], row_accent(f)),
            )
        )

    return await render_worldstate_rows_image_to_file(
        title="裂缝",
        header_lines=[
            f"平台：{platform_norm}",
            f"筛选：{fissure_kind}",
            f"共{len(picked)}条（展示前{min(18, len(picked))}条）",
        ],
        rows=rows,
        accent=(139, 92, 246, 255),
    )
