from __future__ import annotations

import re

from astrbot.api.event import AstrMessageEvent

from ..clients.worldstate_client import WarframeWorldstateClient
from ..constants import WORLDSTATE_PLATFORM_ALIASES
from ..helpers import split_tokens
from ..renderers.worldstate_render import (
    WorldstateRow,
    render_worldstate_rows_image_to_file,
)
from ..services.fissures import render_fissures_image, render_fissures_text
from ..services.worldstate_views import render_worldstate_cycle
from ..utils.platforms import eta_key, worldstate_platform_from_tokens


def _normalize_compact(text: str) -> str:
    return re.sub(r"\s+", "", str(text or "").strip().lower())


def _parse_fissure_kind(tokens: list[str]) -> str:
    fissure_kind = "普通"  # 普通/钢铁/九重天
    for t in tokens:
        t2 = str(t).strip().lower()
        if t2 in {"九重天", "九重", "风暴", "storm"}:
            fissure_kind = "九重天"
            continue
        if t2 in {"钢铁", "钢", "sp", "steel"}:
            fissure_kind = "钢铁"
            continue
        if t2 in {"普通", "正常", "normal"}:
            fissure_kind = "普通"
            continue
    return fissure_kind


async def cmd_sortie(
    *,
    event: AstrMessageEvent,
    raw_args: str,
    worldstate_client: WarframeWorldstateClient,
):
    tokens = split_tokens(str(raw_args))
    platform_norm = worldstate_platform_from_tokens(tokens)

    info = await worldstate_client.fetch_sortie(platform=platform_norm, language="zh")
    if not info:
        return event.plain_result("未获取到突击信息（可能是网络限制或接口不可达）。")

    header_lines: list[str] = [f"平台：{platform_norm}"]
    if info.boss:
        header_lines.append(f"Boss：{info.boss}")
    if info.faction:
        header_lines.append(f"阵营：{info.faction}")

    rows: list[WorldstateRow] = []
    if info.stages:
        for idx, stage in enumerate(info.stages, start=1):
            mod = f" | {stage.modifier}" if stage.modifier else ""
            rows.append(
                WorldstateRow(
                    title=f"{idx}. {stage.mission_type}",
                    subtitle=f"{stage.node}{mod}",
                    right=f"剩余{info.eta}",
                )
            )
    else:
        rows.append(WorldstateRow(title="(暂无任务详情)", right=f"剩余{info.eta}"))

    rendered = await render_worldstate_rows_image_to_file(
        title="突击",
        header_lines=header_lines,
        rows=rows,
        accent=(59, 130, 246, 255),
    )
    if rendered:
        return event.image_result(rendered.path)

    head_parts: list[str] = [f"突击（{platform_norm}）"]
    if info.boss:
        head_parts.append(str(info.boss))
    if info.faction:
        head_parts.append(str(info.faction))
    head_parts.append(f"剩余{info.eta}")
    lines: list[str] = [" ".join(head_parts)]

    if not info.stages:
        lines.append("(暂无任务详情)")
        return event.plain_result("\n".join(lines))

    for idx, stage in enumerate(info.stages, start=1):
        mod = f" | {stage.modifier}" if stage.modifier else ""
        lines.append(f"{idx}. {stage.mission_type} - {stage.node}{mod}")

    return event.plain_result("\n".join(lines))


async def cmd_archon_hunt(
    *,
    event: AstrMessageEvent,
    raw_args: str,
    worldstate_client: WarframeWorldstateClient,
):
    tokens = split_tokens(str(raw_args))
    platform_norm = worldstate_platform_from_tokens(tokens)

    info = await worldstate_client.fetch_archon_hunt(
        platform=platform_norm, language="zh"
    )
    if not info:
        return event.plain_result(
            "未获取到执行官猎杀信息（可能是网络限制或接口不可达）。"
        )

    header_lines: list[str] = [f"平台：{platform_norm}"]
    if info.boss:
        header_lines.append(f"Boss：{info.boss}")
    if info.faction:
        header_lines.append(f"阵营：{info.faction}")

    rows: list[WorldstateRow] = []
    if info.stages:
        for idx, stage in enumerate(info.stages, start=1):
            mod = f" | {stage.modifier}" if stage.modifier else ""
            rows.append(
                WorldstateRow(
                    title=f"{idx}. {stage.mission_type}",
                    subtitle=f"{stage.node}{mod}",
                    right=f"剩余{info.eta}",
                )
            )
    else:
        rows.append(WorldstateRow(title="(暂无任务详情)", right=f"剩余{info.eta}"))

    rendered = await render_worldstate_rows_image_to_file(
        title="执行官猎杀",
        header_lines=header_lines,
        rows=rows,
        accent=(239, 68, 68, 255),
    )
    if rendered:
        return event.image_result(rendered.path)

    head_parts: list[str] = [f"执行官猎杀（{platform_norm}）"]
    if info.boss:
        head_parts.append(str(info.boss))
    if info.faction:
        head_parts.append(str(info.faction))
    head_parts.append(f"剩余{info.eta}")
    lines: list[str] = [" ".join(head_parts)]

    if not info.stages:
        lines.append("(暂无任务详情)")
        return event.plain_result("\n".join(lines))

    for idx, stage in enumerate(info.stages, start=1):
        mod = f" | {stage.modifier}" if stage.modifier else ""
        lines.append(f"{idx}. {stage.mission_type} - {stage.node}{mod}")
    return event.plain_result("\n".join(lines))


