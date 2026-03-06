from __future__ import annotations

import asyncio
import base64
import mimetypes
import os
import platform
import shutil
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from pathlib import Path
from typing import Any

from astrbot.api import logger
from astrbot.core.utils.astrbot_path import get_astrbot_temp_path


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


def _ensure_pyppeteer_home() -> Path:
    # In Docker, HOME may be read-only or ephemeral. Keep Chromium/cache under
    # AstrBot temp dir so download and execution are reliable.
    temp_dir = Path(get_astrbot_temp_path())
    pyppeteer_home = temp_dir / "pyppeteer"
    pyppeteer_home.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("PYPPETEER_HOME", str(pyppeteer_home))
    return pyppeteer_home


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
    # Prefer user-provided path, then common Edge/Chrome install paths.
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

    # Also probe PATH for system-installed browsers.
    for name in ("msedge", "msedge.exe", "chrome", "chrome.exe", "chromium", "chromium-browser"):
        found = shutil.which(name)
        if found:
            candidates.append(found)

    seen: set[str] = set()
    for p in candidates:
        pp = str(p or "").strip()
        if not pp:
            continue
        if pp in seen:
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
        if callable(exe):
            path = str(exe() or "").strip()
        else:
            path = str(exe or "").strip()
        if path and Path(path).exists():
            return path
    except Exception:
        return None
    return None


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
                _CHROMIUM_PREPARED = True
                return True

            logger.warning(
                "Chromium download did not provide a usable executable."
            )
            return False
        except Exception as exc:
            logger.warning(f"Chromium download failed: {exc!s}")
            return False


async def _render_html_to_png_file_impl(
    *,
    html: str,
    width: int,
    prefix: str,
    min_height: int = 720,
) -> str | None:
    temp_dir = Path(get_astrbot_temp_path())
    temp_dir.mkdir(parents=True, exist_ok=True)
    out_path = temp_dir / f"{prefix}_{uuid.uuid4().hex}.png"
    _ensure_pyppeteer_home()

    browser = None
    launch_errors: list[str] = []
    try:
        # Lazy import keeps plugin load resilient when optional browser deps are broken.
        from pyppeteer import launch

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
                "--no-first-run",
                "--no-default-browser-check",
            ],
            "userDataDir": str(temp_dir / "pyppeteer_profile"),
        }
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
                    executable_candidates.append(downloaded)
            else:
                logger.warning(
                    "Failed to render html snapshot: browser executable not found and "
                    "Chromium download failed; set WF_HTML_RENDER_BROWSER to Edge/Chrome path"
                )
                return None

        for candidate in executable_candidates:
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

        if browser is None:
            try:
                # Last resort: let pyppeteer decide executable from its own config.
                browser = await launch(**launch_kwargs)
            except Exception as exc:
                launch_errors.append(f"default: {exc!s}")
                detail = " | ".join(launch_errors[-3:]) if launch_errors else str(exc)
                logger.warning(f"Failed to render html snapshot: {detail}")
                return None

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
        return None
    finally:
        if browser is not None:
            try:
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
