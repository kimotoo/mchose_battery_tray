# -*- coding: utf-8 -*-
"""Windows 任务栏浅色/深色主题只读检测。"""

import sys
from typing import Optional, Tuple


DARK_THEME_FG: Tuple[int, int, int, int] = (255, 255, 255, 255)   # 深色任务栏：纯白
LIGHT_THEME_FG: Tuple[int, int, int, int] = (32, 32, 32, 255)     # 浅色任务栏：深黑


def is_light_taskbar() -> bool:
    """只读查询 Windows 注册表判断任务栏是否为浅色主题（绝不写入注册表）。"""
    if sys.platform != "win32":
        return False
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
            0,
            winreg.KEY_READ,
        ) as key:
            light, _ = winreg.QueryValueEx(key, "SystemUsesLightTheme")
            return bool(light)
    except Exception:
        return False


def foreground_color(light_theme: Optional[bool] = None) -> Tuple[int, int, int, int]:
    """根据任务栏主题返回基础前景线条颜色。"""
    light = is_light_taskbar() if light_theme is None else light_theme
    return LIGHT_THEME_FG if light else DARK_THEME_FG
