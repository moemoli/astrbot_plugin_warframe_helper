from __future__ import annotations

# Shared constants for command parsing / display.

MARKET_PLATFORM_ALIASES: dict[str, str] = {
    "pc": "pc",
    "电脑": "pc",
    "ps": "ps4",
    "ps4": "ps4",
    "ps5": "ps4",
    "xbox": "xbox",
    "xb": "xbox",
    "ns": "switch",
    "switch": "switch",
}

WORLDSTATE_PLATFORM_ALIASES: dict[str, str] = {
    "pc": "pc",
    "电脑": "pc",
    "cn": "cn",
    "-cn": "cn",
    "国服": "cn",
    "ps": "ps4",
    "ps4": "ps4",
    "ps5": "ps4",
    "xbox": "xb1",
    "xb": "xb1",
    "xb1": "xb1",
    "ns": "swi",
    "switch": "swi",
    "swi": "swi",
}

WM_BUY_ALIASES: set[str] = {"收", "买", "buy", "b"}
WM_SELL_ALIASES: set[str] = {"出", "卖", "sell", "s"}

RIVEN_STAT_ALIASES: dict[str, str] = {
    "暴击率": "critical_chance",
    "暴击": "critical_chance",
    "暴率": "critical_chance",
    "爆击": "critical_chance",
    "爆率": "critical_chance",
    "cc": "critical_chance",
    "暴击伤害": "critical_damage",
    "暴伤": "critical_damage",
    "爆伤": "critical_damage",
    "cd": "critical_damage",
    "多重": "multishot",
    "多重射击": "multishot",
    "ms": "multishot",
    "伤害": "base_damage_/_melee_damage",
    "毒": "toxin_damage",
    "火": "heat_damage",
    "冰": "cold_damage",
    "电": "electric_damage",
    "穿刺": "puncture_damage",
    "切割": "slash_damage",
    "冲击": "impact_damage",
    "弹药": "ammo_maximum",
    "弹匣": "magazine_capacity",
    "装填": "reload_speed",
    "变焦": "zoom",
    "g歧视": "damage_vs_grineer",
    "c歧视": "damage_vs_corpus",
    "i歧视": "damage_vs_infested",
    "s歧视": "damage_vs_sentient",
    "grineer歧视": "damage_vs_grineer",
    "corpus歧视": "damage_vs_corpus",
    "infested歧视": "damage_vs_infested",
    "sentient歧视": "damage_vs_sentient",
    "对grineer伤害": "damage_vs_grineer",
    "对corpus伤害": "damage_vs_corpus",
    "对infested伤害": "damage_vs_infested",
    "对gineer伤害": "damage_vs_grineer",
    "对grineer": "damage_vs_grineer",
    "对corpus": "damage_vs_corpus",
    "对infested": "damage_vs_infested",
    "投射物速度": "projectile_speed",
    "投射速度": "projectile_speed",
    "投射": "projectile_speed",
    "弹速": "projectile_speed",
    "flight": "projectile_speed",
    # 穿透
    "穿透": "punch_through",
    "punch": "punch_through",
    # 触发时间
    "触发时间": "status_duration",
    "异常时间": "status_duration",
    "状态持续时间": "status_duration",
    "sdur": "status_duration",
    # 后坐力
    "后坐力": "recoil",
    "后座": "recoil",
    # 初始连击
    "初始连击": "channeling_damage",
    "初始连击数": "channeling_damage",
    "初连": "channeling_damage",
    # 重击效率
    "重击效率": "channeling_efficiency",
    "导引效率": "channeling_efficiency",
    "cheff": "channeling_efficiency",
    # 攻击范围
    "攻击范围": "range",
    "范围": "range",
    # 处决伤害
    "处决伤害": "finisher_damage",
    "处决": "finisher_damage",
    # 射速/攻击速度
    "射速": "fire_rate_/_attack_speed",
    "攻击速度": "fire_rate_/_attack_speed",
    "攻速": "fire_rate_/_attack_speed",
    "as": "fire_rate_/_attack_speed",
    # 触发几率
    "触发几率": "status_chance",
    "触发": "status_chance",
    "触发率": "status_chance",
    "sc": "status_chance",
    # 连击持续时间
    "连击持续时间": "combo_duration",
    "连击时间": "combo_duration",
    "cdur": "combo_duration",
    # 滑行攻击暴击率
    "滑行攻击暴击率": "critical_chance_on_slide_attack",
    "滑砍暴击": "critical_chance_on_slide_attack",
    "滑暴": "critical_chance_on_slide_attack",
    "scc": "critical_chance_on_slide_attack",
    # 额外连击数获取
    "额外连击数获取": "chance_to_gain_extra_combo_count",
    "额外连击": "chance_to_gain_extra_combo_count",
    "extracombo": "chance_to_gain_extra_combo_count",
    # 连击几率
    "连击几率": "chance_to_gain_combo_count",
    "获得连击几率": "chance_to_gain_combo_count",
    "连击率": "chance_to_gain_combo_count",
    "combocount": "chance_to_gain_combo_count",
}

RIVEN_POLARITY_CN: dict[str, str] = {
    "madurai": "V",
    "vazarin": "D",
    "naramon": "-",
    "zenurik": "R",
}

RIVEN_STAT_CN: dict[str, str] = {
    "critical_chance": "暴击率",
    "critical_damage": "暴击伤害",
    "multishot": "多重",
    "base_damage_/_melee_damage": "伤害",
    "toxin_damage": "毒",
    "heat_damage": "火",
    "cold_damage": "冰",
    "electric_damage": "电",
    "puncture_damage": "穿刺",
    "slash_damage": "切割",
    "impact_damage": "冲击",
    "ammo_maximum": "弹药",
    "magazine_capacity": "弹匣",
    "reload_speed": "装填",
    "zoom": "变焦",
    "damage_vs_grineer": "对Grineer伤害",
    "damage_vs_corpus": "对Corpus伤害",
    "damage_vs_infested": "对Infested伤害",
    "damage_vs_sentient": "对Sentient伤害",
    "projectile_speed": "投射物速度",
    "punch_through": "穿透",
    "status_duration": "触发时间",
    "recoil": "后坐力",
    "channeling_damage": "初始连击",
    "channeling_efficiency": "重击效率",
    "range": "攻击范围",
    "finisher_damage": "处决伤害",
    "fire_rate_/_attack_speed": "射速/攻速",
    "status_chance": "触发几率",
    "combo_duration": "连击持续时间",
    "critical_chance_on_slide_attack": "滑砍暴击率",
    "chance_to_gain_extra_combo_count": "额外连击数获取",
    "chance_to_gain_combo_count": "连击几率",
}


MARKET_STATUS_CN: dict[str, str] = {
    "ingame": "游戏中",
    "online": "在线",
    "offline": "离线",
    "away": "离开",
    "invisible": "隐身",
    "unknown": "未知",
}


def normalize_market_status(status: str | None) -> str:
    s = (status or "").strip().lower()
    if s in {"ingame", "in_game", "in-game", "in game"}:
        return "ingame"
    if s in {"online", "off"}:
        return "online"
    if s in {"offline", "invisible_offline"}:
        return "offline"
    if s in {"away", "afk"}:
        return "away"
    if s in {"invisible", "hidden"}:
        return "invisible"
    return "unknown"


def market_status_to_cn(status: str | None) -> str:
    return MARKET_STATUS_CN.get(normalize_market_status(status), "未知")