async def cmd_steel_path_reward(
    *,
    event: AstrMessageEvent,
    raw_args: str,
    worldstate_client: WarframeWorldstateClient,
):
    tokens = split_tokens(str(raw_args))
    platform_norm = worldstate_platform_from_tokens(tokens)

    info = await worldstate_client.fetch_steel_path_reward(
        platform=platform_norm,
        language="zh",
    )
    if not info:
        return event.plain_result(
            "未获取到钢铁奖励信息（可能是网络限制或接口不可达）。"
        )

    reward = info.reward or "(未知奖励)"
    rows = [
        WorldstateRow(
            title=f"当前奖励：{reward}",
            subtitle=None,
            right=f"剩余{info.eta}",
        )
    ]
    rendered = await render_worldstate_rows_image_to_file(
        title="钢铁奖励",
        header_lines=[f"平台：{platform_norm}"],
        rows=rows,
        accent=(100, 116, 139, 255),
    )
    if rendered:
        return event.image_result(rendered.path)

    return event.plain_result(
        f"钢铁奖励（{platform_norm}）\n- 当前：{reward}\n- 剩余{info.eta}"
    )


async def cmd_alerts(
    *,
    event: AstrMessageEvent,
    raw_args: str,
    worldstate_client: WarframeWorldstateClient,
):
    tokens = split_tokens(str(raw_args))
    platform_norm = worldstate_platform_from_tokens(tokens)

    alerts = await worldstate_client.fetch_alerts(platform=platform_norm, language="zh")
    if alerts is None:
        return event.plain_result("未获取到警报信息（可能是网络限制或接口不可达）。")
    if not alerts:
        return event.plain_result(f"当前无警报（{platform_norm}）。")

    rows: list[WorldstateRow] = []
    for a in alerts[:20]:
        lvl = ""
        if a.min_level is not None and a.max_level is not None:
            lvl = f" Lv{a.min_level}-{a.max_level}"
        sub_parts: list[str] = []
        if a.faction:
            sub_parts.append(str(a.faction))
        if a.reward:
            sub_parts.append(str(a.reward))
        subtitle = " | ".join(sub_parts) if sub_parts else None
        rows.append(
            WorldstateRow(
                title=f"{a.mission_type} - {a.node}{lvl}",
                subtitle=subtitle,
                right=f"剩余{a.eta}",
            )
        )

    rendered = await render_worldstate_rows_image_to_file(
        title="警报",
        header_lines=[
            f"平台：{platform_norm}",
            f"共{len(alerts)}条（展示前{min(20, len(alerts))}条）",
        ],
        rows=rows,
        accent=(245, 158, 11, 255),
    )
    if rendered:
        return event.image_result(rendered.path)

    lines: list[str] = [f"警报（{platform_norm}）共{len(alerts)}条："]
    for a in alerts:
        lvl = ""
        if a.min_level is not None and a.max_level is not None:
            lvl = f" Lv{a.min_level}-{a.max_level}"
        rew = f" | {a.reward}" if a.reward else ""
        fac = f" | {a.faction}" if a.faction else ""
        lines.append(f"- {a.mission_type} {a.node}{lvl} | 剩余{a.eta}{fac}{rew}")

    return event.plain_result("\n".join(lines))


async def cmd_fissures(
    *,
    event: AstrMessageEvent,
    raw_args: str,
    worldstate_client: WarframeWorldstateClient,
):
    tokens = split_tokens(str(raw_args))
    platform_norm = worldstate_platform_from_tokens(tokens)
    fissure_kind = _parse_fissure_kind(tokens)

    rendered = await render_fissures_image(
        worldstate_client=worldstate_client,
        platform_norm=platform_norm,
        fissure_kind=fissure_kind,
    )
    if rendered:
        return event.image_result(rendered.path)

    text = await render_fissures_text(
        worldstate_client=worldstate_client,
        platform_norm=platform_norm,
        fissure_kind=fissure_kind,
    )
    return event.plain_result(text)


