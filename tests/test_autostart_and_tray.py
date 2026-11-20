# -*- coding: utf-8 -*-
"""开机自启路径解析、Win32 任务栏结构体、模式与样式切换集成测试。"""

import ctypes
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.interfaces import BaseDeviceDriver, IMenuContributor
from core.manager import DeviceManager
from core.models import (
    BatteryReading,
    ConnectionMode,
    DevicePollResult,
    DeviceSlot,
    DisplayMode,
    TaskbarPlacement,
    TrayMenuAction,
    WidgetStyle,
)
from drivers.virtual_keyboard import VirtualKeyboardDriver
from ui import autostart
from ui.renderer import render_tray_icon
from ui.tray_app import TrayApplication
from ui.win32_taskbar import (
    DBT_DEVICEARRIVAL,
    DBT_DEVICEREMOVECOMPLETE,
    DBT_DEVNODES_CHANGED,
    GWL_EXSTYLE,
    GWL_STYLE,
    GW_CHILD,
    GW_HWNDNEXT,
    GW_HWNDPREV,
    HWND_TOP,
    LAYOUT_POLL_MS,
    NOTIFYICONDATAW,
    RectTuple,
    TIMER_DEVICECHANGE_ID,
    WM_DEVICECHANGE,
    WM_SETTINGCHANGE,
    WM_TIMER,
    WS_CHILD,
    WS_CLIPSIBLINGS,
    WS_EX_TOOLWINDOW,
    WS_EX_TOPMOST,
    WS_POPUP,
    Win32TaskbarHost,
    _init_win32_apis,
    create_hicon_from_image,
)


class _FakeTaskbarHost:
    def __init__(self):
        self.refresh_count = 0
        self.alerts = []
        self.quit_called = False
        self.created = False
        self.modes = []
        self.placements = []
        self.relayout_count = 0

    def create(self):
        self.created = True

    def post_refresh_ui(self):
        self.refresh_count += 1

    def enqueue_balloon(self, title, message):
        self.alerts.append((title, message))

    def set_display_mode(self, mode):
        self.modes.append(mode)

    def set_taskbar_placement(self, placement):
        self.placements.append(placement)

    def force_relayout_and_redraw(self):
        self.relayout_count += 1

    def request_quit(self):
        self.quit_called = True

    def run_message_loop(self):
        pass


class _ScriptedMouseDriver(BaseDeviceDriver, IMenuContributor):
    slot = DeviceSlot.MOUSE
    driver_id = "scripted"
    display_name = "G3 V2"
    priority = 1

    def __init__(self, percent):
        self._percent = percent
        self.clicked = False

    def poll(self, hid_devices):
        return DevicePollResult(
            hardware_connected=True,
            reading=BatteryReading(
                percent=self._percent,
                charging=False,
                online=True,
                model_name="G3 V2",
                connection_mode=ConnectionMode.WIRELESS_24G,
            ),
        )

    def get_menu_actions(self):
        return [
            TrayMenuAction(
                label="回报率",
                children=[
                    TrayMenuAction(
                        label="1000 Hz",
                        callback=lambda: setattr(self, "clicked", True),
                        checked=lambda: self.clicked,
                    )
                ],
            )
        ]


