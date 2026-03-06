from __future__ import annotations

import asyncio
import base64
import html as html_lib
import mimetypes
import os
import platform
import re
import shutil
import stat
import subprocess
import threading
import textwrap
import uuid
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from pathlib import Path
from typing import Any

from astrbot.api import logger
from astrbot.core.utils.astrbot_path import get_astrbot_temp_path

from PIL import Image, ImageDraw, ImageFont


def _env_int(name: str, default: int) -> int:
    raw = str(os.environ.get(name) or "").strip()
    if not raw:
        return int(default)
    try:
        return int(raw)
    except Exception:
        return int(default)


_HTML_RENDER_WORKERS = max(1, min(4, _env_int("WF_HTML_RENDER_WORKERS", 2)))
_HTML_RENDER_EXECUTOR = ThreadPoolExecutor(
    max_workers=_HTML_RENDER_WORKERS,
    thread_name_prefix="wf-html-render",
)
_CHROMIUM_PREPARE_LOCK = threading.Lock()
_CHROMIUM_PREPARED = False
_RENDER_BROWSER_WS_ENDPOINT = ""
_EMPTY_REMOTE_LOCAL_CHAIN_FAILED = False
_EMPTY_REMOTE_LOCAL_CHAIN_LOCK = threading.Lock()


def set_render_browser_ws_endpoint(endpoint: str | None) -> None:
    global _RENDER_BROWSER_WS_ENDPOINT, _EMPTY_REMOTE_LOCAL_CHAIN_FAILED
    _RENDER_BROWSER_WS_ENDPOINT = str(endpoint or "").strip()
    if _RENDER_BROWSER_WS_ENDPOINT:
        # With explicit remote browser configured, clear empty-endpoint fail flag.
        with _EMPTY_REMOTE_LOCAL_CHAIN_LOCK:
            _EMPTY_REMOTE_LOCAL_CHAIN_FAILED = False


def _mark_empty_remote_local_chain_failed() -> None:
    global _EMPTY_REMOTE_LOCAL_CHAIN_FAILED
    with _EMPTY_REMOTE_LOCAL_CHAIN_LOCK:
        _EMPTY_REMOTE_LOCAL_CHAIN_FAILED = True


def _is_empty_remote_local_chain_failed() -> bool:
    with _EMPTY_REMOTE_LOCAL_CHAIN_LOCK:
        return _EMPTY_REMOTE_LOCAL_CHAIN_FAILED


def _ensure_pyppeteer_home() -> Path:
    # Keep Chromium/cache in a writable location for container deployments.
    temp_dir = Path(get_astrbot_temp_path())
    pyppeteer_home = temp_dir / "pyppeteer"
    pyppeteer_home.mkdir(parents=True, exist_ok=True)
    # In Linux containers, pyppeteer often defaults to /root/.local which may
    # not be stable across deployments. Force a writable plugin-local cache.
    if platform.system().lower() == "linux":
        os.environ["PYPPETEER_HOME"] = str(pyppeteer_home)
    else:
        os.environ.setdefault("PYPPETEER_HOME", str(pyppeteer_home))
    return pyppeteer_home


def _detect_missing_shared_libs(executable_path: str | None) -> list[str]:
    if platform.system().lower() != "linux":
        return []
    p = str(executable_path or "").strip()
    if not p or not Path(p).exists():
        return []
    try:
        proc = subprocess.run(
            ["ldd", p],
            capture_output=True,
            text=True,
            check=False,
            timeout=8,
        )
        text = (proc.stdout or "") + "\n" + (proc.stderr or "")
        missing: list[str] = []
        for line in text.splitlines():
            s = line.strip()
            if "=> not found" not in s:
                continue
            lib = s.split("=>", 1)[0].strip()
            if lib and lib not in missing:
                missing.append(lib)
        return missing
    except Exception:
        return []


def image_bytes_to_data_uri(image_bytes: bytes | None, *, filename: str = "image.png") -> str | None:
    if not image_bytes:
        return None

    mime, _ = mimetypes.guess_type(filename)
    mime = mime or "image/png"
    encoded = base64.b64encode(image_bytes).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def svg_text_to_data_uri(svg_text: str) -> str:
    encoded = base64.b64encode(svg_text.encode("utf-8")).decode("ascii")
    return f"data:image/svg+xml;base64,{encoded}"