async def cmd_fissures_kind(
    *,
    event: AstrMessageEvent,
    raw_args: str,
    worldstate_client: WarframeWorldstateClient,
    fissure_kind: str,
):
    tokens = split_tokens(str(raw_args))
    platform_norm = worldstate_platform_from_tokens(tokens)

    rendered = await render_fissures_image(
        worldstate_client=worldstate_client,
        platform_norm=platform_norm,
        fissure_kind=fissure_kind,
    )
    if rendered:
        return event.image_result(rendered.path)

    text = await render_fissures_text(
        worldstate_client=worldstate_client,
        platform_norm=platform_norm,
        fissure_kind=fissure_kind,
    )
    return event.plain_result(text)


async def cmd_void_trader(
    *,
    event: AstrMessageEvent,
    raw_args: str,
    worldstate_client: WarframeWorldstateClient,
):
    tokens = split_tokens(str(raw_args))
    platform_norm = worldstate_platform_from_tokens(tokens)

    info = await worldstate_client.fetch_void_trader(
        platform=platform_norm, language="zh"
    )
    if info is None:
        return event.plain_result("未获取到奸商信息（可能是网络限制或接口不可达）。")

    if not info.active:
        return event.plain_result(f"奸商未到访（{platform_norm}），预计{info.eta}。")

    rows: list[WorldstateRow] = []
    if info.inventory:
        for it in info.inventory[:30]:
            item_name = (it.item or "").strip()
            mapped_name = await worldstate_client.localize_item_display_name(
                item_name,
                language="zh",
            )
            title_name = mapped_name or item_name or "(未知物品)"

            price: list[str] = []
            if it.ducats is not None:
                price.append(f"{it.ducats}杜卡德")
            if it.credits is not None:
                price.append(f"{it.credits}现金")
            rows.append(
                WorldstateRow(
                    title=title_name,
                    right=" / ".join(price) if price else None,
                )
            )
    else:
        rows.append(WorldstateRow(title="(未返回商品清单)"))

    rendered = await render_worldstate_rows_image_to_file(
        title="奸商",
        header_lines=[
            f"平台：{platform_norm}",
            f"地点：{info.location or '未知'}",
            f"剩余：{info.eta}",
        ],
        rows=rows,
        accent=(14, 165, 233, 255),
    )
    if rendered:
        return event.image_result(rendered.path)

    lines: list[str] = [
        f"奸商（{platform_norm}）已到访：",
        f"- 地点：{info.location or '未知'}",
        f"- 剩余：{info.eta}",
    ]
    if not info.inventory:
        lines.append("- (未返回商品清单)")
        return event.plain_result("\n".join(lines))

    for it in info.inventory[:30]:
        item_name = (it.item or "").strip()
        mapped_name = await worldstate_client.localize_item_display_name(
            item_name,
            language="zh",
        )
        title_name = mapped_name or item_name or "(未知物品)"

        price: list[str] = []
        if it.ducats is not None:
            price.append(f"{it.ducats}杜卡德")
        if it.credits is not None:
            price.append(f"{it.credits}现金")
        p = " / ".join(price)
        lines.append(f"- {title_name}{(' | ' + p) if p else ''}")

    return event.plain_result("\n".join(lines))


async def cmd_arbitration(
    *,
    event: AstrMessageEvent,
    raw_args: str,
    worldstate_client: WarframeWorldstateClient,
):
    tokens = split_tokens(str(raw_args))
    platform_norm = worldstate_platform_from_tokens(tokens)

    info = await worldstate_client.fetch_arbitration(
        platform=platform_norm, language="zh"
    )
    if info is None:
        return event.plain_result("未获取到仲裁信息（可能是网络限制或接口不可达）。")

    rendered = await render_worldstate_rows_image_to_file(
        title="仲裁",
        header_lines=[f"平台：{platform_norm}"],
        rows=[
            WorldstateRow(
                title=f"{info.mission_type} - {info.node}",
                subtitle=info.enemy or None,
                right=f"剩余{info.eta}",
            )
        ],
        accent=(100, 116, 139, 255),
    )
    if rendered:
        return event.image_result(rendered.path)

    enemy = f" | {info.enemy}" if info.enemy else ""
    return event.plain_result(
        f"仲裁（{platform_norm}）\n- {info.mission_type} - {info.node}{enemy}\n- 剩余{info.eta}",
    )


