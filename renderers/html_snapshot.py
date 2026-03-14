from __future__ import annotations

import asyncio
import base64
import mimetypes
import os
import platform
import shutil
import sys
import uuid
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


_HTML_RENDER_CONNECT_TIMEOUT_SEC = max(1, _env_int("WF_HTML_RENDER_CONNECT_TIMEOUT", 4))
_HTML_RENDER_LAUNCH_TIMEOUT_SEC = max(2, _env_int("WF_HTML_RENDER_LAUNCH_TIMEOUT", 8))
_HTML_RENDER_PAGE_TIMEOUT_SEC = max(2, _env_int("WF_HTML_RENDER_PAGE_TIMEOUT", 6))
_IMAGE_CACHE_DIR_OVERRIDE = ""

_PLAYWRIGHT_INSTALL_LOCK = asyncio.Lock()
_PLAYWRIGHT_INSTALL_DONE = False
_PLAYWRIGHT_PREPARE_TASK: asyncio.Task[Any] | None = None
_PLAYWRIGHT_PREPARING_SKIP_LOGGED = False


class _PlaywrightRuntime:
    def __init__(self) -> None:
        self._playwright = None
        self._lock = asyncio.Lock()

    async def get(self):
        async with self._lock:
            if self._playwright is not None:
                return self._playwright
            try:
                from playwright.async_api import async_playwright
            except Exception as exc:
                logger.warning(f"playwright import failed: {exc!s}")
                return None

            try:
                self._playwright = await async_playwright().start()
                return self._playwright
            except Exception as exc:
                logger.warning(f"playwright startup failed: {exc!s}")
                return None


_PLAYWRIGHT_RUNTIME = _PlaywrightRuntime()


def configure_image_cache(
    *,
    cache_dir: str | None = None,
) -> None:
    global _IMAGE_CACHE_DIR_OVERRIDE

    if cache_dir is not None:
        _IMAGE_CACHE_DIR_OVERRIDE = str(cache_dir or "").strip()


def _get_image_cache_dir() -> Path:
    override = str(_IMAGE_CACHE_DIR_OVERRIDE or "").strip()
    if not override:
        return Path(get_astrbot_temp_path())

    path = Path(override).expanduser()
    if not path.is_absolute():
        path = Path(get_astrbot_temp_path()) / path
    return path


async def _run_playwright_cli(
    args: list[str], *, timeout_sec: int, env: dict[str, str] | None = None
) -> tuple[int, str]:
    cmd = [sys.executable, "-m", "playwright", *args]
    proc_env = os.environ.copy()
    if env:
        proc_env.update(env)
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=proc_env,
        )
    except Exception as exc:
        return 1, f"spawn failed: {exc!s}"

    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=float(timeout_sec))
    except TimeoutError:
        try:
            proc.kill()
        except Exception:
            pass
        return 124, "timeout"
    except Exception as exc:
        return 1, f"run failed: {exc!s}"

    text = (out or b"").decode("utf-8", errors="ignore").strip()
    return int(proc.returncode or 0), text


async def ensure_playwright_runtime_ready(*, browser: str | None = None) -> None:
    global _PLAYWRIGHT_INSTALL_DONE

    if _PLAYWRIGHT_INSTALL_DONE:
        return

    if _find_browser_executable():
        _PLAYWRIGHT_INSTALL_DONE = True
        return

    async with _PLAYWRIGHT_INSTALL_LOCK:
        if _PLAYWRIGHT_INSTALL_DONE:
            return

        target_browser = (
            str(browser or os.environ.get("WF_PLAYWRIGHT_BROWSER") or "chromium")
            .strip()
            .lower()
        )
        if target_browser not in {"chromium", "firefox", "webkit"}:
            target_browser = "chromium"

        install_deps_timeout = max(
            60, _env_int("WF_PLAYWRIGHT_INSTALL_DEPS_TIMEOUT", 600)
        )
        install_timeout = max(60, _env_int("WF_PLAYWRIGHT_INSTALL_TIMEOUT", 600))
        is_linux = platform.system().lower() == "linux"

        rc_deps, out_deps = await _run_playwright_cli(
            ["install-deps", target_browser],
            timeout_sec=install_deps_timeout,
        )
        if rc_deps != 0:
            # Windows/macOS often don't require or support install-deps; keep startup non-blocking.
            logger.warning(
                "playwright install-deps failed/skipped "
                f"(code={rc_deps}, browser={target_browser}): {out_deps[-300:]}"
            )
        else:
            logger.info(f"playwright install-deps success: {target_browser}")

        install_attempts: list[dict[str, str] | None] = [None]
        if is_linux:
            if not os.environ.get("PLAYWRIGHT_DOWNLOAD_HOST"):
                install_attempts.insert(
                    0,
                    {
                        "PLAYWRIGHT_DOWNLOAD_HOST": "https://cdn.npmmirror.com/binaries/playwright",
                        "PLAYWRIGHT_CHROMIUM_DOWNLOAD_HOST": "https://cdn.npmmirror.com/binaries/chrome-for-testing",
                    },
                )
                install_attempts.insert(
                    1,
                    {
                        "PLAYWRIGHT_DOWNLOAD_HOST": "https://npmmirror.com/mirrors/playwright",
                        "PLAYWRIGHT_CHROMIUM_DOWNLOAD_HOST": "https://cdn.npmmirror.com/binaries/chrome-for-testing",
                    },
                )

        rc_install = 1
        out_install = ""
        for idx, install_env in enumerate(install_attempts, start=1):
            if install_env:
                logger.info(
                    f"playwright install attempt#{idx} with mirror host: {install_env.get('PLAYWRIGHT_DOWNLOAD_HOST', '')}"
                )
            else:
                logger.info(f"playwright install attempt#{idx} with default host")

            rc_install, out_install = await _run_playwright_cli(
                ["install", target_browser],
                timeout_sec=install_timeout,
                env=install_env,
            )
            if rc_install == 0:
                break

        if rc_install != 0:
            logger.warning(
                "playwright install failed "
                f"(code={rc_install}, browser={target_browser}): {out_install[-300:]}"
            )
            return

        logger.info(f"playwright install success: {target_browser}")
        _PLAYWRIGHT_INSTALL_DONE = True


