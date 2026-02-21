from __future__ import annotations

import json
from fnmatch import fnmatch
from typing import Any
from urllib.parse import urlsplit

import aiohttp

from astrbot.api import logger

_proxy_url: str | None = None
_direct_domains: list[str] = []


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


def set_direct_domains(domains: list[str] | None) -> None:
    """Set direct-connect domain patterns.

    When `proxy_url` is set, requests whose URL hostname matches any pattern in this list
    will NOT use the configured proxy.

    Patterns support glob syntax like: `x.com`, `*.x.com`.
    """

    global _direct_domains
    if not domains:
        _direct_domains = []
        return

    out: list[str] = []
    for d in domains:
        s = str(d or "").strip().lower()
        if not s:
            continue

        # Allow users to paste a full URL; extract hostname.
        try:
            if "://" in s:
                host = urlsplit(s).hostname
                s = (host or "").strip().lower()
        except Exception:
            pass

        # Strip path if any.
        if "/" in s:
            s = s.split("/", 1)[0].strip().lower()

        # Strip port if present.
        if ":" in s:
            s = s.split(":", 1)[0].strip().lower()

        if s and s not in out:
            out.append(s)

    _direct_domains = out


def get_proxy_url() -> str | None:
    return _proxy_url


def _url_hostname(url: str) -> str:
    u = str(url or "").strip()
    if not u:
        return ""
    try:
        host = urlsplit(u).hostname
        return str(host or "").strip().lower()
    except Exception:
        return ""


def _should_bypass_proxy(url: str) -> bool:
    host = _url_hostname(url)
    if not host:
        return False
    for pattern in _direct_domains:
        try:
            if fnmatch(host, pattern):
                return True
        except Exception:
            continue
    return False


def request_kwargs_for_url(url: str) -> dict[str, Any]:
    """Build aiohttp per-request kwargs (proxy etc) for a specific URL."""

    kw: dict[str, Any] = {}
    if _proxy_url and not _should_bypass_proxy(url):
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

    async with aiohttp.ClientSession(timeout=timeout, trust_env=True) as session:
        last_err: str | None = None
        for url in url_list:
            try:
                req_kw = request_kwargs_for_url(url)
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