async def cmd_nightwave(
    *,
    event: AstrMessageEvent,
    raw_args: str,
    worldstate_client: WarframeWorldstateClient,
):
    tokens = split_tokens(str(raw_args))
    platform_norm = worldstate_platform_from_tokens(tokens)

    info = await worldstate_client.fetch_nightwave(
        platform=platform_norm, language="zh"
    )
    if info is None:
        return event.plain_result("未获取到电波信息（可能是网络限制或接口不可达）。")

    title = f"电波（{platform_norm}）"
    if info.season is not None:
        title += f" S{info.season}"
    if info.phase is not None:
        title += f" P{info.phase}"

    lines: list[str] = [f"{title}\n- 剩余{info.eta}"]
    if not info.active_challenges:
        lines.append("- (未返回挑战列表)")
        rendered = await render_worldstate_rows_image_to_file(
            title="电波",
            header_lines=[f"平台：{platform_norm}", f"剩余：{info.eta}"],
            rows=[WorldstateRow(title="(未返回挑战列表)")],
            accent=(124, 58, 237, 255),
        )
        if rendered:
            return event.image_result(rendered.path)
        return event.plain_result("\n".join(lines))

    rows: list[WorldstateRow] = []
    for c in info.active_challenges[:12]:
        kind = "日常" if c.is_daily else "周常"
        rep = f"+{c.reputation}" if c.reputation is not None else ""
        rows.append(
            WorldstateRow(
                title=c.title,
                subtitle=rep or None,
                right=f"剩余{c.eta}",
                tag=kind,
            )
        )

    rendered = await render_worldstate_rows_image_to_file(
        title="电波",
        header_lines=[f"平台：{platform_norm}", f"剩余：{info.eta}"],
        rows=rows,
        accent=(124, 58, 237, 255),
    )
    if rendered:
        return event.image_result(rendered.path)

    for c in info.active_challenges[:12]:
        kind = "日常" if c.is_daily else "周常"
        rep = f" +{c.reputation}" if c.reputation is not None else ""
        lines.append(f"- [{kind}] {c.title}{rep} | 剩余{c.eta}")

    return event.plain_result("\n".join(lines))