class TestAutostartAndTray(unittest.TestCase):
    def test_resolve_launch_target_source_and_frozen(self):
        entry = Path("D:/tools/mchose_battery.py")
        with patch.object(sys, "frozen", False, create=True):
            target, args, workdir = autostart._resolve_launch_target(entry)
            self.assertIn("python", target.name.lower())
            self.assertIn("mchose_battery.py", args)
            self.assertEqual(workdir, entry.resolve().parent)

        with patch.object(sys, "frozen", True, create=True):
            with patch.object(sys, "executable", "D:/tools/迈从电量托盘.exe"):
                target, args, workdir = autostart._resolve_launch_target(entry)
                self.assertEqual(target, Path("D:/tools/迈从电量托盘.exe").resolve())
                self.assertEqual(args, "")
                self.assertEqual(workdir, Path("D:/tools").resolve())

    def test_tray_refresh_mode_style_switch_and_menu_actions(self):
        mgr = DeviceManager(enumerate_fn=lambda: [])
        drv = _ScriptedMouseDriver(percent=10)
        mgr.register_driver(drv)
        mgr.register_driver(VirtualKeyboardDriver())

        fake_host = _FakeTaskbarHost()
        app = TrayApplication(manager=mgr, refresh_sec=60, host=fake_host)
        app._perform_refresh()

        self.assertEqual(fake_host.refresh_count, 1)
        self.assertEqual(len(fake_host.alerts), 1)
        self.assertIn("10%", fake_host.alerts[0][1])
        self.assertIn("鼠标 G3 V2: 10%", app._current_tooltip())
        self.assertIn("键盘 MCHOSE: 未连接", app._current_tooltip())

        # 再次刷新同电量不应重复通知
        app._perform_refresh()
        self.assertEqual(fake_host.refresh_count, 2)
        self.assertEqual(len(fake_host.alerts), 1)

        # 验证菜单树精简后不再重复展示悬浮状态，并校验显示模式/样式切换
        actions = app._build_menu_actions()
        labels = [a.label for a in actions]
        self.assertEqual(labels[0], "立即刷新")
        self.assertFalse(any("2.4G" in lbl for lbl in labels))
        self.assertFalse(any("上次刷新:" in lbl for lbl in labels))

        mode_menu = next(a for a in actions if a.label == "显示模式")
        self.assertTrue(mode_menu.children[0].checked())
        mode_menu.children[1].callback()  # 切换为仅托盘图标
        self.assertEqual(app._display_mode, DisplayMode.TRAY_ICON_ONLY)
        self.assertEqual(fake_host.modes[-1], DisplayMode.TRAY_ICON_ONLY)
        mode_menu.children[0].callback()  # 切回任务栏常驻条
        self.assertEqual(app._display_mode, DisplayMode.TASKBAR_BAR)
        self.assertEqual(fake_host.modes[-1], DisplayMode.TASKBAR_BAR)

        style_menu = next(a for a in actions if a.label == "显示样式")
        self.assertTrue(style_menu.children[0].checked())
        style_menu.children[1].callback()  # 切换为单鼠标胶囊样式
        self.assertEqual(app._widget_style, WidgetStyle.MOUSE_CAPSULE)
        self.assertEqual(fake_host.relayout_count, 1)
        self.assertNotIn("键盘", app._current_tooltip())
        self.assertTrue(all("键盘" not in a.label for a in app._build_menu_actions()))

        placement_menu = next(a for a in actions if a.label == "停靠位置")
        self.assertEqual(app._taskbar_placement, TaskbarPlacement.LEFT_OF_SIBLINGS)
        self.assertTrue(placement_menu.children[0].checked())
        placement_menu.children[1].callback()
        self.assertEqual(app._taskbar_placement, TaskbarPlacement.RIGHT_OF_SIBLINGS)
        self.assertEqual(fake_host.placements[-1], TaskbarPlacement.RIGHT_OF_SIBLINGS)
        placement_menu.children[0].callback()
        self.assertEqual(app._taskbar_placement, TaskbarPlacement.LEFT_OF_SIBLINGS)
        self.assertEqual(fake_host.placements[-1], TaskbarPlacement.LEFT_OF_SIBLINGS)

        interval_menu = next(a for a in actions if a.label == "刷新间隔")
        interval_menu.children[0].callback()
        self.assertEqual(app._refresh_sec, 30)
        self.assertTrue(interval_menu.children[0].checked())

        extra_menu = next(a for a in actions if a.label == "回报率")
        extra_menu.children[0].callback()
        self.assertTrue(drv.clicked)
        self.assertTrue(extra_menu.children[0].checked())

        # 验证托盘图标(16px 紧凑数字)与气泡图标(32px 胶囊)尺寸分离
        self.assertEqual(app._render_tray_icon_img(16).size, (16, 16))
        self.assertEqual(app._render_balloon_icon_img().size, (32, 32))

    def test_win32_structures_and_hicon_creation(self):
        if sys.maxsize > 2**32:
            self.assertEqual(ctypes.sizeof(NOTIFYICONDATAW), 976)
        u32, g32, _k32, _s32 = _init_win32_apis()
        img = render_tray_icon(10, 100, size=16)
        hicon = create_hicon_from_image(u32, g32, img)
        self.assertTrue(bool(hicon))
        self.assertTrue(bool(u32.DestroyIcon(hicon)))

        host = Win32TaskbarHost(
            on_double_click=lambda: None,
            build_menu_items=lambda: [],
            render_widget_fn=lambda d, h, hov, bg: img,
            measure_widget_fn=lambda d, h: (100, h),
            get_tooltip_text=lambda: "tip",
            get_balloon_icon_img=lambda: img,
            is_light_theme_fn=lambda: False,
        )
        tray_sz = host._get_tray_icon_size()
        self.assertGreaterEqual(tray_sz, 16)

    def test_host_roundtrip_taskbar_and_tray_recreates_clean_bar(self):
        img = render_tray_icon(60, 100, size=16)
        bar_img = render_tray_icon(60, 100, size=40)
        host = Win32TaskbarHost(
            on_double_click=lambda: None,
            build_menu_items=lambda: [],
            render_widget_fn=lambda d, h, hov, bg: bar_img,
            measure_widget_fn=lambda d, h: (40, h),
            get_tooltip_text=lambda: "tip",
            get_balloon_icon_img=lambda: img,
            is_light_theme_fn=lambda: False,
            get_tray_icon_img=lambda sz: render_tray_icon(60, 100, size=sz),
            initial_mode=DisplayMode.TASKBAR_BAR,
        )
        with patch.object(host._s32, "Shell_NotifyIconW", return_value=1):
            host.create()
            msg_hwnd = host._msg_hwnd
            bar1 = host._bar_hwnd
            self.assertTrue(bool(msg_hwnd))
            self.assertTrue(bool(bar1))

            # 切至仅托盘图标：消息宿主保持不变，任务栏窗口彻底销毁
            host.set_display_mode(DisplayMode.TRAY_ICON_ONLY)
            self.assertEqual(host._msg_hwnd, msg_hwnd)
            self.assertIsNone(host._bar_hwnd)
            self.assertFalse(bool(host._u32.IsWindow(bar1)))

            # 从托盘切回任务栏：重建全新的干净任务栏窗口，不再复用到旧 Reparent 句柄
            host.set_display_mode(DisplayMode.TASKBAR_BAR)
            bar2 = host._bar_hwnd
            self.assertTrue(bool(bar2))
            self.assertNotEqual(bar2, bar1)
            self.assertTrue(bool(host._u32.IsWindow(bar2)))

            host.set_taskbar_placement(TaskbarPlacement.RIGHT_OF_SIBLINGS)
            self.assertEqual(host._taskbar_placement, TaskbarPlacement.RIGHT_OF_SIBLINGS)
            host.set_taskbar_placement(TaskbarPlacement.LEFT_OF_SIBLINGS)
            self.assertEqual(host._taskbar_placement, TaskbarPlacement.LEFT_OF_SIBLINGS)
            host.force_relayout_and_redraw()
            host._cleanup_and_destroy()
            self.assertIsNone(host._msg_hwnd)
            self.assertIsNone(host._bar_hwnd)

    def test_restore_taskbar_layout_skips_when_traffic_monitor_present(self):
        img = render_tray_icon(60, 100, size=16)
        host = Win32TaskbarHost(
            on_double_click=lambda: None,
            build_menu_items=lambda: [],
            render_widget_fn=lambda d, h, hov, bg: img,
            measure_widget_fn=lambda d, h: (100, h),
            get_tooltip_text=lambda: "tip",
            get_balloon_icon_img=lambda: img,
            is_light_theme_fn=lambda: False,
        )
        host._h_rebar = 101
        host._h_mstask = 102
        host._h_tray = 103
        host._modified_mstask = True
        rects = {
            101: RectTuple(120, 1040, 1700, 1080),
            102: RectTuple(120, 1040, 1460, 1080),
            103: RectTuple(0, 1040, 1920, 1080),
        }
        swp_calls = []
        host._taskbar_placement = TaskbarPlacement.LEFT_OF_SIBLINGS
        with patch.object(host._u32, "IsWindow", return_value=True), \
                patch.object(host, "_get_window_rect", side_effect=lambda h: rects.get(h)), \
                patch.object(host, "_find_rebar_sibling_widgets", return_value=[RectTuple(1460, 1040, 1700, 1080)]), \
                patch.object(host._u32, "SetWindowPos", side_effect=lambda *a: swp_calls.append(a)):
            host._restore_taskbar_layout()
            self.assertEqual(swp_calls, [])
            self.assertFalse(host._modified_mstask)

        # 若右侧存在 TrafficMonitor 等兄弟组件，无论左/右停靠模式退出时都不再改写 MSTaskSwWClass
        host._taskbar_placement = TaskbarPlacement.RIGHT_OF_SIBLINGS
        host._modified_mstask = True
        with patch.object(host._u32, "IsWindow", return_value=True), \
                patch.object(host, "_get_window_rect", side_effect=lambda h: rects.get(h)), \
                patch.object(host, "_find_rebar_sibling_widgets", return_value=[RectTuple(1460, 1040, 1700, 1080)]), \
                patch.object(host._u32, "SetWindowPos", side_effect=lambda *a: swp_calls.append(a)):
            host._restore_taskbar_layout()
            self.assertEqual(swp_calls, [])
            self.assertFalse(host._modified_mstask)

        # 无兄弟组件时正常恢复 MSTaskSwWClass 宽度
        swp_calls.clear()
        host._taskbar_placement = TaskbarPlacement.LEFT_OF_SIBLINGS
        host._modified_mstask = True
        with patch.object(host._u32, "IsWindow", return_value=True), \
                patch.object(host, "_get_window_rect", side_effect=lambda h: rects.get(h)), \
                patch.object(host, "_find_rebar_sibling_widgets", return_value=[]), \
                patch.object(host._u32, "SetWindowPos", side_effect=lambda *a: swp_calls.append(a)):
            host._restore_taskbar_layout()
            self.assertEqual(len(swp_calls), 1)
            self.assertEqual(swp_calls[0][4], 1580)

    def test_win10_tm_sibling_switches_to_overlay_wide_rect_and_caches_hwnd(self):
        self.assertEqual(LAYOUT_POLL_MS, 250)
        img = render_tray_icon(60, 100, size=16)
        host = Win32TaskbarHost(
            on_double_click=lambda: None,
            build_menu_items=lambda: [],
            render_widget_fn=lambda d, h, hov, bg: img,
            measure_widget_fn=lambda d, h: (105, h),
            get_tooltip_text=lambda: "tip",
            get_balloon_icon_img=lambda: img,
            is_light_theme_fn=lambda: False,
        )
        tray_2k = RectTuple(0, 1400, 2560, 1440)
        # 520px 超宽多指标 TM 应识别成功，1831px 全宽 ReBar 容器仍应过滤排除
        self.assertTrue(host._is_sibling_widget_rect(RectTuple(1690, 1404, 2210, 1436), tray_2k, 1.0))
        self.assertFalse(host._is_sibling_widget_rect(RectTuple(382, 1400, 2213, 1440), tray_2k, 1.0))

        # 验证命中 HWND 缓存时不重复调用 EnumWindows 全扫描
        tm_hwnd = 999
        host._cached_sibling_hwnds = [tm_hwnd]
        host._last_sibling_scan_ts = 1e18
        with patch.object(host._u32, "IsWindow", return_value=True), \
                patch.object(host._u32, "IsWindowVisible", return_value=True), \
                patch.object(host._u32, "IsIconic", return_value=False), \
                patch.object(host, "_get_window_rect", return_value=RectTuple(2057, 1404, 2215, 1436)), \
                patch.object(host._u32, "EnumWindows", side_effect=AssertionError("不应触发全量 EnumWindows")):
            res = host._find_taskbar_siblings(tray_2k, 1.0, include_rebar=True, force_rescan=False)
            self.assertEqual(res, [RectTuple(2057, 1404, 2215, 1436)])

        # Win10 即使检测到 TrafficMonitor，也应统一嵌入 ReBarWindow32 并保持 WS_POPUP|WS_CLIPSIBLINGS，
        # 既随 Shell_TrayWnd 同步 Z 序层级，又支持跨进程 UpdateLayeredWindow
        host._bar_hwnd = 888
        host._h_tray = 103
        host._h_rebar = 101
        host._h_mstask = 102
        set_parent_calls = []
        set_style_calls = []
        with patch.object(host, "_detect_taskbar_handles", return_value=True), \
                patch.object(host, "_get_window_rect", return_value=tray_2k), \
                patch.object(host, "_get_dpi_for_hwnd", return_value=96), \
                patch.object(host._u32, "IsWindow", return_value=True), \
                patch.object(host._u32, "GetParent", return_value=0), \
                patch.object(host._u32, "GetWindowLongPtrW", return_value=WS_POPUP), \
                patch.object(host._u32, "SetWindowLongPtrW", side_effect=lambda _h, idx, val: set_style_calls.append((idx, val)) or 0), \
                patch.object(host._u32, "SetWindowPos", return_value=True), \
                patch.object(host._u32, "SetParent", side_effect=lambda h, p: set_parent_calls.append((h, p)) or 1):
            self.assertFalse(host._should_use_overlay_on_create())
            host._attach_to_taskbar()
            self.assertTrue(host._embedded)
            self.assertFalse(host._win11_overlay)
            self.assertEqual(set_parent_calls, [(888, 101)])
            applied_style = dict(set_style_calls)[GWL_STYLE]
            self.assertTrue(bool(applied_style & WS_POPUP))
            self.assertTrue(bool(applied_style & WS_CLIPSIBLINGS))
            self.assertFalse(bool(applied_style & WS_CHILD))

        # 第三方兄弟组件在开始菜单切换瞬间短时隐藏但句柄仍存在时，应保留上次已知坐标不乱跳
        host._cached_sibling_hwnds = [tm_hwnd]
        host._last_nonempty_siblings = [RectTuple(1952, 1404, 2110, 1436)]
        host._last_sibling_scan_ts = 0.0
        with patch.object(host._u32, "IsWindow", side_effect=lambda h: (h.value if hasattr(h, "value") else int(h)) == tm_hwnd), \
                patch.object(host, "_collect_child_siblings", return_value=None), \
                patch.object(host._u32, "EnumWindows", return_value=True):
            res_glitch = host._find_taskbar_siblings(tray_2k, 1.0, include_rebar=True, force_rescan=True)
            self.assertEqual(res_glitch, [RectTuple(1952, 1404, 2110, 1436)])
            self.assertEqual(host._last_sibling_scan_ts, 0.0)

        # Win11 现代 XAML 任务栏同样应挂载为 Shell_TrayWnd 子级并置于 XAML Bridge 之前
        set_parent_win11 = []
        with patch.object(host, "_detect_taskbar_handles", return_value=False), \
                patch.object(host._u32, "GetWindowLongPtrW", return_value=WS_POPUP), \
                patch.object(host._u32, "SetWindowLongPtrW", return_value=0), \
                patch.object(host._u32, "SetWindowPos", return_value=True), \
                patch.object(host._u32, "SetParent", side_effect=lambda h, p: set_parent_win11.append((h, p)) or 1):
            host._h_tray = 103
            host._bar_hwnd = 888
            self.assertFalse(host._should_use_overlay_on_create())
            host._attach_to_taskbar()
            self.assertTrue(host._embedded)
            self.assertTrue(host._win11_overlay)
            self.assertEqual(host._parent_hwnd, 103)
            self.assertEqual(set_parent_win11, [(888, 103)])

        # 若 Win11 DesktopWindowContentBridge 排到电量条前面，应判定需要 HWND_TOP 拉回最前
        cls_map = {
            200: "Windows.UI.Composition.DesktopWindowContentBridge",
            888: host.BAR_CLASS_NAME,
        }
        next_map = {
            (103, GW_CHILD): 200,
            (200, GW_HWNDNEXT): 888,
            (888, GW_HWNDNEXT): 0,
        }
        with patch.object(host._u32, "GetWindow", side_effect=lambda h, cmd: next_map.get((h, cmd), 0)), \
                patch.object(host._u32, "IsWindowVisible", return_value=1), \
                patch.object(host, "_get_window_class", side_effect=lambda h: cls_map.get(h, "")):
            self.assertTrue(host._is_win11_bar_behind_children())

            swp_win11 = []
            win11_tray = RectTuple(0, 1392, 2560, 1440)
            win11_notify = RectTuple(2338, 1392, 2560, 1440)
            with patch.object(host, "_get_monitor_rect", return_value=RectTuple(0, 0, 2560, 1440)), \
                    patch.object(host, "_find_win11_sibling_widgets", return_value=[RectTuple(2182, 1400, 2340, 1432)]), \
                    patch.object(host, "_get_window_rect", return_value=RectTuple(2073, 1392, 2178, 1440)), \
                    patch.object(host, "_screen_to_parent_client", return_value=(2073, 0, 105, 48)), \
                    patch.object(host._u32, "SetWindowPos", side_effect=lambda *a: swp_win11.append(a) or True):
                host._sync_win11_layout(False, win11_tray, win11_notify, 96, 1.0)
                self.assertEqual(len(swp_win11), 1)
                self.assertIs(swp_win11[0][1], HWND_TOP)
                self.assertEqual(swp_win11[0][2:6], (2073, 0, 105, 48))

        # 当未找到任务栏时，兜底创建原生带 WS_EX_TOPMOST 的悬浮条窗口
        host._h_tray = None
        with patch.object(host, "_detect_taskbar_handles", return_value=False):
            self.assertTrue(host._should_use_overlay_on_create())
        host._register_classes()
        try:
            self.assertTrue(host._create_bar_window(topmost=True))
            ex_style = host._u32.GetWindowLongPtrW(host._bar_hwnd, GWL_EXSTYLE)
            self.assertTrue(bool(ex_style & WS_EX_TOPMOST))
        finally:
            host._cleanup_and_destroy()

    def test_wm_devicechange_triggers_debounced_refresh_and_unregisters(self):
        img = render_tray_icon(60, None, size=16)
        dev_changes = []
        host = Win32TaskbarHost(
            on_double_click=lambda: None,
            build_menu_items=lambda: [],
            render_widget_fn=lambda d, h, hov, bg: img,
            measure_widget_fn=lambda d, h: (100, h),
            get_tooltip_text=lambda: "tip",
            get_balloon_icon_img=lambda: img,
            is_light_theme_fn=lambda: False,
            initial_mode=DisplayMode.TRAY_ICON_ONLY,
            on_device_change=lambda: dev_changes.append(1),
        )
        host.create()
        try:
            self.assertTrue(bool(host._msg_hwnd))
            self.assertTrue(bool(host._dev_notify_handle))
            set_timer_calls = []
            kill_timer_calls = []
            with patch.object(host._u32, "SetTimer", side_effect=lambda *a: set_timer_calls.append(a) or 1), \
                    patch.object(host._u32, "KillTimer", side_effect=lambda *a: kill_timer_calls.append(a) or 1):
                self.assertEqual(host._msg_wnd_proc(host._msg_hwnd, WM_DEVICECHANGE, DBT_DEVICEARRIVAL, 0), 1)
                self.assertEqual(host._msg_wnd_proc(host._msg_hwnd, WM_DEVICECHANGE, DBT_DEVICEREMOVECOMPLETE, 0), 1)
                self.assertEqual(host._msg_wnd_proc(host._msg_hwnd, WM_DEVICECHANGE, DBT_DEVNODES_CHANGED, 0), 1)
                self.assertEqual([c[1] for c in set_timer_calls], [TIMER_DEVICECHANGE_ID] * 3)
                # 防抖期间尚未直接回调，触发 TIMER_DEVICECHANGE_ID 后仅唤醒一次
                self.assertEqual(dev_changes, [])
                self.assertEqual(host._msg_wnd_proc(host._msg_hwnd, WM_TIMER, TIMER_DEVICECHANGE_ID, 0), 0)
                self.assertEqual(dev_changes, [1])
                self.assertEqual(len(kill_timer_calls), 1)
        finally:
            host._cleanup_and_destroy()
            self.assertIsNone(host._dev_notify_handle)

    def test_fullscreen_cover_ignores_start_menu_cloaked_and_below_taskbar_windows(self):
        img = render_tray_icon(60, None, size=16)
        host = Win32TaskbarHost(
            on_double_click=lambda: None,
            build_menu_items=lambda: [],
            render_widget_fn=lambda d, h, hov, bg: img,
            measure_widget_fn=lambda d, h: (100, h),
            get_tooltip_text=lambda: "tip",
            get_balloon_icon_img=lambda: img,
            is_light_theme_fn=lambda: False,
            initial_mode=DisplayMode.TASKBAR_BAR,
        )
        host._h_tray = 103
        host._bar_hwnd = 888
        tray_2k = RectTuple(0, 1400, 2560, 1440)
        mon_2k = RectTuple(0, 0, 2560, 1440)
        full_uwp = RectTuple(-8, -8, 2568, 1448)

        fg_holder = {"hwnd": 2001, "cls": "Windows.UI.Core.CoreWindow", "ex": 0, "cloaked": False, "above": True, "rect": mon_2k}

        def fake_get_rect(hwnd):
            return tray_2k if hwnd == 103 else fg_holder["rect"]

        def fake_get_window(hwnd, cmd):
            if cmd == GW_HWNDPREV and hwnd == 103 and fg_holder["above"]:
                return fg_holder["hwnd"]
            return 0

        with patch.object(host._u32, "GetForegroundWindow", side_effect=lambda: fg_holder["hwnd"]), \
                patch.object(host._u32, "IsWindowVisible", return_value=1), \
                patch.object(host, "_get_window_class", side_effect=lambda _h: fg_holder["cls"]), \
                patch.object(host._u32, "GetWindowLongPtrW", side_effect=lambda _h, _i: fg_holder["ex"]), \
                patch.object(host, "_is_window_cloaked", side_effect=lambda _h: fg_holder["cloaked"]), \
                patch.object(host._u32, "MonitorFromWindow", return_value=1), \
                patch.object(host, "_get_monitor_rect", return_value=mon_2k), \
                patch.object(host, "_get_window_rect", side_effect=fake_get_rect), \
                patch.object(host._u32, "GetWindow", side_effect=fake_get_window):
            # 开始菜单 / 搜索 (CoreWindow 全屏) 不得触发隐藏
            self.assertFalse(host._is_fullscreen_covered(tray_2k))

            # 后台挂起但处于 Cloaked 状态的 UWP ApplicationFrameWindow 不得触发隐藏
            fg_holder.update(cls="ApplicationFrameWindow", rect=full_uwp, cloaked=True, above=True)
            self.assertFalse(host._is_fullscreen_covered(tray_2k))

            # 位于任务栏 Z 序之下的非 Cloaked 窗口不得触发隐藏
            fg_holder.update(cls="Chrome_WidgetWin_1", rect=mon_2k, cloaked=False, above=False)
            self.assertFalse(host._is_fullscreen_covered(tray_2k))

            # ToolWindow 扩展样式窗口不触发全屏隐藏
            fg_holder.update(cls="PopupHost", ex=WS_EX_TOOLWINDOW, cloaked=False, above=True)
            self.assertFalse(host._is_fullscreen_covered(tray_2k))

            # 真正可见、未 Cloaked 且压在任务栏之上的全屏应用触发隐藏
            fg_holder.update(cls="Chrome_WidgetWin_1", ex=0, cloaked=False, above=True, rect=mon_2k)
            self.assertTrue(host._is_fullscreen_covered(tray_2k))

    def test_wm_settingchange_skips_redundant_redraw_when_layout_and_theme_unchanged(self):
        img = render_tray_icon(60, None, size=16)
        light_state = {"val": False}
        host = Win32TaskbarHost(
            on_double_click=lambda: None,
            build_menu_items=lambda: [],
            render_widget_fn=lambda d, h, hov, bg: img,
            measure_widget_fn=lambda d, h: (100, h),
            get_tooltip_text=lambda: "tip",
            get_balloon_icon_img=lambda: img,
            is_light_theme_fn=lambda: light_state["val"],
            initial_mode=DisplayMode.TASKBAR_BAR,
        )
        host._last_theme_light = False
        sync_calls = []
        redraw_calls = []
        with patch.object(host, "_sync_layout", side_effect=lambda force=False: sync_calls.append(force) or False), \
                patch.object(host, "redraw", side_effect=lambda: redraw_calls.append(1)):
            host._msg_wnd_proc(0, WM_SETTINGCHANGE, 0, 0)
            self.assertEqual(sync_calls, [False])
            self.assertEqual(redraw_calls, [])

            # 当系统浅色/深色主题实际变化时，强制同步并重绘
            light_state["val"] = True
            host._msg_wnd_proc(0, WM_SETTINGCHANGE, 0, 0)
            self.assertEqual(sync_calls, [False, True])
            self.assertEqual(redraw_calls, [1])

    def test_detect_taskbar_handles_rejects_win11_hidden_rebar_or_xaml_bridge(self):
        img = render_tray_icon(60, None, size=16)
        host = Win32TaskbarHost(
            on_double_click=lambda: None,
            build_menu_items=lambda: [],
            render_widget_fn=lambda d, h, hov, bg: img,
            measure_widget_fn=lambda d, h: (100, h),
            get_tooltip_text=lambda: "tip",
            get_balloon_icon_img=lambda: img,
            is_light_theme_fn=lambda: False,
            initial_mode=DisplayMode.TASKBAR_BAR,
        )
        rects = {
            101: RectTuple(0, 1032, 700, 1080),
            102: RectTuple(0, 1032, 700, 1080),
        }

        def fake_find_ex(parent, _after, cls_name, _title):
            if parent == 103 and cls_name == "ReBarWindow32":
                return 101
            if parent == 101 and cls_name == "MSTaskSwWClass":
                return 102
            if parent == 103 and cls_name == "TrayNotifyWnd":
                return 104
            if parent == 103 and cls_name == "Windows.UI.Composition.DesktopWindowContentBridge":
                return bridge_state["hwnd"]
            return 0

        vis_map = {101: 0, 102: 0, 200: 1}
        bridge_state = {"hwnd": 200}

        with patch.object(host._u32, "FindWindowW", return_value=103), \
                patch.object(host._u32, "FindWindowExW", side_effect=fake_find_ex), \
                patch.object(host._u32, "IsWindowVisible", side_effect=lambda h: vis_map.get(h, 0)), \
                patch.object(host, "_get_window_rect", side_effect=lambda h: rects.get(h)):
            # 1. Win11 下隐藏的 ReBar/MSTask 占位句柄 + XAML Bridge 必须识别为非经典任务栏
            self.assertFalse(host._detect_taskbar_handles())

            # 2. 即使 Win11 兼容层把 ReBar/MSTask 标为可见，只要存在可见 XAML Bridge 仍走 Win11 悬浮层
            vis_map[101] = 1
            vis_map[102] = 1
            self.assertFalse(host._detect_taskbar_handles())

            # 3. 真正 Win10 经典任务栏（ReBar/MSTask 均可见且无 XAML Bridge）识别为经典模式
            bridge_state["hwnd"] = 0
            self.assertTrue(host._detect_taskbar_handles())


if __name__ == "__main__":
    unittest.main()
