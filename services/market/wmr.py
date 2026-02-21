from __future__ import annotations

import json
import re

from astrbot.api.event import AstrMessageEvent

from ...clients.market_client import WarframeMarketClient
from ...components.event_ttl_cache import EventScopedTTLCache
from ...components.qq_official_webhook import QQOfficialWebhookPager
from ...constants import MARKET_PLATFORM_ALIASES, RIVEN_STAT_ALIASES
from ...helpers import split_tokens, uniq_lower
from ...mappers.riven_mapping import WarframeRivenWeaponMapper
from ...mappers.riven_stats_mapping import WarframeRivenStatMapper
from .pager_common import rank_wmr_auctions, render_wmr_page_image


def _normalize_key(text: str) -> str:
    return re.sub(r"\s+", "", str(text or "").strip().lower())


async def _ai_split_stat_token(
    *,
    context: object,
    provider_id: str,
    token: str,
) -> list[str]:
    tok = (token or "").strip()
    if not tok or not provider_id:
        return []

    system_prompt = (
        "You split a Warframe Riven stat shorthand token into individual stat tokens. "
        "Return JSON only."
    )
    prompt = (
        "Split the following user token into 1~6 smaller stat tokens (Chinese or common abbreviations).\n"
        "Rules:\n"
        '- Output MUST be valid JSON: {"tokens": ["..."]}.\n'
        "- Do NOT output explanations.\n"
        "- Keep tokens minimal and meaningful for riven stat parsing.\n"
        "Examples:\n"
        '- "双爆毒" -> {"tokens":["双爆","毒"]}\n'
        '- "暴伤多重" -> {"tokens":["暴伤","多重"]}\n'
        f"Token: {tok}\n"
        "JSON:"
    )

    try:
        llm_generate = getattr(context, "llm_generate")
        llm_resp = await llm_generate(
            chat_provider_id=provider_id,
            prompt=prompt,
            system_prompt=system_prompt,
            temperature=0,
            timeout=15,
        )
    except TypeError:
        try:
            llm_generate = getattr(context, "llm_generate")
            llm_resp = await llm_generate(
                chat_provider_id=provider_id,
                prompt=prompt,
                system_prompt=system_prompt,
                temperature=0,
            )
        except Exception:
            return []
    except Exception:
        return []

    text = (getattr(llm_resp, "completion_text", None) or "").strip()

    obj: object | None = None
    try:
        obj = json.loads(text)
    except Exception:
        m = re.search(r"\{[\s\S]*\}", text)
        if m:
            try:
                obj = json.loads(m.group(0))
            except Exception:
                obj = None

    arr = obj.get("tokens") if isinstance(obj, dict) else None
    if isinstance(arr, str):
        arr = [arr]
    if not isinstance(arr, list):
        return []

    out: list[str] = []
    seen: set[str] = set()
    for s in arr:
        if not isinstance(s, str):
            continue
        s2 = s.strip()
        s2 = re.sub(r"^(正面|正|负面|负)[:：]?", "", s2)
        s2 = s2.strip(" ,，+\t\r\n")
        if not s2:
            continue
        k = s2.lower()
        if k in seen:
            continue
        seen.add(k)
        out.append(s2)
        if len(out) >= 6:
            break

    return out


