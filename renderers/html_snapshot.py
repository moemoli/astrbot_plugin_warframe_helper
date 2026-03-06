from __future__ import annotations

import base64
import mimetypes
import os
import uuid
from pathlib import Path
from typing import Any

from astrbot.api import logger
from astrbot.core.utils.astrbot_path import get_astrbot_temp_path


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
    # Prefer user-provided path, then common Edge/Chrome install paths on Windows.
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
    ]
    for p in candidates:
        if p and Path(p).exists():
            return p
    return None


async def render_html_to_png_file(
    *,
    html: str,
    width: int,
    prefix: str,
    min_height: int = 720,
) -> str | None:
    temp_dir = Path(get_astrbot_temp_path())
    temp_dir.mkdir(parents=True, exist_ok=True)
    out_path = temp_dir / f"{prefix}_{uuid.uuid4().hex}.png"

    browser = None
    try:
        # Lazy import keeps plugin load resilient when optional browser deps are broken.
        from pyppeteer import launch

        launch_kwargs: dict[str, Any] = {
            "headless": True,
            "args": [
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
            ],
        }
        executable_path = _find_browser_executable()
        if executable_path:
            launch_kwargs["executablePath"] = executable_path

        browser = await launch(**launch_kwargs)
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
