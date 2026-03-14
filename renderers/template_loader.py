from __future__ import annotations

import contextvars
import re
from pathlib import Path

from jinja2 import Environment

_TEMPLATE_NAME = "default"
_VALID_TEMPLATE_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_CURRENT_COMMAND = contextvars.ContextVar("wf_render_command", default="")
_CURRENT_TEMPLATE_NAME = contextvars.ContextVar("wf_render_template_name", default="")
_JINJA_ENV = Environment(autoescape=True)

_WORLD_CYCLE_COMMANDS = {
    "平原",
    "夜灵平原",
    "地球昼夜",
    "奥布山谷",
    "魔胎之境",
    "双衍王境",
    "轮回奖励",
}

_CRACK_COMMANDS = {
    "裂缝",
    "普通裂缝",
    "钢铁裂缝",
    "九重天裂缝",
}

_EVENT_COMMANDS = {
    "突击",
    "执行官猎杀",
    "警报",
    "入侵",
    "奸商",
    "仲裁",
    "电波",
    "钢铁奖励",
    "集团",
}

_LOOKUP_COMMANDS = {
    "武器",
    "战甲",
    "MOD",
    "掉落",
    "遗物",
}

_SUBSCRIPTION_COMMANDS = {
    "订阅",
    "退订",
    "订阅列表",
}

_GUIDE_COMMANDS = {
    "wf",
    "wfmap",
}


def _normalize_command_key(key: str | None) -> str:
    s = str(key or "").strip()
    if not s:
        return ""
    if s.startswith("/"):
        s = s[1:]
    return s


def set_render_template_name(name: str | None) -> None:
    global _TEMPLATE_NAME
    s = str(name or "").strip()
    if not s or not _VALID_TEMPLATE_RE.match(s):
        _TEMPLATE_NAME = "default"
        return
    _TEMPLATE_NAME = s


def set_current_render_command(command: str | None) -> None:
    _CURRENT_COMMAND.set(_normalize_command_key(command))


def set_current_render_template_name(name: str | None) -> None:
    s = str(name or "").strip()
    if not s:
        _CURRENT_TEMPLATE_NAME.set("")
        return
    if not _VALID_TEMPLATE_RE.match(s):
        _CURRENT_TEMPLATE_NAME.set("")
        return
    _CURRENT_TEMPLATE_NAME.set(s)


def has_render_template_name(name: str | None) -> bool:
    s = str(name or "").strip()
    if not s or not _VALID_TEMPLATE_RE.match(s):
        return False
    base = _plugin_root() / "assets" / "template"
    p = base / s
    return p.exists() and p.is_dir()


def list_available_render_template_names() -> list[str]:
    base = _plugin_root() / "assets" / "template"
    out: list[str] = []
    try:
        if base.exists() and base.is_dir():
            for p in base.iterdir():
                if not p.is_dir():
                    continue
                name = p.name.strip()
                if not _VALID_TEMPLATE_RE.match(name):
                    continue
                out.append(name)
    except Exception:
        pass
    if "default" not in out:
        out.append("default")
    out = sorted(set(out), key=lambda x: x.lower())
    return out


def get_render_template_name() -> str:
    return _TEMPLATE_NAME


def _plugin_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _family_template_filename(command_key: str, filename: str) -> str:
    if filename != "status_list.html":
        return ""

    if command_key in _WORLD_CYCLE_COMMANDS:
        return "cycle_status.html"
    if command_key in _CRACK_COMMANDS:
        return "crack.html"
    if command_key in _EVENT_COMMANDS:
        return "event.html"
    if command_key in _LOOKUP_COMMANDS:
        return "lookup.html"
    if command_key in _SUBSCRIPTION_COMMANDS:
        return "subscription.html"
    if command_key in _GUIDE_COMMANDS:
        return "guide.html"
    return ""


def _template_file_candidates(
    filename: str, template_name: str | None = None
) -> list[Path]:
    selected = (
        str(template_name or "").strip()
        or str(_CURRENT_TEMPLATE_NAME.get() or "").strip()
        or _TEMPLATE_NAME
    )
    base = _plugin_root() / "assets" / "template"
    command_key = _normalize_command_key(_CURRENT_COMMAND.get())
    family_filename = _family_template_filename(command_key, filename)

    out: list[Path] = []
    if family_filename:
        out.append(base / selected / family_filename)
    if command_key:
        out.append(base / selected / f"{command_key}.html")
    out.append(base / selected / filename)
    if family_filename:
        out.append(base / "default" / family_filename)
    if command_key:
        out.append(base / "default" / f"{command_key}.html")
    out.append(base / "default" / filename)
    return out


def load_html_template(
    *,
    filename: str,
    context: dict[str, object],
    template_name: str | None = None,
) -> str:
    tpl = ""
    for path in _template_file_candidates(filename, template_name=template_name):
        try:
            if path.exists() and path.is_file():
                tpl = path.read_text(encoding="utf-8")
                break
        except Exception:
            continue

    if not tpl:
        return ""

    try:
        template = _JINJA_ENV.from_string(tpl)
        return str(template.render(**(context or {})))
    except Exception:
        return ""
