# -*- coding: utf-8 -*-
"""绿色免注册表用户偏好持久化（%APPDATA%\\mchose_battery_tray\\settings.json）。"""

from dataclasses import dataclass
import json
import os
from pathlib import Path
from typing import Any, Optional, Type, TypeVar

from config import DEFAULT_REFRESH_SEC, REFRESH_INTERVAL_OPTIONS
from core.models import DisplayMode, TaskbarPlacement, WidgetStyle


SETTINGS_DIR_NAME = "mchose_battery_tray"
SETTINGS_FILE_NAME = "settings.json"
_VALID_REFRESH_SECONDS = frozenset(sec for _label, sec in REFRESH_INTERVAL_OPTIONS)
_EnumT = TypeVar("_EnumT", DisplayMode, WidgetStyle, TaskbarPlacement)


@dataclass(frozen=True)
class UserSettings:
    """可持久化的托盘显示与轮询偏好设置。"""

    display_mode: DisplayMode = DisplayMode.TASKBAR_BAR
    widget_style: WidgetStyle = WidgetStyle.DUAL_RING
    taskbar_placement: TaskbarPlacement = TaskbarPlacement.LEFT_OF_SIBLINGS
    refresh_sec: int = DEFAULT_REFRESH_SEC


def roaming_appdata_dir() -> Path:
    """获取当前用户的 Roaming AppData 目录。"""
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata)
    return Path.home() / "AppData" / "Roaming"


def settings_path() -> Path:
    """返回用户配置文件完整路径（%APPDATA%\\mchose_battery_tray\\settings.json）。"""
    return roaming_appdata_dir() / SETTINGS_DIR_NAME / SETTINGS_FILE_NAME


def _parse_enum(enum_cls: Type[_EnumT], raw: Any, default: _EnumT) -> _EnumT:
    if isinstance(raw, str):
        try:
            return enum_cls(raw)
        except ValueError:
            return default
    return default


def _parse_refresh_sec(raw: Any, default: int) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int):
        return default
    if raw in _VALID_REFRESH_SECONDS:
        return raw
    return default


def load_user_settings(path: Optional[Path] = None) -> UserSettings:
    """读取用户配置；文件缺失、损坏或字段非法时安全回退默认值。"""
    target = path or settings_path()
    defaults = UserSettings()
    try:
        if not target.exists():
            return defaults
        raw_text = target.read_text(encoding="utf-8")
        data = json.loads(raw_text)
    except Exception:
        return defaults

    if not isinstance(data, dict):
        return defaults

    return UserSettings(
        display_mode=_parse_enum(DisplayMode, data.get("display_mode"), defaults.display_mode),
        widget_style=_parse_enum(WidgetStyle, data.get("widget_style"), defaults.widget_style),
        taskbar_placement=_parse_enum(
            TaskbarPlacement,
            data.get("taskbar_placement"),
            defaults.taskbar_placement,
        ),
        refresh_sec=_parse_refresh_sec(data.get("refresh_sec"), defaults.refresh_sec),
    )


def save_user_settings(settings: UserSettings, path: Optional[Path] = None) -> bool:
    """以原子替换方式将用户配置写入 UTF-8 JSON 文件。"""
    target = path or settings_path()
    tmp_path = target.with_name(f"{target.name}.tmp")
    payload = {
        "display_mode": settings.display_mode.value,
        "widget_style": settings.widget_style.value,
        "taskbar_placement": settings.taskbar_placement.value,
        "refresh_sec": int(settings.refresh_sec),
    }
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(tmp_path, target)
        return True
    except Exception:
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except Exception:
            pass
        return False
