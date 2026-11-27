# -*- coding: utf-8 -*-
"""Windows 任务栏长条与系统托盘双模式主控制器。"""

import ctypes
from datetime import datetime
from pathlib import Path
import threading
from typing import Dict, List, Optional, Tuple

from config import (
    DEFAULT_REFRESH_SEC,
    MUTEX_NAME,
    REFRESH_INTERVAL_OPTIONS,
)
from core.manager import DeviceManager
from core.models import (
    DeviceSlot,
    DisplayMode,
    SlotState,
    TaskbarPlacement,
    TrayMenuAction,
    WidgetStyle,
)

from .autostart import is_autostart_enabled, set_autostart
from .notifier import LowBatteryNotifier
from .settings import UserSettings, save_user_settings
from .renderer import (
    measure_widget_by_style,
    render_tray_icon_by_style,
    render_widget_by_style,
)
from .theme import is_light_taskbar
from .win32_taskbar import Win32TaskbarHost


def acquire_single_instance(mutex_name: str = MUTEX_NAME) -> Tuple[bool, Optional[int]]:
    """通过 Windows 内核命名互斥体防止多开。"""
    try:
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        handle = kernel32.CreateMutexW(None, False, mutex_name)
        if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
            if handle:
                kernel32.CloseHandle(handle)
            return False, None
        return True, handle
    except Exception:
        return True, None


def format_slot_line(state: SlotState) -> str:
    """格式化单个设备槽位的连接状态与电量详情。"""
    prefix = f"{state.display_label} {state.model_name}".strip()
    if state.is_virtual:
        val_str = f"{state.percent}%" if state.percent is not None else "--"
        return f"{prefix}: {val_str}"
    if not state.connected:
        return f"{prefix}: 未连接"
    mode_tag = state.connection_mode.value
    if state.percent is None:
        if state.charging:
            status_note = f"{mode_tag} 充电中"
        elif not state.online:
            status_note = f"{mode_tag} 休眠"
        else:
            status_note = f"{mode_tag} 读取中"
        return f"{prefix}: -- ({status_note})"
    if state.charging:
        return f"{prefix}: {state.percent}% ({mode_tag} 充电中)"
    if not state.online:
        return f"{prefix}: {state.percent}% ({mode_tag} 休眠)"
    return f"{prefix}: {state.percent}% ({mode_tag} 在线)"


def format_tooltip(
    states: Dict[DeviceSlot, SlotState],
    last_refresh_time: Optional[str] = None,
    mouse_only: bool = False,
    max_len: int = 120,
) -> str:
    """组装悬停多行提示文本。"""
    lines = [format_slot_line(states[DeviceSlot.MOUSE])]
    if not mouse_only:
        lines.append(format_slot_line(states[DeviceSlot.KEYBOARD]))
    if last_refresh_time:
        lines.append(f"更新: {last_refresh_time}")
    text = "\n".join(lines)
    if len(text) > max_len:
        return text[:max_len]
    return text