async def cmd_plains(
    *,
    event: AstrMessageEvent,
    raw_args: str,
    worldstate_client: WarframeWorldstateClient,
):
    raw_tokens = split_tokens(str(raw_args))
    platform_norm = worldstate_platform_from_tokens(raw_tokens)

    platform_tokens = {
        _normalize_compact(k) for k in WORLDSTATE_PLATFORM_ALIASES.keys() if k
    } | {_normalize_compact(v) for v in WORLDSTATE_PLATFORM_ALIASES.values() if v}

    query_tokens = [
        t for t in raw_tokens if _normalize_compact(t) not in platform_tokens
    ]
    query = "".join([str(t).strip() for t in query_tokens if str(t).strip()])
    qn = _normalize_compact(query)

    plains: list[tuple[str, set[str]]] = [
        ("夜灵平原", {"夜灵平原", "希图斯", "cetus", "poe"}),
        (
            "奥布山谷",
            {
                "奥布山谷",
                "金星平原",
                "福尔图娜",
                "vallis",
                "orb",
                "orbvallis",
                "fortuna",
            },
        ),
        ("魔胎之境", {"魔胎之境", "魔胎", "cambion"}),
        ("双衍王境", {"双衍王境", "双衍王镜", "双衍", "duviri"}),
    ]

    def match_plain(name: str, aliases: set[str], q: str) -> bool:
        if not q:
            return False
        if q in _normalize_compact(name):
            return True
        for a in aliases:
            if q in _normalize_compact(a):
                return True
        return False

    if qn:
        matched = [p for p in plains if match_plain(p[0], p[1], qn)]
        if not matched:
            return event.plain_result(
                "未识别平原名称。用法：/平原 [希图斯/福尔图娜/魔胎/双衍] [平台]；不带参数列出全部平原状态。"
            )
        if len(matched) > 1:
            names = "、".join([m[0] for m in matched])
            return event.plain_result(
                f"匹配到多个平原：{names}。请把参数写得更具体一些。"
            )

        plain_name = matched[0][0]
        if plain_name == "夜灵平原":
            info = await worldstate_client.fetch_cetus_cycle(
                platform=platform_norm, language="zh"
            )
            if info is None:
                return event.plain_result(
                    "未获取到夜灵平原信息（可能是网络限制或接口不可达）。"
                )
            state_cn = info.state or (
                "白天" if info.is_day else ("夜晚" if info.is_day is False else "未知")
            )
            left = info.time_left or info.eta
            return await render_worldstate_cycle(
                event,
                title="夜灵平原",
                platform_norm=platform_norm,
                state_cn=state_cn,
                left=left,
                start_time=getattr(info, "start_time", None),
                end_time=getattr(info, "end_time", None),
                accent=(20, 184, 166, 255),
                plain_prefix="夜灵平原",
            )

        if plain_name == "双衍王境":
            cycle = await worldstate_client.fetch_duviri_cycle(
                platform=platform_norm, language="zh"
            )
            if cycle is None:
                return event.plain_result(
                    "未获取到双衍王境信息（可能是网络限制或接口不可达）。"
                )

            circuit = await worldstate_client.fetch_duviri_circuit_rewards(
                platform=platform_norm, language="zh"
            )

            state = (cycle.state or "未知").strip()
            left = cycle.time_left or cycle.eta
            header_lines = [f"平台：{platform_norm}", f"情绪：{state} | 剩余{left}"]
            if circuit is not None:
                header_lines.append(f"轮回重置：{circuit.eta}")

            normal = (
                "、".join(list(circuit.normal_choices))
                if circuit is not None and circuit.normal_choices
                else "(未返回)"
            )
            steel = (
                "、".join(list(circuit.steel_choices))
                if circuit is not None and circuit.steel_choices
                else "(未返回)"
            )

            rows: list[WorldstateRow] = [
                WorldstateRow(title="普通轮回", subtitle=normal, right=None),
                WorldstateRow(title="钢铁轮回", subtitle=steel, right=None),
            ]
            rendered = await render_worldstate_rows_image_to_file(
                title="双衍王境",
                header_lines=header_lines,
                rows=rows,
                accent=(20, 184, 166, 255),
            )
            if rendered:
                return event.image_result(rendered.path)

            lines = [f"双衍王境（{platform_norm}）", f"情绪：{state} | 剩余{left}"]
            if circuit is not None:
                lines.append(f"轮回重置：{circuit.eta}")
            lines.append(f"- 普通轮回：{normal}")
            lines.append(f"- 钢铁轮回：{steel}")
            return event.plain_result("\n".join(lines))

        if plain_name == "奥布山谷":
            info = await worldstate_client.fetch_vallis_cycle(
                platform=platform_norm, language="zh"
            )
            if info is None:
                return event.plain_result(
                    "未获取到奥布山谷信息（可能是网络限制或接口不可达）。"
                )
            state_cn = info.state or (
                "温暖"
                if info.is_warm
                else ("寒冷" if info.is_warm is False else "未知")
            )
            left = info.time_left or info.eta
            return await render_worldstate_cycle(
                event,
                title="奥布山谷",
                platform_norm=platform_norm,
                state_cn=state_cn,
                left=left,
                start_time=getattr(info, "start_time", None),
                end_time=getattr(info, "end_time", None),
                accent=(20, 184, 166, 255),
                plain_prefix="奥布山谷",
            )

        info = await worldstate_client.fetch_cambion_cycle(
            platform=platform_norm, language="zh"
        )
        if info is None:
            return event.plain_result(
                "未获取到魔胎之境信息（可能是网络限制或接口不可达）。"
            )
        state_cn = info.active or info.state or "未知"
        left = info.time_left or info.eta
        return await render_worldstate_cycle(
            event,
            title="魔胎之境",
            platform_norm=platform_norm,
            state_cn=state_cn,
            left=left,
            start_time=getattr(info, "start_time", None),
            end_time=getattr(info, "end_time", None),
            accent=(20, 184, 166, 255),
            plain_prefix="魔胎之境",
        )

    # No query: list all plains.
    try:
        cetus = await worldstate_client.fetch_cetus_cycle(
            platform=platform_norm, language="zh"
        )
    except Exception:
        cetus = None

    try:
        vallis = await worldstate_client.fetch_vallis_cycle(
            platform=platform_norm, language="zh"
        )
    except Exception:
        vallis = None

    try:
        cambion = await worldstate_client.fetch_cambion_cycle(
            platform=platform_norm, language="zh"
        )
    except Exception:
        cambion = None

    try:
        duviri = await worldstate_client.fetch_duviri_cycle(
            platform=platform_norm, language="zh"
        )
    except Exception:
        duviri = None

    rows: list[WorldstateRow] = []

    if cetus is None:
        rows.append(WorldstateRow(title="夜灵平原", subtitle="(获取失败)", right=None))
    else:
        state_cn = cetus.state or (
            "白天" if cetus.is_day else ("夜晚" if cetus.is_day is False else "未知")
        )
        left = cetus.time_left or cetus.eta
        rows.append(
            WorldstateRow(
                title="夜灵平原", subtitle=f"当前：{state_cn}", right=f"剩余{left}"
            )
        )

    if vallis is None:
        rows.append(WorldstateRow(title="奥布山谷", subtitle="(获取失败)", right=None))
    else:
        state_cn = vallis.state or (
            "温暖"
            if vallis.is_warm
            else ("寒冷" if vallis.is_warm is False else "未知")
        )
        left = vallis.time_left or vallis.eta
        rows.append(
            WorldstateRow(
                title="奥布山谷", subtitle=f"当前：{state_cn}", right=f"剩余{left}"
            )
        )

    if cambion is None:
        rows.append(WorldstateRow(title="魔胎之境", subtitle="(获取失败)", right=None))
    else:
        state_cn = cambion.active or cambion.state or "未知"
        left = cambion.time_left or cambion.eta
        rows.append(
            WorldstateRow(
                title="魔胎之境", subtitle=f"当前：{state_cn}", right=f"剩余{left}"
            )
        )

    if duviri is None:
        rows.append(WorldstateRow(title="双衍王境", subtitle="(获取失败)", right=None))
    else:
        state_cn = (duviri.state or "未知").strip()
        left = duviri.time_left or duviri.eta
        rows.append(
            WorldstateRow(
                title="双衍王境", subtitle=f"当前：{state_cn}", right=f"剩余{left}"
            )
        )

    rendered = await render_worldstate_rows_image_to_file(
        title="平原状态",
        header_lines=[f"平台：{platform_norm}"],
        rows=rows,
        accent=(20, 184, 166, 255),
    )
    if rendered:
        return event.image_result(rendered.path)

    lines = [f"平原状态（{platform_norm}）："]
    for r in rows:
        right = f" {r.right}" if r.right else ""
        sub = f" {r.subtitle}" if r.subtitle else ""
        lines.append(f"- {r.title}{sub}{right}")
    return event.plain_result("\n".join(lines))


