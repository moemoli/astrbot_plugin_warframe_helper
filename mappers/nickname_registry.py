from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Any

from astrbot.api import logger
from astrbot.core.utils.astrbot_path import get_astrbot_plugin_data_path

from ..http_utils import fetch_json

NICKNAME_FILE_NAME = "warframe_nicknames.json"

SYM_BASE_NICKNAMES = "_BUILTIN_BASE_NICKNAMES"
SYM_RIVEN_WEAPON_NICKNAMES = "_BUILTIN_RIVEN_WEAPON_NICKNAMES"
SYM_RIVEN_STAT_NICKNAMES = "_BUILTIN_RIVEN_STAT_NICKNAMES"
USER_ALIASES = "aliases"

DEFAULT_NICKNAME_REMOTE_URL = "https://gh-proxy.org/https://raw.githubusercontent.com/moemoli/astrbot_plugin_warframe_helper/refs/heads/master/assets/warframe_nicknames.default.json"

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

    @property
    def default_path(self) -> Path:
        return self._default_path

    def _sanitize_payload(self, payload: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        raw: dict[str, Any] = dict(payload or {})
        changed = False

        # Backward compatibility: migrate old #sym:* keys into new keys.
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

        if not isinstance(raw.get("version"), int):
            raw["version"] = 1
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

        return raw, changed

    def _default_payload(self) -> dict[str, Any]:
        if self._default_path.exists():
            try:
                raw = json.loads(self._default_path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    normalized, _ = self._sanitize_payload(raw)
                    return normalized
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

    def load_default(self) -> dict[str, Any]:
        data = self._default_payload()
        normalized, changed = self._sanitize_payload(data)
        if changed:
            try:
                self.save_default(normalized)
            except Exception as exc:
                logger.warning(f"Failed to persist normalized default nickname json: {exc!s}")
        return normalized

    def save_default(self, data: dict[str, Any]) -> None:
        payload, _ = self._sanitize_payload(dict(data or {}))
        self._default_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def ensure_file(self) -> None:
        if self._path.exists():
            return

        payload = self.load_default()
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

        raw, changed = self._sanitize_payload(raw)

        if changed:
            try:
                self.save(raw)
            except Exception as exc:
                logger.warning(f"Failed to persist migrated nickname json: {exc!s}")
        return raw

    def save(self, data: dict[str, Any]) -> None:
        payload, _ = self._sanitize_payload(dict(data or {}))

        self._path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def sync_default_to_data(self, *, preserve_user_aliases: bool = True) -> dict[str, int]:
        default_data = self.load_default()
        if preserve_user_aliases:
            runtime_data = self.load()
            aliases = runtime_data.get(USER_ALIASES)
            if isinstance(aliases, dict):
                default_data[USER_ALIASES] = aliases

        self.save(default_data)
        return {
            "base_aliases": len(default_data.get(SYM_BASE_NICKNAMES, {})),
            "user_aliases": len(default_data.get(USER_ALIASES, {})),
        }

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

    def upsert_default_alias(
        self,
        *,
        alias: str,
        full_name: str,
        section: str = SYM_BASE_NICKNAMES,
        sync_to_data: bool = True,
    ) -> tuple[str, str]:
        key = normalize_alias_key(alias)
        value = normalize_alias_value(full_name)
        if not key:
            raise ValueError("alias is empty")
        if not value:
            raise ValueError("full_name is empty")

        data = self.load_default()
        block = data.get(section)
        if not isinstance(block, dict):
            block = {}

        block[key] = value
        data[section] = dict(sorted(block.items(), key=lambda kv: kv[0]))
        self.save_default(data)

        if sync_to_data:
            self.sync_default_to_data(preserve_user_aliases=True)

        return key, value

    async def refresh_default_from_url(
        self,
        *,
        url: str = DEFAULT_NICKNAME_REMOTE_URL,
        merge_builtins: bool = True,
    ) -> dict[str, Any]:
        """Fetch remote nickname table and save as new default.

        Args:
            url: Remote URL to fetch the JSON from.
            merge_builtins: If True, merge local _BUILTIN_* sections with the
                remote data so that local expansions are preserved. Local
                entries take priority for same-key conflicts.
        """
        src = str(url or "").strip() or DEFAULT_NICKNAME_REMOTE_URL
        data = await fetch_json(src, timeout_sec=20.0)
        if not isinstance(data, dict):
            return {
                "ok": False,
                "reason": "invalid_json",
                "url": src,
            }

        normalized, _ = self._sanitize_payload(data)

        if merge_builtins:
            # Merge local built-in sections into the remote data.
            # Local entries are preserved; new remote entries are added.
            try:
                local = self._default_payload()
            except Exception:
                local = {}
            builtin_sections = [
                SYM_BASE_NICKNAMES,
                SYM_RIVEN_WEAPON_NICKNAMES,
                SYM_RIVEN_STAT_NICKNAMES,
            ]
            for section in builtin_sections:
                remote_section = normalized.get(section)
                local_section = local.get(section)
                if not isinstance(remote_section, dict):
                    remote_section = {}
                if not isinstance(local_section, dict):
                    local_section = {}
                # Local ∪ Remote (local wins for same key)
                merged: dict[str, str] = {}
                for k, v in remote_section.items():
                    if isinstance(k, str) and isinstance(v, str):
                        merged[k] = v
                for k, v in local_section.items():
                    if isinstance(k, str) and isinstance(v, str):
                        merged[k] = v  # local overrides remote
                normalized[section] = merged

        try:
            self.save_default(normalized)
            sync_stats = self.sync_default_to_data(preserve_user_aliases=True)
            return {
                "ok": True,
                "url": src,
                "base_aliases": int(sync_stats.get("base_aliases", 0)),
                "user_aliases": int(sync_stats.get("user_aliases", 0)),
            }
        except Exception as exc:
            return {
                "ok": False,
                "reason": f"save_failed: {exc!s}",
                "url": src,
            }