def _find_browser_executable() -> str | None:
    # Prefer user-provided path, then common install paths and PATH lookup.
    custom = str(os.environ.get("WF_HTML_RENDER_BROWSER") or "").strip()
    if custom and Path(custom).exists():
        return custom

    candidates = [
        os.path.join(
            os.environ.get("ProgramFiles", r"C:\Program Files"),
            "Microsoft",
            "Edge",
            "Application",
            "msedge.exe",
        ),
        os.path.join(
            os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
            "Microsoft",
            "Edge",
            "Application",
            "msedge.exe",
        ),
        os.path.join(
            os.environ.get("ProgramFiles", r"C:\Program Files"),
            "Google",
            "Chrome",
            "Application",
            "chrome.exe",
        ),
        os.path.join(
            os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
            "Google",
            "Chrome",
            "Application",
            "chrome.exe",
        ),
        os.path.join(
            os.environ.get("LocalAppData", ""),
            "Microsoft",
            "Edge",
            "Application",
            "msedge.exe",
        ),
        os.path.join(
            os.environ.get("LocalAppData", ""),
            "Google",
            "Chrome",
            "Application",
            "chrome.exe",
        ),
    ]

    if platform.system().lower() == "linux":
        candidates.extend(
            [
                "/usr/bin/chromium",
                "/usr/bin/chromium-browser",
                "/usr/bin/google-chrome",
                "/usr/bin/google-chrome-stable",
                "/opt/google/chrome/chrome",
            ]
        )

    for name in (
        "msedge",
        "msedge.exe",
        "chrome",
        "chrome.exe",
        "chromium",
        "chromium-browser",
    ):
        found = shutil.which(name)
        if found:
            candidates.append(found)

    seen: set[str] = set()
    for p in candidates:
        pp = str(p or "").strip()
        if not pp or pp in seen:
            continue
        seen.add(pp)
        if Path(pp).exists():
            return pp
    return None


def _downloaded_chromium_executable() -> str | None:
    try:
        _ensure_pyppeteer_home()
        from pyppeteer import chromium_downloader as cd

        exe: Any = cd.chromium_executable
        path = str(exe() if callable(exe) else exe).strip()
        if path and Path(path).exists():
            return path
    except Exception:
        return None
    return None


def _looks_like_downloaded_chromium(path: str) -> bool:
    p = (path or "").lower()
    return "pyppeteer" in p and "local-chromium" in p


def _ensure_executable_permission(path: str | None) -> None:
    if not path:
        return
    try:
        p = Path(path)
        if not p.exists():
            return
        mode = p.stat().st_mode
        p.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    except Exception:
        return


def _prepare_chromium_if_needed() -> bool:
    global _CHROMIUM_PREPARED

    if _CHROMIUM_PREPARED:
        return True

    with _CHROMIUM_PREPARE_LOCK:
        if _CHROMIUM_PREPARED:
            return True

        try:
            _ensure_pyppeteer_home()
            from pyppeteer import chromium_downloader as cd

            if not cd.check_chromium():
                logger.warning(
                    "Local browser executable not found, trying pyppeteer Chromium download..."
                )
                cd.download_chromium()

            if cd.check_chromium():
                _ensure_executable_permission(_downloaded_chromium_executable())
                _CHROMIUM_PREPARED = True
                return True

            logger.warning(
                "Chromium download did not provide a usable executable."
            )
            return False
        except Exception as exc:
            logger.warning(f"Chromium download failed: {exc!s}")
            return False


def _refresh_downloaded_chromium() -> str | None:
    # Redownload once if existing downloaded binary is unusable.
    with _CHROMIUM_PREPARE_LOCK:
        try:
            _ensure_pyppeteer_home()
            from pyppeteer import chromium_downloader as cd

            target = Path(cd.DOWNLOADS_FOLDER) / str(cd.__chromium_revision__)
            if target.exists():
                shutil.rmtree(target, ignore_errors=True)

            logger.warning("Refreshing downloaded Chromium for pyppeteer...")
            cd.download_chromium()
            path = _downloaded_chromium_executable()
            _ensure_executable_permission(path)
            return path
        except Exception as exc:
            logger.warning(f"Chromium refresh failed: {exc!s}")
            return None