async def cmd_cycle(
    *,
    event: AstrMessageEvent,
    raw_args: str,
    worldstate_client: WarframeWorldstateClient,
    cycle: str,
):
    tokens = split_tokens(str(raw_args))
    platform_norm = worldstate_platform_from_tokens(tokens)

    if cycle == "cetus":
        info = await worldstate_client.fetch_cetus_cycle(
            platform=platform_norm, language="zh"
        )
        if info is None:
            return event.plain_result(
                "未获取到夜灵平原信息（可能是网络限制或接口不可达）。"
            )
        state_cn = info.state or (
            "白天" if info.is_day else ("夜晚" if info.is_day is False else "未知")
        )
        left = info.time_left or info.eta
        return await render_worldstate_cycle(
            event,
            title="夜灵平原",
            platform_norm=platform_norm,
            state_cn=state_cn,
            left=left,
            start_time=getattr(info, "start_time", None),
            end_time=getattr(info, "end_time", None),
            accent=(20, 184, 166, 255),
            plain_prefix="夜灵平原",
        )

    if cycle == "cambion":
        info = await worldstate_client.fetch_cambion_cycle(
            platform=platform_norm, language="zh"
        )
        if info is None:
            return event.plain_result(
                "未获取到魔胎之境信息（可能是网络限制或接口不可达）。"
            )
        state_cn = info.active or info.state or "未知"
        left = info.time_left or info.eta
        return await render_worldstate_cycle(
            event,
            title="魔胎之境",
            platform_norm=platform_norm,
            state_cn=state_cn,
            left=left,
            start_time=getattr(info, "start_time", None),
            end_time=getattr(info, "end_time", None),
            accent=(20, 184, 166, 255),
            plain_prefix="魔胎之境",
        )

    if cycle == "earth":
        info = await worldstate_client.fetch_earth_cycle(
            platform=platform_norm, language="zh"
        )
        if info is None:
            return event.plain_result(
                "未获取到地球循环信息（可能是网络限制或接口不可达）。"
            )
        state_cn = info.state or (
            "白天" if info.is_day else ("夜晚" if info.is_day is False else "未知")
        )
        left = info.time_left or info.eta
        return await render_worldstate_cycle(
            event,
            title="地球昼夜",
            platform_norm=platform_norm,
            state_cn=state_cn,
            left=left,
            start_time=getattr(info, "start_time", None),
            end_time=getattr(info, "end_time", None),
            accent=(20, 184, 166, 255),
            plain_prefix="地球",
        )

    if cycle == "vallis":
        info = await worldstate_client.fetch_vallis_cycle(
            platform=platform_norm, language="zh"
        )
        if info is None:
            return event.plain_result(
                "未获取到奥布山谷信息（可能是网络限制或接口不可达）。"
            )
        state_cn = info.state or (
            "温暖" if info.is_warm else ("寒冷" if info.is_warm is False else "未知")
        )
        left = info.time_left or info.eta
        return await render_worldstate_cycle(
            event,
            title="奥布山谷",
            platform_norm=platform_norm,
            state_cn=state_cn,
            left=left,
            start_time=getattr(info, "start_time", None),
            end_time=getattr(info, "end_time", None),
            accent=(20, 184, 166, 255),
            plain_prefix="奥布山谷",
        )

    if cycle == "duviri":
        info = await worldstate_client.fetch_duviri_cycle(
            platform=platform_norm, language="zh"
        )
        if info is None:
            return event.plain_result(
                "未获取到双衍王境信息（可能是网络限制或接口不可达）。"
            )

        circuit = await worldstate_client.fetch_duviri_circuit_rewards(
            platform=platform_norm, language="zh"
        )

        state = (info.state or "未知").strip()
        left = info.time_left or info.eta

        header_lines = [f"平台：{platform_norm}", f"情绪：{state} | 剩余{left}"]
        if circuit is not None:
            header_lines.append(f"轮回重置：{circuit.eta}")

        normal = (
            "、".join(list(circuit.normal_choices))
            if circuit is not None and circuit.normal_choices
            else "(未返回)"
        )
        steel = (
            "、".join(list(circuit.steel_choices))
            if circuit is not None and circuit.steel_choices
            else "(未返回)"
        )
        rows: list[WorldstateRow] = [
            WorldstateRow(title="普通轮回", subtitle=normal, right=None),
            WorldstateRow(title="钢铁轮回", subtitle=steel, right=None),
        ]

        rendered = await render_worldstate_rows_image_to_file(
            title="双衍王境",
            header_lines=header_lines,
            rows=rows,
            accent=(20, 184, 166, 255),
        )
        if rendered:
            return event.image_result(rendered.path)

        lines = [f"双衍王境（{platform_norm}）", f"情绪：{state} | 剩余{left}"]
        if circuit is not None:
            lines.append(f"轮回重置：{circuit.eta}")
        lines.append(f"- 普通轮回：{normal}")
        lines.append(f"- 钢铁轮回：{steel}")
        return event.plain_result("\n".join(lines))

    return event.plain_result("未支持的循环类型。")


