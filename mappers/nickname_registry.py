from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Any

from astrbot.api import logger
from astrbot.core.utils.astrbot_path import get_astrbot_plugin_data_path

NICKNAME_FILE_NAME = "warframe_nicknames.json"

SYM_BASE_NICKNAMES = "_BUILTIN_BASE_NICKNAMES"
SYM_RIVEN_WEAPON_NICKNAMES = "_BUILTIN_RIVEN_WEAPON_NICKNAMES"
SYM_RIVEN_STAT_NICKNAMES = "_BUILTIN_RIVEN_STAT_NICKNAMES"
USER_ALIASES = "aliases"

_LEGACY_KEY_MAP: dict[str, str] = {
    "#sym:_BUILTIN_BASE_NICKNAMES": SYM_BASE_NICKNAMES,
    "#sym:_BUILTIN_RIVEN_WEAPON_NICKNAMES": SYM_RIVEN_WEAPON_NICKNAMES,
    "#sym:_BUILTIN_RIVEN_STAT_NICKNAMES": SYM_RIVEN_STAT_NICKNAMES,
}


def normalize_alias_key(text: str) -> str:
    text = unicodedata.normalize("NFKC", str(text or ""))
    text = text.strip().lower()
    text = re.sub(r"\s+", "", text)
    return text


def normalize_alias_value(text: str) -> str:
    text = unicodedata.normalize("NFKC", str(text or ""))
    text = re.sub(r"\s+", " ", text).strip()
    return text


class NicknameRegistry:
    def __init__(self) -> None:
        self._plugin_data_dir = (
            Path(get_astrbot_plugin_data_path()) / "astrbot_plugin_warframe_helper"
        )
        self._plugin_data_dir.mkdir(parents=True, exist_ok=True)
        self._path = self._plugin_data_dir / NICKNAME_FILE_NAME
        self._default_path = (
            Path(__file__).resolve().parents[1]
            / "assets"
            / "warframe_nicknames.default.json"
        )

    @property
    def path(self) -> Path:
        return self._path

    def _default_payload(self) -> dict[str, Any]:
        if self._default_path.exists():
            try:
                raw = json.loads(self._default_path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    return raw
            except Exception as exc:
                logger.warning(
                    f"Failed to parse default nickname json {self._default_path}: {exc!s}"
                )

        return {
            "version": 1,
            SYM_BASE_NICKNAMES: {},
            SYM_RIVEN_WEAPON_NICKNAMES: {},
            SYM_RIVEN_STAT_NICKNAMES: {},
            USER_ALIASES: {},
        }

    def ensure_file(self) -> None:
        if self._path.exists():
            return

        payload = self._default_payload()
        try:
            self._path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as exc:
            logger.warning(f"Failed to initialize nickname json: {exc!s}")

    def load(self) -> dict[str, Any]:
        self.ensure_file()
        if not self._path.exists():
            return self._default_payload()

        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                return self._default_payload()
        except Exception as exc:
            logger.warning(f"Failed to load nickname json, fallback to default: {exc!s}")
            return self._default_payload()

        # Backward compatibility: migrate old #sym:* keys into new keys.
        changed = False
        for legacy_key, new_key in _LEGACY_KEY_MAP.items():
            legacy_val = raw.get(legacy_key)
            if isinstance(legacy_val, dict):
                dst = raw.get(new_key)
                if not isinstance(dst, dict):
                    dst = {}
                for k, v in legacy_val.items():
                    if k not in dst:
                        dst[k] = v
                raw[new_key] = dst
                raw.pop(legacy_key, None)
                changed = True

        for key in (
            SYM_BASE_NICKNAMES,
            SYM_RIVEN_WEAPON_NICKNAMES,
            SYM_RIVEN_STAT_NICKNAMES,
            USER_ALIASES,
        ):
            if not isinstance(raw.get(key), dict):
                raw[key] = {}
                changed = True

        if changed:
            try:
                self.save(raw)
            except Exception as exc:
                logger.warning(f"Failed to persist migrated nickname json: {exc!s}")
        return raw

    def save(self, data: dict[str, Any]) -> None:
        payload = dict(data or {})
        payload.setdefault("version", 1)
        for key in (
            SYM_BASE_NICKNAMES,
            SYM_RIVEN_WEAPON_NICKNAMES,
            SYM_RIVEN_STAT_NICKNAMES,
            USER_ALIASES,
        ):
            if not isinstance(payload.get(key), dict):
                payload[key] = {}

        self._path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def get_alias_map(self, *sections: str) -> dict[str, str]:
        data = self.load()
        if not sections:
            sections = (
                SYM_BASE_NICKNAMES,
                SYM_RIVEN_WEAPON_NICKNAMES,
                SYM_RIVEN_STAT_NICKNAMES,
                USER_ALIASES,
            )

        out: dict[str, str] = {}
        for sec in sections:
            block = data.get(sec)
            if not isinstance(block, dict):
                continue
            for alias, full_name in block.items():
                if not isinstance(alias, str) or not isinstance(full_name, str):
                    continue
                key = normalize_alias_key(alias)
                value = normalize_alias_value(full_name)
                if not key or not value:
                    continue
                out[key] = value
        return out

    def upsert_alias(
        self,
        *,
        alias: str,
        full_name: str,
        section: str = USER_ALIASES,
    ) -> tuple[str, str]:
        key = normalize_alias_key(alias)
        value = normalize_alias_value(full_name)
        if not key:
            raise ValueError("alias is empty")
        if not value:
            raise ValueError("full_name is empty")

        data = self.load()
        block = data.get(section)
        if not isinstance(block, dict):
            block = {}

        block[key] = value
        data[section] = dict(sorted(block.items(), key=lambda kv: kv[0]))
        self.save(data)
        return key, value
