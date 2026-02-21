from __future__ import annotations

from astrbot.api.event import AstrMessageEvent

from ..clients.drop_data_client import DropDataClient
from ..clients.public_export_client import PublicExportClient
from ..helpers import split_tokens
from ..utils.text import normalize_compact, safe_relic_name


async def cmd_drops(
    *,
    event: AstrMessageEvent,
    drop_data_client: DropDataClient,
    public_export_client: PublicExportClient,
    raw_args: str,
):
    arg_text = str(raw_args).strip()
    if not arg_text:
        return event.plain_result(
            "用法：/掉落 <物品> [数量<=30] 例如：/掉落 Neurodes 15"
        )

    tokens = split_tokens(arg_text)
    if not tokens:
        return event.plain_result(
            "用法：/掉落 <物品> [数量<=30] 例如：/掉落 Neurodes 15"
        )

    limit = 15
    if tokens and str(tokens[-1]).isdigit():
        try:
            limit = int(str(tokens[-1]))
            tokens = tokens[:-1]
        except Exception:
            limit = 15

    query = " ".join([str(t).strip() for t in tokens if str(t).strip()]).strip()
    if not query:
        return event.plain_result(
            "用法：/掉落 <物品> [数量<=30] 例如：/掉落 Neurodes 15"
        )

    resolved_en: str | None = None

    rows = await drop_data_client.search_drops(item_query=query, limit=limit)
    if not rows:
        candidates = await public_export_client.resolve_localized_to_english_candidates(
            query, language="zh", limit=5
        )
        if candidates:
            resolved_en = candidates[0]
            rows = await drop_data_client.search_drops(
                item_query=resolved_en, limit=limit
            )

    if not rows:
        return event.plain_result(
            f"未找到掉落信息：{query}\n"
            "提示：该数据源条目多为英文名；也可以尝试更短关键词（如 Neurodes / Blueprint）。"
        )

    def fmt_chance(v: object) -> str:
        if isinstance(v, (int, float)):
            return f"{float(v):g}%"
        if isinstance(v, str):
            try:
                return f"{float(v):g}%"
            except Exception:
                return "?%"
        return "?%"

    title = f"掉落搜索：{query}"
    if resolved_en:
        title += f"（解析：{resolved_en}）"
    title += f"（展示前{len(rows)}条 | 数据源：WFCD/warframe-drop-data）"

    lines: list[str] = [title]
    for r in rows:
        place = str(r.get("place") or "?")
        rarity = str(r.get("rarity") or "").strip()
        chance = fmt_chance(r.get("chance"))
        suffix = f" | {rarity}" if rarity else ""
        lines.append(f"- {place} | {chance}{suffix}")

    return event.plain_result("\n".join(lines))


async def cmd_relic(
    *,
    event: AstrMessageEvent,
    drop_data_client: DropDataClient,
    raw_args: str,
):
    arg_text = str(raw_args).strip()
    if not arg_text:
        return event.plain_result(
            "用法：/遗物 <纪元> <遗物名> 或 /遗物 <遗物名>\n"
            "示例：/遗物 古纪 A1  或  /遗物 Axi A1  或  /遗物 A1"
        )

    tokens = split_tokens(arg_text)
    if not tokens:
        return event.plain_result(
            "用法：/遗物 <纪元> <遗物名> 或 /遗物 <遗物名>\n"
            "示例：/遗物 古纪 A1  或  /遗物 Axi A1  或  /遗物 A1"
        )

    tier_aliases: dict[str, str] = {
        "lith": "Lith",
        "meso": "Meso",
        "neo": "Neo",
        "axi": "Axi",
        "requiem": "Requiem",
        "omnia": "Omnia",
        "古纪": "Lith",
        "前纪": "Meso",
        "中纪": "Neo",
        "后纪": "Axi",
        "安魂": "Requiem",
        "全能": "Omnia",
    }

    tier: str | None = None
    name_parts: list[str] = []
    for t in tokens:
        raw = str(t).strip()
        if not raw:
            continue
        k = normalize_compact(raw)
        guess = tier_aliases.get(k)
        if guess and tier is None:
            tier = guess
            continue
        name_parts.append(raw)

    relic_name = safe_relic_name("".join(name_parts))
    if not relic_name:
        return event.plain_result("未识别遗物名。示例：/遗物 古纪 A1  或  /遗物 Axi A1")

    if tier is None:
        tiers = await drop_data_client.find_relic_tiers(relic_name)
        if not tiers:
            return event.plain_result(
                f"未找到遗物：{relic_name}。\n"
                "提示：可以尝试带上纪元，例如：/遗物 古纪 A1"
            )
        if len(tiers) > 1:
            return event.plain_result(
                f"遗物 {relic_name} 匹配到多个纪元：{', '.join(tiers)}。\n"
                "请指定纪元，例如：/遗物 古纪 A1 或 /遗物 Axi A1"
            )
        tier = tiers[0]

    detail = await drop_data_client.get_relic_detail(tier=tier, relic_name=relic_name)
    if detail is None:
        return event.plain_result(
            f"未获取到遗物数据：{tier} {relic_name}（可能是网络限制或接口不可达）。"
        )

    rewards = detail.get("rewards") if isinstance(detail, dict) else None
    if not isinstance(rewards, dict):
        return event.plain_result(
            f"遗物数据结构异常：{tier} {relic_name}（未返回 rewards）。"
        )

    def fmt_reward(it: object) -> str:
        if not isinstance(it, dict):
            return "- ?"
        item_name = it.get("itemName") or it.get("item") or it.get("name")
        rarity = it.get("rarity")
        chance = it.get("chance")
        if isinstance(chance, (int, float)):
            ch = f"{float(chance):g}%"
        elif isinstance(chance, str):
            try:
                ch = f"{float(chance):g}%"
            except Exception:
                ch = "?%"
        else:
            ch = "?%"
        r = str(rarity).strip() if isinstance(rarity, str) and rarity.strip() else ""
        n = (
            str(item_name).strip()
            if isinstance(item_name, str) and item_name.strip()
            else "?"
        )
        prefix = f"{r} " if r else ""
        return f"- {ch} {prefix}{n}".strip()

    order = ["Intact", "Exceptional", "Flawless", "Radiant"]
    lines: list[str] = [
        f"遗物：{tier} {relic_name}（数据源：WFCD/warframe-drop-data）",
    ]
    for key in order:
        arr = rewards.get(key)
        if not isinstance(arr, list) or not arr:
            continue
        lines.append(f"\n{key}：")
        for it in arr:
            lines.append(fmt_reward(it))

    return event.plain_result("\n".join(lines).strip())
