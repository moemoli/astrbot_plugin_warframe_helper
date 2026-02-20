from __future__ import annotations

import json
from typing import Any

import aiohttp

from astrbot.api import logger


_proxy_url: str | None = None


def set_proxy_url(proxy_url: str | None) -> None:
    """Set plugin-level proxy url.

    - If proxy_url is a non-empty string, all HTTP requests made via this module
      will force using this proxy.
    - If proxy_url is empty/None, requests will rely on system/environment proxy
      settings (via aiohttp trust_env=True).
    """

    global _proxy_url
    if proxy_url is None:
        _proxy_url = None
        return
    p = str(proxy_url).strip()
    _proxy_url = p if p else None


def get_proxy_url() -> str | None:
    return _proxy_url


def _request_kwargs() -> dict[str, Any]:
    """Build aiohttp per-request kwargs (proxy etc)."""

    kw: dict[str, Any] = {}
    if _proxy_url:
        kw["proxy"] = _proxy_url
    return kw


def _default_headers() -> dict[str, str]:
    return {
        "User-Agent": "AstrBot/warframe_helper (+https://github.com/Soulter/AstrBot)",
        # Some upstream endpoints may enforce anti-bot rules.
        # Keep headers lightweight but browser-compatible.
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7",
        "Referer": "https://www.warframe.com/",
        "Origin": "https://www.warframe.com",
    }


async def fetch_bytes(
    urls: str | list[str],
    *,
    timeout_sec: float = 20.0,
    headers: dict[str, str] | None = None,
) -> bytes | None:
    url_list = [urls] if isinstance(urls, str) else [u for u in urls if u]
    if not url_list:
        return None

    req_headers = {**_default_headers(), **(headers or {})}
    timeout = aiohttp.ClientTimeout(total=float(timeout_sec))

    req_kw = _request_kwargs()

    async with aiohttp.ClientSession(timeout=timeout, trust_env=True) as session:
        last_err: str | None = None
        for url in url_list:
            try:
                async with session.get(url, headers=req_headers, **req_kw) as resp:
                    if resp.status != 200:
                        last_err = f"{resp.status} {url}"
                        continue
                    return await resp.read()
            except Exception as exc:
                last_err = f"{exc!s} ({url})"
                continue

        if last_err:
            logger.warning(f"http fetch_bytes failed: {last_err}")
        return None


async def fetch_json(
    urls: str | list[str],
    *,
    timeout_sec: float = 20.0,
    headers: dict[str, str] | None = None,
) -> Any | None:
    raw = await fetch_bytes(urls, timeout_sec=timeout_sec, headers=headers)
    if raw is None:
        return None
    try:
        return json.loads(raw.decode("utf-8", "replace"))
    except Exception as exc:
        logger.warning(f"http fetch_json decode failed: {exc!s}")
        return None