async def cmd_duviri_circuit_rewards(
    *,
    event: AstrMessageEvent,
    raw_args: str,
    worldstate_client: WarframeWorldstateClient,
):
    tokens = split_tokens(str(raw_args))
    platform_norm = worldstate_platform_from_tokens(tokens)

    info = await worldstate_client.fetch_duviri_circuit_rewards(
        platform=platform_norm, language="zh"
    )
    if info is None:
        return event.plain_result(
            "未获取到轮回奖励信息（可能是网络限制或接口不可达）。"
        )

    normal = "、".join(list(info.normal_choices)) or "(未返回)"
    steel = "、".join(list(info.steel_choices)) or "(未返回)"

    rows = [
        WorldstateRow(title="普通奖励", subtitle=normal, right=None),
        WorldstateRow(title="钢铁奖励", subtitle=steel, right=None),
    ]
    rendered = await render_worldstate_rows_image_to_file(
        title="轮回奖励",
        header_lines=[f"平台：{platform_norm}", f"轮回重置：{info.eta}"],
        rows=rows,
        accent=(20, 184, 166, 255),
    )
    if rendered:
        return event.image_result(rendered.path)

    return event.plain_result(
        "\n".join(
            [
                f"轮回奖励（{platform_norm}）",
                f"轮回重置：{info.eta}",
                f"- 普通奖励：{normal}",
                f"- 钢铁奖励：{steel}",
            ]
        )
    )


async def cmd_invasions(
    *,
    event: AstrMessageEvent,
    raw_args: str,
    worldstate_client: WarframeWorldstateClient,
):
    tokens = split_tokens(str(raw_args))
    platform_norm = worldstate_platform_from_tokens(tokens)

    limit = 10
    for t in tokens:
        if str(t).isdigit():
            limit = int(str(t))
            break
    limit = max(1, min(limit, 20))

    inv = await worldstate_client.fetch_invasions(platform=platform_norm, language="zh")
    if inv is None:
        return event.plain_result("未获取到入侵信息（可能是网络限制或接口不可达）。")
    if not inv:
        return event.plain_result(f"当前无入侵（{platform_norm}）。")

    inv.sort(key=lambda x: (eta_key(x.eta), -(x.completion or 0.0)))

    rows: list[WorldstateRow] = []
    for i in inv[:limit]:
        sides = " vs ".join([x for x in [i.attacker, i.defender] if x]) or "未知阵营"
        comp = f"进度{i.completion:.0f}%" if i.completion is not None else ""
        subtitle_parts = [p for p in [comp, i.reward] if p]
        rows.append(
            WorldstateRow(
                title=f"{i.node} | {sides}",
                subtitle=" | ".join(subtitle_parts) if subtitle_parts else None,
                right=f"剩余{i.eta}",
            )
        )

    rendered = await render_worldstate_rows_image_to_file(
        title="入侵",
        header_lines=[f"平台：{platform_norm}", f"展示前{min(limit, len(inv))}条"],
        rows=rows,
        accent=(249, 115, 22, 255),
    )
    if rendered:
        return event.image_result(rendered.path)

    lines: list[str] = [f"入侵（{platform_norm}）前{min(limit, len(inv))}条："]
    for i in inv[:limit]:
        sides = " vs ".join([x for x in [i.attacker, i.defender] if x]) or "未知阵营"
        comp = f" | 进度{i.completion:.0f}%" if i.completion is not None else ""
        rew = f" | {i.reward}" if i.reward else ""
        lines.append(f"- {i.node} | {sides} | 剩余{i.eta}{comp}{rew}")

    return event.plain_result("\n".join(lines))