def is_playwright_runtime_preparing() -> bool:
    return _PLAYWRIGHT_PREPARE_TASK is not None and not _PLAYWRIGHT_PREPARE_TASK.done()


def start_playwright_runtime_prepare(*, browser: str | None = None) -> None:
    global _PLAYWRIGHT_INSTALL_DONE, _PLAYWRIGHT_PREPARE_TASK, _PLAYWRIGHT_PREPARING_SKIP_LOGGED

    if _PLAYWRIGHT_PREPARE_TASK is not None and not _PLAYWRIGHT_PREPARE_TASK.done():
        return

    local_browser = _find_browser_executable()
    if local_browser:
        _PLAYWRIGHT_INSTALL_DONE = True
        logger.info(
            f"local browser found, skip playwright install prepare: {local_browser}"
        )
        return

    target_browser = (
        str(browser or os.environ.get("WF_PLAYWRIGHT_BROWSER") or "chromium")
        .strip()
        .lower()
    )
    if target_browser not in {"chromium", "firefox", "webkit"}:
        target_browser = "chromium"

    logger.info(f"playwright preparing started in background: {target_browser}")
    _PLAYWRIGHT_PREPARING_SKIP_LOGGED = False

    async def _runner() -> None:
        try:
            await ensure_playwright_runtime_ready(browser=browser)
            if _PLAYWRIGHT_INSTALL_DONE:
                logger.info(
                    "playwright preparing finished; browser rendering is enabled"
                )
        except Exception as exc:
            logger.warning(f"playwright background prepare failed: {exc!s}")

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return

    _PLAYWRIGHT_PREPARE_TASK = loop.create_task(_runner())


def image_bytes_to_data_uri(
    image_bytes: bytes | None,
    *,
    filename: str = "image.png",
) -> str | None:
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

    for name in ("msedge", "msedge.exe", "chrome", "chrome.exe"):
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


async def _render_page_to_png(
    *,
    browser,
    html: str,
    width: int,
    min_height: int,
    out_path: Path,
) -> str | None:
    page = None
    try:
        timeout_ms = int(float(_HTML_RENDER_PAGE_TIMEOUT_SEC) * 1000)
        page = await browser.new_page(
            viewport={
                "width": max(420, int(width)),
                "height": max(320, int(min_height)),
            },
            device_scale_factor=1.5,
        )
        await page.set_content(html, wait_until="load", timeout=timeout_ms)
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
        await page.set_viewport_size(
            {
                "width": max(420, int(width)),
                "height": max(320, int(content_height or min_height)),
            }
        )
        await page.screenshot(
            path=str(out_path), full_page=True, type="png", timeout=timeout_ms
        )
        return str(out_path)
    except Exception as exc:
        logger.warning(f"Browser render failed: {exc!s}")
        return None
    finally:
        if page is not None:
            try:
                await page.close()
            except Exception:
                pass


async def _render_html_to_png_file_impl(
    *,
    html: str,
    width: int,
    prefix: str,
    min_height: int = 720,
) -> str | None:
    temp_dir = _get_image_cache_dir()
    temp_dir.mkdir(parents=True, exist_ok=True)
    out_path = temp_dir / f"{prefix}_{uuid.uuid4().hex}.png"

    runtime = await _PLAYWRIGHT_RUNTIME.get()
    if runtime is None:
        return None

    executable_path = _find_browser_executable()
    launch_args = [
        "--no-sandbox",
        "--disable-setuid-sandbox",
        "--disable-dev-shm-usage",
        "--disable-gpu",
        "--disable-software-rasterizer",
        "--no-zygote",
        "--no-first-run",
        "--no-default-browser-check",
    ]

    browser = None
    try:
        launch_timeout_ms = int(float(_HTML_RENDER_LAUNCH_TIMEOUT_SEC) * 1000)
        kwargs: dict[str, Any] = {
            "headless": True,
            "args": launch_args,
            "timeout": launch_timeout_ms,
        }
        if executable_path:
            kwargs["executable_path"] = executable_path

        browser = await runtime.chromium.launch(**kwargs)
        rendered = await _render_page_to_png(
            browser=browser,
            html=html,
            width=width,
            min_height=min_height,
            out_path=out_path,
        )
        if rendered:
            return rendered
    except Exception as exc:
        logger.warning(f"Failed to render html snapshot by local playwright: {exc!s}")
    finally:
        if browser is not None:
            try:
                await browser.close()
            except Exception:
                pass

    return None


async def render_html_to_png_file(
    *,
    html: str,
    width: int,
    prefix: str,
    min_height: int = 720,
) -> str | None:
    global _PLAYWRIGHT_PREPARING_SKIP_LOGGED

    if is_playwright_runtime_preparing():
        if not _PLAYWRIGHT_PREPARING_SKIP_LOGGED:
            logger.info(
                "playwright preparing in progress; skip browser render and fallback to plain text"
            )
            _PLAYWRIGHT_PREPARING_SKIP_LOGGED = True
        return None

    return await _render_html_to_png_file_impl(
        html=html,
        width=width,
        prefix=prefix,
        min_height=min_height,
    )
