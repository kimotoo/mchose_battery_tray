# -*- coding: utf-8 -*-
"""UI 与任务栏嵌入式交互包导出。"""

from .autostart import is_autostart_enabled, set_autostart, shortcut_path
from .notifier import LowBatteryNotifier
from .settings import UserSettings, load_user_settings, save_user_settings, settings_path
from .renderer import (
    format_device_bar_text,
    image_to_premultiplied_bgra,
    measure_taskbar_widget,
    measure_widget_by_style,
    render_mouse_capsule_icon,
    render_mouse_capsule_widget,
    render_taskbar_widget,
    render_tray_icon,
    render_tray_icon_by_style,
    render_widget_by_style,
    resolve_arc_color,
)
from .theme import foreground_color, is_light_taskbar
from .tray_app import TrayApplication, acquire_single_instance, format_tooltip
from .win32_taskbar import (
    RectTuple,
    TaskbarLayoutResult,
    Win32TaskbarHost,
    compute_win10_layout,
)

__all__ = [
    "LowBatteryNotifier",
    "RectTuple",
    "TaskbarLayoutResult",
    "TrayApplication",
    "UserSettings",
    "Win32TaskbarHost",
    "acquire_single_instance",
    "compute_win10_layout",
    "foreground_color",
    "format_device_bar_text",
    "format_tooltip",
    "image_to_premultiplied_bgra",
    "is_autostart_enabled",
    "is_light_taskbar",
    "load_user_settings",
    "measure_taskbar_widget",
    "measure_widget_by_style",
    "render_mouse_capsule_icon",
    "render_mouse_capsule_widget",
    "render_taskbar_widget",
    "render_tray_icon",
    "render_tray_icon_by_style",
    "render_widget_by_style",
    "resolve_arc_color",
    "save_user_settings",
    "set_autostart",
    "settings_path",
    "shortcut_path",
]
