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
    "cc": "critical_chance",
    "暴击伤害": "critical_damage",
    "暴伤": "critical_damage",
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
    "grineer歧视": "damage_vs_grineer",
    "corpus歧视": "damage_vs_corpus",
    "infested歧视": "damage_vs_infested",
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
    "damage_vs_grineer": "G歧视",
    "damage_vs_corpus": "C歧视",
    "damage_vs_infested": "I歧视",
}