class TrayApplication:
    """迈从 G3 V2 电量监控主控（支持任务栏常驻条与仅托盘图标双模式切换）。"""

    def __init__(
        self,
        manager: DeviceManager,
        refresh_sec: int = DEFAULT_REFRESH_SEC,
        entry_script=None,
        host=None,
        display_mode: DisplayMode = DisplayMode.TASKBAR_BAR,
        widget_style: WidgetStyle = WidgetStyle.DUAL_RING,
        taskbar_placement: TaskbarPlacement = TaskbarPlacement.LEFT_OF_SIBLINGS,
        settings_path: Optional[Path] = None,
    ) -> None:
        self._manager = manager
        self._refresh_sec = refresh_sec
        self._entry_script = entry_script
        self._display_mode = display_mode
        self._widget_style = widget_style
        self._taskbar_placement = taskbar_placement
        self._settings_path = settings_path
        self._notifier = LowBatteryNotifier()

        self._stop_event = threading.Event()
        self._wake_event = threading.Event()
        self._refresh_lock = threading.Lock()
        self._states_lock = threading.Lock()
        self._states: Dict[DeviceSlot, SlotState] = self._manager.get_all_states()
        self._last_refresh_time: Optional[str] = None
        self._manager.set_async_update_callback(self._wake_event.set)

        self._host = host or Win32TaskbarHost(
            on_double_click=self._on_refresh_now,
            build_menu_items=self._build_menu_actions,
            render_widget_fn=self._render_widget,
            measure_widget_fn=self._measure_widget,
            get_tooltip_text=self._current_tooltip,
            get_balloon_icon_img=self._render_balloon_icon_img,
            is_light_theme_fn=is_light_taskbar,
            get_tray_icon_img=self._render_tray_icon_img,
            initial_mode=self._display_mode,
            initial_placement=self._taskbar_placement,
            on_device_change=self._on_refresh_now,
        )

    def _current_states_snapshot(self) -> Tuple[Dict[DeviceSlot, SlotState], Optional[str]]:
        with self._states_lock:
            return dict(self._states), self._last_refresh_time

    def _current_tooltip(self) -> str:
        states, last_time = self._current_states_snapshot()
        return format_tooltip(
            states,
            last_refresh_time=last_time,
            mouse_only=(self._widget_style == WidgetStyle.MOUSE_CAPSULE),
        )

    def _measure_widget(self, dpi: int, bar_height: int) -> Tuple[int, int]:
        return measure_widget_by_style(style=self._widget_style, dpi=dpi, bar_height=bar_height)

    def _render_widget(
        self,
        dpi: int,
        bar_height: int,
        hovered: bool,
        opaque_bg: Optional[Tuple[int, int, int]],
    ):
        states, _ = self._current_states_snapshot()
        mouse_state = states[DeviceSlot.MOUSE]
        kb_state = states[DeviceSlot.KEYBOARD]
        return render_widget_by_style(
            style=self._widget_style,
            mouse_percent=mouse_state.percent,
            keyboard_percent=kb_state.percent,
            mouse_connected=mouse_state.connected,
            mouse_online=mouse_state.online,
            mouse_charging=mouse_state.charging,
            keyboard_connected=kb_state.connected,
            keyboard_charging=kb_state.charging,
            dpi=dpi,
            bar_height=bar_height,
            hovered=hovered,
            opaque_bg=opaque_bg,
        )

    def _render_style_icon(self, size: int, compact_numeric: bool):
        states, _ = self._current_states_snapshot()
        mouse_state = states[DeviceSlot.MOUSE]
        kb_state = states[DeviceSlot.KEYBOARD]
        return render_tray_icon_by_style(
            style=self._widget_style,
            mouse_percent=mouse_state.percent,
            keyboard_percent=kb_state.percent,
            mouse_connected=mouse_state.connected,
            mouse_online=mouse_state.online,
            mouse_charging=mouse_state.charging,
            keyboard_connected=kb_state.connected,
            keyboard_charging=kb_state.charging,
            size=size,
            compact_numeric=compact_numeric,
        )

    def _render_tray_icon_img(self, size: int = 16):
        return self._render_style_icon(size=size, compact_numeric=True)

    def _render_balloon_icon_img(self):
        return self._render_style_icon(size=32, compact_numeric=False)

    def _make_interval_action(self, sec: int):
        return lambda: self._on_select_interval(sec)

    def _make_interval_checked(self, sec: int):
        return lambda: self._refresh_sec == sec

    def _make_mode_action(self, mode: DisplayMode):
        return lambda: self._on_select_display_mode(mode)

    def _make_mode_checked(self, mode: DisplayMode):
        return lambda: self._display_mode == mode

    def _make_style_action(self, style: WidgetStyle):
        return lambda: self._on_select_widget_style(style)

    def _make_style_checked(self, style: WidgetStyle):
        return lambda: self._widget_style == style

    def _make_placement_action(self, placement: TaskbarPlacement):
        return lambda: self._on_select_taskbar_placement(placement)

    def _make_placement_checked(self, placement: TaskbarPlacement):
        return lambda: self._taskbar_placement == placement

    def _build_menu_actions(self) -> List[TrayMenuAction]:
        mode_children = [
            TrayMenuAction(
                label="任务栏常驻条",
                callback=self._make_mode_action(DisplayMode.TASKBAR_BAR),
                checked=self._make_mode_checked(DisplayMode.TASKBAR_BAR),
                radio=True,
            ),
            TrayMenuAction(
                label="仅托盘图标",
                callback=self._make_mode_action(DisplayMode.TRAY_ICON_ONLY),
                checked=self._make_mode_checked(DisplayMode.TRAY_ICON_ONLY),
                radio=True,
            ),
        ]

        style_children = [
            TrayMenuAction(
                label="双环双行 (鼠标 + 键盘)",
                callback=self._make_style_action(WidgetStyle.DUAL_RING),
                checked=self._make_style_checked(WidgetStyle.DUAL_RING),
                radio=True,
            ),
            TrayMenuAction(
                label="鼠标图标 + 电量 (仅鼠标)",
                callback=self._make_style_action(WidgetStyle.MOUSE_CAPSULE),
                checked=self._make_style_checked(WidgetStyle.MOUSE_CAPSULE),
                radio=True,
            ),
        ]

        placement_children = [
            TrayMenuAction(
                label="位于 TrafficMonitor 等左侧 (推荐)",
                callback=self._make_placement_action(TaskbarPlacement.LEFT_OF_SIBLINGS),
                checked=self._make_placement_checked(TaskbarPlacement.LEFT_OF_SIBLINGS),
                radio=True,
            ),
            TrayMenuAction(
                label="位于 TrafficMonitor 等右侧 (贴近托盘)",
                callback=self._make_placement_action(TaskbarPlacement.RIGHT_OF_SIBLINGS),
                checked=self._make_placement_checked(TaskbarPlacement.RIGHT_OF_SIBLINGS),
                radio=True,
            ),
        ]

        interval_children = [
            TrayMenuAction(
                label=label,
                callback=self._make_interval_action(sec),
                checked=self._make_interval_checked(sec),
                radio=True,
            )
            for label, sec in REFRESH_INTERVAL_OPTIONS
        ]

        actions: List[TrayMenuAction] = [
            TrayMenuAction(label="立即刷新", callback=self._on_refresh_now),
            TrayMenuAction(label="显示模式", children=mode_children),
            TrayMenuAction(label="显示样式", children=style_children),
            TrayMenuAction(label="停靠位置", children=placement_children),
            TrayMenuAction(label="刷新间隔", children=interval_children),
        ]

        extra_actions = self._manager.collect_menu_actions()
        if extra_actions:
            actions.append(TrayMenuAction(label="---"))
            actions.extend(extra_actions)

        actions.extend(
            [
                TrayMenuAction(label="---"),
                TrayMenuAction(
                    label="开机自启",
                    callback=self._on_toggle_autostart,
                    checked=is_autostart_enabled,
                ),
                TrayMenuAction(label="退出", callback=self._on_quit),
            ]
        )
        return actions

    def _perform_refresh(self) -> None:
        with self._refresh_lock:
            states = self._manager.refresh_all()
            now_str = datetime.now().strftime("%H:%M:%S")
            with self._states_lock:
                self._states = states
                self._last_refresh_time = now_str

            try:
                self._host.post_refresh_ui()
            except Exception:
                pass

            for slot in (DeviceSlot.MOUSE, DeviceSlot.KEYBOARD):
                alert = self._notifier.evaluate(states[slot])
                if alert is not None:
                    title, message = alert
                    try:
                        self._host.enqueue_balloon(title, message)
                    except Exception:
                        pass

    def _worker_loop(self) -> None:
        while not self._stop_event.is_set():
            self._perform_refresh()
            self._wake_event.wait(timeout=self._refresh_sec)
            self._wake_event.clear()

    def _on_refresh_now(self) -> None:
        self._wake_event.set()

    def _save_settings(self) -> None:
        if self._settings_path is None:
            return
        save_user_settings(
            UserSettings(
                display_mode=self._display_mode,
                widget_style=self._widget_style,
                taskbar_placement=self._taskbar_placement,
                refresh_sec=self._refresh_sec,
            ),
            path=self._settings_path,
        )

    def _on_select_interval(self, seconds: int) -> None:
        if self._refresh_sec == seconds:
            return
        self._refresh_sec = seconds
        self._save_settings()
        self._wake_event.set()

    def _on_select_display_mode(self, mode: DisplayMode) -> None:
        if self._display_mode == mode:
            return
        self._display_mode = mode
        self._save_settings()
        try:
            self._host.set_display_mode(mode)
        except Exception:
            pass

    def _on_select_widget_style(self, style: WidgetStyle) -> None:
        if self._widget_style == style:
            return
        self._widget_style = style
        self._save_settings()
        try:
            self._host.force_relayout_and_redraw()
        except Exception:
            pass

    def _on_select_taskbar_placement(self, placement: TaskbarPlacement) -> None:
        if self._taskbar_placement == placement:
            return
        self._taskbar_placement = placement
        self._save_settings()
        try:
            self._host.set_taskbar_placement(placement)
        except Exception:
            pass

    def _on_toggle_autostart(self) -> None:
        set_autostart(not is_autostart_enabled(), entry_script=self._entry_script)

    def _on_quit(self) -> None:
        self._stop_event.set()
        self._wake_event.set()
        self._manager.stop_drivers()
        try:
            self._host.request_quit()
        except Exception:
            pass

    def run(self) -> None:
        self._host.create()
        worker = threading.Thread(target=self._worker_loop, name="MchosePollWorker", daemon=True)
        worker.start()
        try:
            self._host.run_message_loop()
        finally:
            self._stop_event.set()
            self._wake_event.set()
            self._manager.stop_drivers()