async def cmd_wmr(
    *,
    context: object,
    event: AstrMessageEvent,
    raw_args: str,
    config: dict | None,
    market_client: WarframeMarketClient,
    riven_weapon_mapper: WarframeRivenWeaponMapper,
    riven_stat_mapper: WarframeRivenStatMapper,
    pager_cache: EventScopedTTLCache,
    qq_pager: QQOfficialWebhookPager,
):
    try:
        event.should_call_llm(False)
    except Exception:
        pass

    arg_text = str(raw_args or "").strip()
    if not arg_text:
        yield event.plain_result(
            "用法：/wmr <武器> [条件...] 例如：/wmr 绝路 双暴 负任意 12段 r槽",
        )
        return

    tokens = split_tokens(arg_text)
    if not tokens:
        yield event.plain_result(
            "用法：/wmr <武器> [条件...] 例如：/wmr 绝路 双暴 负任意 12段 r槽",
        )
        return

    weapon_query = tokens[0]
    rest = tokens[1:]

    platform_norm = "pc"
    limit = 10
    language = "zh"

    positive_stats: list[str] = []
    negative_stats: list[str] = []
    negative_required = False
    mastery_rank_min: int | None = None
    polarity: str | None = None

    pending_stats: list[tuple[str, bool]] = []
    unknown_tokens: list[str] = []

    for t in rest:
        t_norm = _normalize_key(t)
        if not t_norm:
            continue

        if t_norm in MARKET_PLATFORM_ALIASES:
            platform_norm = MARKET_PLATFORM_ALIASES[t_norm]
            continue
        if t_norm in MARKET_PLATFORM_ALIASES.values():
            platform_norm = t_norm
            continue

        if t_norm.isdigit():
            limit = int(t_norm)
            continue

        if re.fullmatch(r"[a-z]{2}([\-_][a-z]{2,8})?", t_norm):
            language = t_norm.replace("_", "-")
            continue

        m = re.fullmatch(r"mr?(\d{1,2})", t_norm)
        if m:
            mastery_rank_min = int(m.group(1))
            continue
        m = re.fullmatch(r"(\d{1,2})段", t_norm)
        if m:
            mastery_rank_min = int(m.group(1))
            continue

        m = re.fullmatch(r"([vd\-r])槽", t_norm)
        if not m:
            m = re.fullmatch(r"([vd\-r])极性", t_norm)
        if not m:
            m = re.fullmatch(r"极性([vd\-r])", t_norm)
        if m:
            p = m.group(1)
            if p == "v":
                polarity = "madurai"
            elif p == "d":
                polarity = "vazarin"
            elif p == "-":
                polarity = "naramon"
            elif p == "r":
                polarity = "zenurik"
            continue

        if t_norm in {"madurai", "vazarin", "naramon", "zenurik"}:
            polarity = t_norm
            continue

        if "双暴" in t_norm or "双爆" in t_norm:
            positive_stats.extend(["critical_chance", "critical_damage"])
            rest_tok = t_norm.replace("双暴", "").replace("双爆", "")
            if "毒" in rest_tok:
                positive_stats.append("toxin_damage")
            if "火" in rest_tok:
                positive_stats.append("heat_damage")
            if "冰" in rest_tok:
                positive_stats.append("cold_damage")
            if "电" in rest_tok:
                positive_stats.append("electric_damage")
            if "多重" in rest_tok:
                positive_stats.append("multishot")
            if "伤害" in rest_tok:
                positive_stats.append("base_damage_/_melee_damage")
            if "穿刺" in rest_tok:
                positive_stats.append("puncture_damage")
            if "切割" in rest_tok:
                positive_stats.append("slash_damage")
            if "冲击" in rest_tok:
                positive_stats.append("impact_damage")
            continue

        if t_norm in {"负任意", "任意负", "有负", "要负"} or "负任意" in t_norm:
            negative_required = True
            continue
        if t_norm in {"无负", "不要负", "不带负"}:
            negative_required = False
            negative_stats = []
            continue
        if t_norm.startswith("负") and len(t_norm) > 1:
            negative_required = True
            key = t_norm[1:]
            if key in RIVEN_STAT_ALIASES:
                url_name = RIVEN_STAT_ALIASES[key]
                if url_name == "damage_vs_sentient":
                    pending_stats.append((url_name, True))
                else:
                    negative_stats.append(url_name)
                continue

        if t_norm in RIVEN_STAT_ALIASES:
            url_name = RIVEN_STAT_ALIASES[t_norm]
            if url_name == "damage_vs_sentient":
                pending_stats.append((url_name, False))
            else:
                positive_stats.append(url_name)
            continue

        if "暴击率" in t_norm:
            positive_stats.append("critical_chance")
            continue
        if "暴击伤害" in t_norm or "暴伤" in t_norm:
            positive_stats.append("critical_damage")
            continue

        unknown_tokens.append(str(t).strip())

    provider_id = str((config or {}).get("unknown_abbrev_provider_id") or "")

    await riven_stat_mapper.initialize()

    for url_name, is_negative in pending_stats:
        if riven_stat_mapper.is_valid_url_name(url_name):
            if is_negative:
                negative_stats.append(url_name)
            else:
                positive_stats.append(url_name)
            continue

        yield event.plain_result(
            "warframe.market 当前不支持“对Sentient伤害（S歧视）”紫卡词条。"
            "目前仅支持：对Grineer/Corpus/Infested伤害（G/C/I歧视）。"
        )
        return

    for tok in unknown_tokens:
        tok2 = (tok or "").strip()
        if not tok2:
            continue

        is_negative = False
        query_tok = tok2
        if tok2.startswith("负") and len(tok2) > 1:
            is_negative = True
            negative_required = True
            query_tok = tok2[1:]

        resolved = riven_stat_mapper.resolve_from_alias(
            query_tok, alias_map=RIVEN_STAT_ALIASES
        )
        if not resolved:
            resolved = await riven_stat_mapper.resolve_with_ai(
                context=context,
                event=event,
                token=query_tok,
                provider_id=provider_id,
            )

        if not resolved:
            parts = await _ai_split_stat_token(
                context=context,
                provider_id=provider_id,
                token=query_tok,
            )
            for part in parts:
                part_resolved = riven_stat_mapper.resolve_from_alias(
                    part, alias_map=RIVEN_STAT_ALIASES
                )
                if not part_resolved:
                    part_resolved = await riven_stat_mapper.resolve_with_ai(
                        context=context,
                        event=event,
                        token=part,
                        provider_id=provider_id,
                    )
                if not part_resolved:
                    continue
                if is_negative:
                    negative_stats.append(part_resolved)
                else:
                    positive_stats.append(part_resolved)
            continue

        if is_negative:
            negative_stats.append(resolved)
        else:
            positive_stats.append(resolved)

    positive_stats = uniq_lower(positive_stats)
    negative_stats = uniq_lower(negative_stats)

    weapon = await riven_weapon_mapper.resolve_weapon(
        context=context,
        event=event,
        query=weapon_query,
        provider_id=provider_id,
    )
    if not weapon:
        yield event.plain_result(
            f"未识别武器：{weapon_query}（可尝试输入英文名，如 soma）"
        )
        return

    auctions = await market_client.fetch_riven_auctions(
        weapon.url_name,
        platform=platform_norm,
        positive_stats=positive_stats,
        negative_stats=negative_stats,
        mastery_rank_min=mastery_rank_min,
        polarity=polarity,
        buyout_policy="direct",
    )
    if not auctions:
        yield event.plain_result("未获取到紫卡拍卖数据（可能是网络限制或接口不可达）。")
        return

    ranked = rank_wmr_auctions(
        auctions,
        platform=platform_norm,
        positive_stats=positive_stats,
        negative_stats=negative_stats,
        negative_required=bool(negative_required),
        mastery_rank_min=mastery_rank_min,
        polarity=polarity,
    )

    limit = max(1, min(int(limit), 20))
    page = 1

    if not ranked:
        yield event.plain_result("没有符合条件的一口价紫卡拍卖。")
        return

    pager_cache.put(
        event=event,
        state={
            "kind": "wmr",
            "page": page,
            "limit": limit,
            "platform": platform_norm,
            "language": language,
            "weapon_query": weapon_query,
            "weapon": weapon,
            "positive_stats": list(positive_stats),
            "negative_stats": list(negative_stats),
            "negative_required": bool(negative_required),
            "mastery_rank_min": mastery_rank_min,
            "polarity": polarity,
        },
    )

    rendered, top, summary = await render_wmr_page_image(
        weapon=weapon,
        weapon_query=weapon_query,
        auctions_ranked=ranked,
        platform=platform_norm,
        language=language,
        positive_stats=list(positive_stats),
        negative_stats=list(negative_stats),
        negative_required=bool(negative_required),
        mastery_rank_min=mastery_rank_min,
        polarity=polarity,
        page=page,
        limit=limit,
    )

    if not top:
        yield event.plain_result("没有符合条件的一口价紫卡拍卖。")
        return

    if rendered:
        if qq_pager.enabled_for(event):
            ok = await qq_pager.send_result_markdown_with_keyboard(
                event,
                kind="/wmr",
                page=page,
                image_path=rendered.path,
            )
            if ok:
                return

            try:
                await event.send(event.image_result(rendered.path))
                await qq_pager.send_pager_keyboard(event, kind="/wmr", page=page)
                return
            except Exception:
                yield event.image_result(rendered.path)
                return

        yield event.image_result(rendered.path)
        return

    fallback_name = weapon.item_name if language.startswith("en") else weapon_query
    lines = [f"紫卡 {fallback_name}（{platform_norm}）{summary} 前{len(top)}："]
    for idx, a in enumerate(top, start=1):
        name = a.owner_name or "unknown"
        status = a.owner_status or "unknown"
        pol = a.polarity or "?"
        mr = a.mastery_level if a.mastery_level is not None else "?"
        rr = a.re_rolls if a.re_rolls is not None else "?"
        lines.append(
            f"{idx}. {a.buyout_price}p  {status}  {name}  MR{mr}  {pol}  洗练{rr}"
        )
    yield event.plain_result("\n".join(lines))

    if qq_pager.enabled_for(event):
        try:
            await qq_pager.send_pager_keyboard(event, kind="/wmr", page=page)
        except Exception:
            pass