def _html_to_plain_text(html: str) -> str:
    text = str(html or "")
    text = re.sub(r"<\s*br\s*/?\s*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<\s*/\s*(p|div|section|article|li|h1|h2|h3|h4|h5|h6)\s*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<\s*script[\s\S]*?<\s*/\s*script\s*>", "", text, flags=re.IGNORECASE)
    text = re.sub(r"<\s*style[\s\S]*?<\s*/\s*style\s*>", "", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = html_lib.unescape(text)
    lines = [re.sub(r"\s+", " ", ln).strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if ln]
    return "\n".join(lines) or "(render fallback)"


def _render_plain_text_png(
    *,
    text: str,
    width: int,
    min_height: int,
    out_path: Path,
) -> str | None:
    try:
        content_width = max(420, int(width))
        font = ImageFont.load_default()
        wrapped: list[str] = []
        for line in str(text or "").splitlines() or [""]:
            wrapped.extend(textwrap.wrap(line, width=64) or [""])

        line_height = 20
        pad_x, pad_y = 24, 24
        canvas_h = max(int(min_height), pad_y * 2 + max(1, len(wrapped)) * line_height)

        image = Image.new("RGB", (content_width, canvas_h), (248, 250, 252))
        draw = ImageDraw.Draw(image)

        y = pad_y
        for line in wrapped:
            draw.text((pad_x, y), line, fill=(15, 23, 42), font=font)
            y += line_height

        image.save(out_path, format="PNG")
        return str(out_path)
    except Exception as exc:
        logger.warning(f"Plain-text fallback render failed: {exc!s}")
        return None


async def _render_html_to_png_file_impl(
    *,
    html: str,
    width: int,
    prefix: str,
    min_height: int = 720,
) -> str | None:
    temp_dir = Path(get_astrbot_temp_path())
    temp_dir.mkdir(parents=True, exist_ok=True)
    _ensure_pyppeteer_home()
    out_path = temp_dir / f"{prefix}_{uuid.uuid4().hex}.png"

    remote_endpoint = str(_RENDER_BROWSER_WS_ENDPOINT or "").strip()
    if not remote_endpoint and _is_empty_remote_local_chain_failed():
        # Once empty-endpoint local chain has failed, directly use fallback to
        # avoid repeated browser startup timeouts.
        fallback_text = _html_to_plain_text(html)
        fallback = _render_plain_text_png(
            text=fallback_text,
            width=width,
            min_height=min_height,
            out_path=out_path,
        )
        if fallback:
            logger.warning(
                "render_browser_ws_endpoint is empty and local browser chain is marked failed; using default plain-text image renderer."
            )
        return fallback

    browser = None
    remote_connected = False
    launch_errors: list[str] = []
    chromium_refreshed = False
    try:
        # Lazy import keeps plugin load resilient when optional browser deps are broken.
        from pyppeteer import connect, launch

        if remote_endpoint:
            try:
                browser = await connect(browserWSEndpoint=remote_endpoint)
                remote_connected = True
            except Exception as exc:
                launch_errors.append(f"remote({remote_endpoint}): {exc!s}")
                logger.warning(
                    f"Remote browser connect failed for {remote_endpoint}: {exc!s}"
                )

        launch_kwargs: dict[str, Any] = {
            "headless": True,
            # Rendering now runs in worker threads. Disable signal handlers to
            # avoid thread-context startup issues.
            "handleSIGINT": False,
            "handleSIGTERM": False,
            "handleSIGHUP": False,
            "args": [
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--disable-software-rasterizer",
                "--no-zygote",
                "--single-process",
                "--no-first-run",
                "--no-default-browser-check",
            ],
            "userDataDir": str(temp_dir / "pyppeteer_profile"),
        }

        if browser is None:
            executable_candidates: list[str] = []
            executable_path = _find_browser_executable()
            if executable_path:
                executable_candidates.append(executable_path)
            else:
                if _prepare_chromium_if_needed():
                    logger.warning(
                        "Using downloaded pyppeteer Chromium because local browser was not found."
                    )
                    downloaded = _downloaded_chromium_executable()
                    if downloaded:
                        _ensure_executable_permission(downloaded)
                        executable_candidates.append(downloaded)
                else:
                    logger.warning(
                        "Failed to render html snapshot: browser executable not found and "
                        "Chromium download failed; set WF_HTML_RENDER_BROWSER to Edge/Chrome path"
                    )
                    if not remote_endpoint:
                        _mark_empty_remote_local_chain_failed()
                    fallback_text = _html_to_plain_text(html)
                    return _render_plain_text_png(
                        text=fallback_text,
                        width=width,
                        min_height=min_height,
                        out_path=out_path,
                    )

            for candidate in list(executable_candidates):
                try:
                    kwargs = dict(launch_kwargs)
                    kwargs["executablePath"] = candidate
                    browser = await launch(**kwargs)
                    break
                except Exception as exc:
                    launch_errors.append(f"{candidate}: {exc!s}")
                    logger.warning(
                        f"Browser launch attempt failed for {candidate}: {exc!s}"
                    )
                    missing_libs = _detect_missing_shared_libs(candidate)
                    if missing_libs:
                        logger.warning(
                            "Chromium missing shared libs: " + ", ".join(missing_libs)
                        )

                    if not chromium_refreshed and _looks_like_downloaded_chromium(candidate):
                        chromium_refreshed = True
                        refreshed = _refresh_downloaded_chromium()
                        if refreshed and refreshed not in executable_candidates:
                            executable_candidates.append(refreshed)

            if browser is None:
                try:
                    browser = await launch(**launch_kwargs)
                except Exception as exc:
                    launch_errors.append(f"default: {exc!s}")
                    detail = " | ".join(launch_errors[-3:]) if launch_errors else str(exc)
                    logger.warning(f"Failed to render html snapshot: {detail}")
                    if not remote_endpoint:
                        _mark_empty_remote_local_chain_failed()
                    fallback_text = _html_to_plain_text(html)
                    return _render_plain_text_png(
                        text=fallback_text,
                        width=width,
                        min_height=min_height,
                        out_path=out_path,
                    )

        page = await browser.newPage()
        await page.setViewport(
            {
                "width": max(420, int(width)),
                "height": max(320, int(min_height)),
                "deviceScaleFactor": 2,
            }
        )
        await page.setContent(html)
        await page.waitFor(120)

        content_height = await page.evaluate(
            """
            () => {
              const body = document.body;
              const doc = document.documentElement;
              return Math.ceil(Math.max(
                body ? body.scrollHeight : 0,
                body ? body.offsetHeight : 0,
                doc ? doc.clientHeight : 0,
                doc ? doc.scrollHeight : 0,
                doc ? doc.offsetHeight : 0,
              ));
            }
            """
        )

        await page.setViewport(
            {
                "width": max(420, int(width)),
                "height": max(320, int(content_height or min_height)),
                "deviceScaleFactor": 2,
            }
        )

        await page.screenshot(
            {
                "path": str(out_path),
                "fullPage": True,
                "type": "png",
            }
        )

        return str(out_path)
    except Exception as exc:
        logger.warning(f"Failed to render html snapshot: {exc!s}")
        if not remote_endpoint:
            _mark_empty_remote_local_chain_failed()
        fallback_text = _html_to_plain_text(html)
        fallback = _render_plain_text_png(
            text=fallback_text,
            width=width,
            min_height=min_height,
            out_path=out_path,
        )
        if fallback:
            logger.warning("Using plain-text fallback image because browser snapshot failed.")
        return fallback
    finally:
        if browser is not None:
            try:
                if remote_connected:
                    await browser.disconnect()
                else:
                    await browser.close()
            except Exception:
                pass


def _render_html_to_png_file_worker(
    *,
    html: str,
    width: int,
    prefix: str,
    min_height: int,
) -> str | None:
    # Run pyppeteer in a dedicated thread with its own event loop.
    return asyncio.run(
        _render_html_to_png_file_impl(
            html=html,
            width=width,
            prefix=prefix,
            min_height=min_height,
        )
    )


async def render_html_to_png_file(
    *,
    html: str,
    width: int,
    prefix: str,
    min_height: int = 720,
) -> str | None:
    loop = asyncio.get_running_loop()
    fn = partial(
        _render_html_to_png_file_worker,
        html=html,
        width=width,
        prefix=prefix,
        min_height=min_height,
    )
    return await loop.run_in_executor(_HTML_RENDER_EXECUTOR, fn)
