from __future__ import annotations

from astrbot.api.event import AstrMessageEvent

from ..clients.public_export_client import PublicExportClient
from ..renderers.worldstate_render import (
    WorldstateRow,
    render_worldstate_rows_image_to_file,
)


async def _localized_title(
    *,
    public_export_client: PublicExportClient,
    name: object,
    unique_name: object,
    language: str = "zh",
) -> str:
    raw_name = str(name) if isinstance(name, str) and name else ""
    raw_unique = (
        str(unique_name) if isinstance(unique_name, str) and unique_name else ""
    )

    if raw_unique:
        mapped = await public_export_client.translate_unique_name_loose(
            raw_unique,
            language=language,
        )
        if isinstance(mapped, str) and mapped.strip():
            return mapped.strip()

    if raw_name:
        mapped = await public_export_client.translate_display_name(
            raw_name,
            language=language,
        )
        if isinstance(mapped, str) and mapped.strip():
            return mapped.strip()
        return raw_name

    return "?"


async def cmd_warframe(
    *,
    event: AstrMessageEvent,
    query: str,
    public_export_client: PublicExportClient,
):
    query = str(query).strip()
    if not query:
        return event.plain_result("用法：/战甲 <名称> 例如：/战甲 Rhino")

    items = await public_export_client.search_warframe(query, language="zh", limit=5)
    if not items:
        return event.plain_result(f"未找到战甲：{query}")

    rows: list[WorldstateRow] = []
    lines: list[str] = [f"战甲搜索：{query}（展示前{len(items)}条）"]
    for w in items:
        if not isinstance(w, dict):
            continue
        name = w.get("name")
        uniq = w.get("uniqueName")
        hp = w.get("health")
        shield = w.get("shield")
        armor = w.get("armor")
        energy = w.get("power") or w.get("energy")
        sprint = w.get("sprintSpeed") or w.get("sprint_speed")

        name_s = await _localized_title(
            public_export_client=public_export_client,
            name=name,
            unique_name=uniq,
            language="zh",
        )
        uniq_s = str(uniq) if isinstance(uniq, str) and uniq else ""
        stat_parts: list[str] = []
        if isinstance(hp, (int, float)):
            stat_parts.append(f"血{int(hp)}")
        if isinstance(shield, (int, float)):
            stat_parts.append(f"盾{int(shield)}")
        if isinstance(armor, (int, float)):
            stat_parts.append(f"甲{int(armor)}")
        if isinstance(energy, (int, float)):
            stat_parts.append(f"能{int(energy)}")
        if isinstance(sprint, (int, float)):
            stat_parts.append(f"速{float(sprint):.2g}")
        right = " ".join(stat_parts) or None

        rows.append(WorldstateRow(title=name_s, subtitle=uniq_s or None, right=right))
        suffix = f" | {right}" if right else ""
        extra = f" | {uniq_s}" if uniq_s else ""
        lines.append(f"- {name_s}{suffix}{extra}")

    rendered = await render_worldstate_rows_image_to_file(
        title="战甲",
        header_lines=["数据源：PublicExport", f"查询：{query}"],
        rows=rows,
        accent=(34, 197, 94, 255),
    )
    if rendered:
        return event.image_result(rendered.path)

    return event.plain_result("\n".join(lines))


async def cmd_weapon(
    *,
    event: AstrMessageEvent,
    query: str,
    public_export_client: PublicExportClient,
):
    query = str(query).strip()
    if not query:
        return event.plain_result("用法：/武器 <名称> 例如：/武器 绝路 或 /武器 soma")

    items = await public_export_client.search_weapon(query, language="zh", limit=5)
    if not items:
        return event.plain_result(f"未找到武器：{query}")

    rows: list[WorldstateRow] = []
    lines: list[str] = [f"武器搜索：{query}（展示前{len(items)}条）"]
    for w in items:
        name = w.get("name") if isinstance(w, dict) else None
        uniq = w.get("uniqueName") if isinstance(w, dict) else None
        mr = w.get("masteryReq") if isinstance(w, dict) else None
        cat = w.get("category") if isinstance(w, dict) else None

        name_s = await _localized_title(
            public_export_client=public_export_client,
            name=name,
            unique_name=uniq,
            language="zh",
        )
        uniq_s = str(uniq) if isinstance(uniq, str) and uniq else ""
        mr_s = f"MR{mr}" if isinstance(mr, int) else None
        cat_s = str(cat) if isinstance(cat, str) and cat else None
        right = " ".join([x for x in [mr_s, cat_s] if x]) or None

        rows.append(WorldstateRow(title=name_s, subtitle=uniq_s or None, right=right))
        suffix = f" | {right}" if right else ""
        extra = f" | {uniq_s}" if uniq_s else ""
        lines.append(f"- {name_s}{suffix}{extra}")

    rendered = await render_worldstate_rows_image_to_file(
        title="武器",
        header_lines=["数据源：PublicExport", f"查询：{query}"],
        rows=rows,
        accent=(245, 158, 11, 255),
    )
    if rendered:
        return event.image_result(rendered.path)

    return event.plain_result("\n".join(lines))


async def cmd_mod(
    *,
    event: AstrMessageEvent,
    query: str,
    public_export_client: PublicExportClient,
):
    query = str(query).strip()
    if not query:
        return event.plain_result("用法：/MOD <名称> 例如：/MOD 过载")

    items = await public_export_client.search_mod(query, language="zh", limit=10)
    if not items:
        return event.plain_result(f"未找到 MOD：{query}")

    rows: list[WorldstateRow] = []
    lines: list[str] = [f"MOD 搜索：{query}（展示前{len(items)}条）"]
    for m in items[:10]:
        if not isinstance(m, dict):
            continue
        name = m.get("name")
        uniq = m.get("uniqueName")
        rarity = m.get("rarity")
        polarity = m.get("polarity")
        fusion_limit = m.get("fusionLimit")
        mod_type = m.get("modType")

        name_s = await _localized_title(
            public_export_client=public_export_client,
            name=name,
            unique_name=uniq,
            language="zh",
        )
        uniq_s = str(uniq) if isinstance(uniq, str) and uniq else ""
        sub_parts: list[str] = []
        if isinstance(mod_type, str) and mod_type.strip():
            sub_parts.append(mod_type.strip())
        if isinstance(rarity, str) and rarity.strip():
            sub_parts.append(rarity.strip())
        if isinstance(polarity, str) and polarity.strip():
            sub_parts.append(f"极性:{polarity.strip()}")
        subtitle = " | ".join(sub_parts) or (uniq_s or None)

        right_parts: list[str] = []
        if isinstance(fusion_limit, int):
            right_parts.append(f"满级:{fusion_limit}")
        right = " ".join(right_parts) or None

        rows.append(WorldstateRow(title=name_s, subtitle=subtitle, right=right))
        suffix = f" | {right}" if right else ""
        extra = f" | {subtitle}" if subtitle else ""
        lines.append(f"- {name_s}{suffix}{extra}")

    rendered = await render_worldstate_rows_image_to_file(
        title="MOD",
        header_lines=["数据源：PublicExport", f"查询：{query}"],
        rows=rows,
        accent=(168, 85, 247, 255),
    )
    if rendered:
        return event.image_result(rendered.path)

    return event.plain_result("\n".join(lines))