async def cmd_syndicates(
    *,
    event: AstrMessageEvent,
    raw_args: str,
    worldstate_client: WarframeWorldstateClient,
):
    tokens = split_tokens(str(raw_args))
    platform_norm = worldstate_platform_from_tokens(tokens)

    def is_platform_token(tok: str) -> bool:
        t = (tok or "").strip().lower()
        return (
            t in WORLDSTATE_PLATFORM_ALIASES
            or t in WORLDSTATE_PLATFORM_ALIASES.values()
        )

    name_tokens: list[str] = []
    for t in tokens:
        s = str(t).strip()
        if not s:
            continue
        if is_platform_token(s):
            continue
        name_tokens.append(s)

    syndicate_query = " ".join(name_tokens).strip()

    syndicates = await worldstate_client.fetch_syndicates(
        platform=platform_norm, language="zh"
    )
    if syndicates is None:
        return event.plain_result(
            "未获取到集团任务信息（可能是网络限制或接口不可达）。"
        )
    if not syndicates:
        return event.plain_result(f"当前无集团任务（{platform_norm}）。")

    def norm(s: str) -> str:
        return re.sub(r"\s+", "", (s or "").strip().lower())

    if syndicate_query:
        qn = norm(syndicate_query)
        matched = [s for s in syndicates if qn and qn in norm(s.name)]

        if not matched:
            names = "、".join([s.name for s in syndicates])
            return event.plain_result(
                f"未找到集团：{syndicate_query}（{platform_norm}）。可用集团：{names}"
            )

        if len(matched) > 1:
            names = "、".join([s.name for s in matched])
            return event.plain_result(
                f"匹配到多个集团：{names}。请更精确一些（例如输入完整名称）。"
            )

        s = matched[0]
        syndicate_jobs = list(s.jobs or ())
        if not syndicate_jobs:
            return event.plain_result(f"{s.name}（{platform_norm}）当前无任务。")

        rows: list[WorldstateRow] = []
        for j in syndicate_jobs[:18]:
            node = j.node or "?"
            mtype = j.mission_type or "?"
            rows.append(
                WorldstateRow(
                    title=f"{mtype} - {node}",
                    right=f"剩余{j.eta}" if j.eta else None,
                )
            )

        rendered = await render_worldstate_rows_image_to_file(
            title=f"集团 {s.name}",
            header_lines=[
                f"平台：{platform_norm}",
                f"剩余：{s.eta}",
                f"共{len(syndicate_jobs)}条（展示前{min(18, len(syndicate_jobs))}条）",
            ],
            rows=rows,
            accent=(16, 185, 129, 255),
        )
        if rendered:
            return event.image_result(rendered.path)

        lines: list[str] = [
            f"集团 {s.name}（{platform_norm}）剩余{s.eta}：共{len(syndicate_jobs)}条"
        ]
        for j in syndicate_jobs:
            node = j.node or "?"
            mtype = j.mission_type or "?"
            lines.append(f"- {mtype} - {node} | 剩余{j.eta}")
        return event.plain_result("\n".join(lines))

    rows: list[WorldstateRow] = []
    for s in syndicates[:10]:
        job_summaries: list[str] = []
        for j in s.jobs[:3]:
            node = j.node or "?"
            mtype = j.mission_type or "?"
            job_summaries.append(f"{mtype}-{node}")
        subtitle = " | ".join(job_summaries) if job_summaries else None
        rows.append(
            WorldstateRow(title=s.name, subtitle=subtitle, right=f"剩余{s.eta}")
        )

    rendered = await render_worldstate_rows_image_to_file(
        title="集团任务",
        header_lines=[
            f"平台：{platform_norm}",
            f"共{len(syndicates)}组（展示前{min(10, len(syndicates))}组）",
        ],
        rows=rows,
        accent=(16, 185, 129, 255),
    )
    if rendered:
        return event.image_result(rendered.path)

    lines: list[str] = [f"集团任务（{platform_norm}）共{len(syndicates)}组："]
    for s in syndicates:
        lines.append(f"- {s.name} | 剩余{s.eta}")
        if not s.jobs:
            continue
        for j in s.jobs[:3]:
            node = j.node or "?"
            mtype = j.mission_type or "?"
            lines.append(f"  - {mtype} - {node}")

    return event.plain_result("\n".join(lines))
