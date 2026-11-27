# -*- coding: utf-8 -*-
"""设备管理器缓存、虚拟键盘默认 100%、低电量通知与图标渲染单元测试。"""

from pathlib import Path
import sys
import unittest

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
    SlotState,
    TaskbarPlacement,
    TrayMenuAction,
    WidgetStyle,
    quantize_battery_step10,
)
from drivers.virtual_keyboard import VirtualKeyboardDriver
from ui.notifier import LowBatteryNotifier
from ui.renderer import (
    COLOR_CHARGING,
    COLOR_CHARGING_LIGHT,
    COLOR_LOW,
    COLOR_MID,
    format_device_bar_text,
    image_to_premultiplied_bgra,
    measure_taskbar_widget,
    measure_widget_by_style,
    render_mouse_capsule_widget,
    render_numeric_tray_icon,
    render_taskbar_widget,
    render_tray_icon,
    render_tray_icon_by_style,
    render_widget_by_style,
    resolve_arc_color,
)
from ui.theme import DARK_THEME_FG, LIGHT_THEME_FG
from ui.tray_app import format_tooltip
from ui.win32_taskbar import (
    RectTuple,
    compute_win10_layout,
    compute_win11_layout,
)


class MockMouseDriver(BaseDeviceDriver, IMenuContributor):
    slot = DeviceSlot.MOUSE
    driver_id = "mock_mouse"
    display_name = "G3 V2"
    priority = 10

    def __init__(self, script):
        self._script = list(script)

    def poll(self, hid_devices):
        return self._script.pop(0)

    def get_menu_actions(self):
        return [TrayMenuAction(label="扩展占位菜单")]


class TestManagerAndUi(unittest.TestCase):
    def test_sleep_preserves_last_battery_and_disconnect_clears(self):
        mock_mouse = MockMouseDriver(
            [
                DevicePollResult(
                    hardware_connected=True,
                    reading=BatteryReading(
                        percent=64,
                        charging=False,
                        online=True,
                        model_name="G3 V2",
                        connection_mode=ConnectionMode.WIRELESS_24G,
                    ),
                ),
                # 第二轮休眠：保留 64%
                DevicePollResult(
                    hardware_connected=True,
                    reading=BatteryReading(
                        percent=None,
                        charging=False,
                        online=False,
                        model_name="G3 V2",
                        connection_mode=ConnectionMode.WIRELESS_24G,
                    ),
                ),
                # 第三轮彻底拔出接收器：清除为 None
                None,
            ]
        )

        mgr = DeviceManager(enumerate_fn=lambda: [])
        mgr.register_driver(mock_mouse)
        mgr.register_driver(VirtualKeyboardDriver())

        s1 = mgr.refresh_all()
        self.assertEqual(s1[DeviceSlot.MOUSE].percent, 64)
        self.assertTrue(s1[DeviceSlot.MOUSE].online)
        self.assertFalse(s1[DeviceSlot.KEYBOARD].connected)
        self.assertFalse(s1[DeviceSlot.KEYBOARD].online)
        self.assertIsNone(s1[DeviceSlot.KEYBOARD].percent)
        self.assertFalse(s1[DeviceSlot.KEYBOARD].is_virtual)

        s2 = mgr.refresh_all()
        self.assertTrue(s2[DeviceSlot.MOUSE].connected)
        self.assertFalse(s2[DeviceSlot.MOUSE].online)
        self.assertEqual(s2[DeviceSlot.MOUSE].percent, 64)
        self.assertIn("休眠", format_tooltip(s2))
        self.assertIn("键盘 MCHOSE: 未连接", format_tooltip(s2))

        s3 = mgr.refresh_all()
        self.assertFalse(s3[DeviceSlot.MOUSE].connected)
        self.assertIsNone(s3[DeviceSlot.MOUSE].percent)
        self.assertFalse(s3[DeviceSlot.KEYBOARD].connected)
        self.assertIsNone(s3[DeviceSlot.KEYBOARD].percent)
        self.assertIn("未连接", format_tooltip(s3))

        # 显式指定虚拟电量时仍可按虚拟槽位运作
        mgr_override = DeviceManager(enumerate_fn=lambda: [])
        mgr_override.register_driver(VirtualKeyboardDriver(default_percent=80))
        s_over = mgr_override.refresh_all()
        self.assertTrue(s_over[DeviceSlot.KEYBOARD].connected)
        self.assertTrue(s_over[DeviceSlot.KEYBOARD].is_virtual)
        self.assertEqual(s_over[DeviceSlot.KEYBOARD].percent, 80)

        self.assertEqual(len(mgr.collect_menu_actions()), 1)

    def test_low_battery_notifier_latch_and_recharge_reset(self):
        notifier = LowBatteryNotifier(notify_threshold=10, reset_threshold=15)

        def mouse_state(pct, charging=False, online=True, connected=True):
            return SlotState(
                slot=DeviceSlot.MOUSE,
                display_label="鼠标",
                model_name="G3 V2",
                connected=connected,
                online=online,
                charging=charging,
                percent=pct,
                is_virtual=False,
            )

        # > 10% 不提醒
        self.assertIsNone(notifier.evaluate(mouse_state(11)))
        # <= 10% 触发一次
        alert1 = notifier.evaluate(mouse_state(10))
        self.assertIsNotNone(alert1)
        self.assertIn("10%", alert1[1])
        # 持续低电不重复弹窗
        self.assertIsNone(notifier.evaluate(mouse_state(9)))
        # 休眠也不重置
        self.assertIsNone(notifier.evaluate(mouse_state(9, online=False)))
        self.assertTrue(notifier.is_notified(DeviceSlot.MOUSE))
        # 回升到 12% 尚未跨过滞回阈值不误重置
        self.assertIsNone(notifier.evaluate(mouse_state(12)))
        self.assertTrue(notifier.is_notified(DeviceSlot.MOUSE))
        # 插线充电立即重置
        self.assertIsNone(notifier.evaluate(mouse_state(12, charging=True)))
        self.assertFalse(notifier.is_notified(DeviceSlot.MOUSE))
        # 拔线后再次低于 <= 10% 可重新提醒
        self.assertIsNotNone(notifier.evaluate(mouse_state(8, charging=False)))
        # 或者未识别充电标志但电量充到 >= 15% 后重置
        self.assertIsNone(notifier.evaluate(mouse_state(15, charging=False)))
        self.assertFalse(notifier.is_notified(DeviceSlot.MOUSE))

        # 虚拟键盘不弹窗
        kb_state = SlotState(
            slot=DeviceSlot.KEYBOARD,
            display_label="键盘",
            model_name="默认",
            connected=True,
            online=True,
            percent=5,
            is_virtual=True,
        )
        self.assertIsNone(notifier.evaluate(kb_state))

    def test_renderer_colors_and_themes(self):
        self.assertEqual(resolve_arc_color(80, DARK_THEME_FG), DARK_THEME_FG)
        self.assertEqual(resolve_arc_color(80, LIGHT_THEME_FG), LIGHT_THEME_FG)
        self.assertEqual(resolve_arc_color(30, DARK_THEME_FG), COLOR_MID)
        self.assertEqual(resolve_arc_color(16, DARK_THEME_FG), COLOR_MID)
        self.assertEqual(resolve_arc_color(15, DARK_THEME_FG), COLOR_LOW)
        self.assertEqual(resolve_arc_color(1, LIGHT_THEME_FG), COLOR_LOW)
        self.assertEqual(resolve_arc_color(10, DARK_THEME_FG, charging=True), COLOR_CHARGING)
        self.assertEqual(resolve_arc_color(10, LIGHT_THEME_FG, charging=True), COLOR_CHARGING_LIGHT)

        img_dark = render_tray_icon(85, 100, light_theme=False)
        img_light = render_tray_icon(25, 100, light_theme=True)
        img_low = render_tray_icon(10, 100, light_theme=False)
        img_none = render_tray_icon(None, None, light_theme=False)
        img_kb_off = render_tray_icon(85, None, light_theme=False)

        for img in (img_dark, img_light, img_low, img_none, img_kb_off):
            self.assertEqual(img.size, (64, 64))
            self.assertEqual(img.mode, "RGBA")

        # 内圈键盘未接入时应仅绘制变暗底环（Alpha <= 60），不绘制高亮 100% 实线
        px_kb_on = img_dark.load()
        px_kb_off = img_kb_off.load()
        self.assertGreaterEqual(max(px_kb_on[31, y][3] for y in range(18, 26)), 240)
        kb_off_inner_alpha = max(px_kb_off[31, y][3] for y in range(18, 26))
        self.assertGreater(kb_off_inner_alpha, 0)
        self.assertLessEqual(kb_off_inner_alpha, 60)

    def test_taskbar_widget_fixed_width_and_icon_only_coloring(self):
        self.assertEqual(format_device_bar_text("鼠标", 38), "鼠标: 38%")
        self.assertEqual(format_device_bar_text("鼠标", None), "鼠标: --")
        self.assertEqual(format_device_bar_text("键盘", None), "键盘: --")

        w_bar_off_none = render_widget_by_style(
            WidgetStyle.DUAL_RING,
            85,
            None,
            keyboard_connected=False,
            light_theme=False,
        )
        w_bar_off_stale = render_widget_by_style(
            WidgetStyle.DUAL_RING,
            85,
            100,
            keyboard_connected=False,
            light_theme=False,
        )
        self.assertEqual(w_bar_off_none.tobytes(), w_bar_off_stale.tobytes())

        w_96, h_96 = measure_taskbar_widget(dpi=96, bar_height=40)
        w_120, h_120 = measure_taskbar_widget(dpi=120, bar_height=50)
        self.assertGreater(w_120, w_96)
        self.assertEqual(h_96, 40)
        self.assertEqual(h_120, 50)

        # 低电量 (12% 红) 下：不同电量文字长度渲染出来的位图尺寸必须完全一致
        img_12 = render_taskbar_widget(12, 100, light_theme=False, dpi=96, bar_height=40)
        img_100 = render_taskbar_widget(100, 100, light_theme=False, dpi=96, bar_height=40)
        self.assertEqual(img_12.size, (w_96, 40))
        self.assertEqual(img_100.size, (w_96, 40))

        # 严格校验“只有图标变色”：右半边文字区不能出现红色或黄色像素
        px = img_12.load()
        left_has_red = any(
            px[x, y][0] > 200 and px[x, y][1] < 120 and px[x, y][3] > 100
            for x in range(0, 32)
            for y in range(40)
        )
        right_has_color = any(
            abs(px[x, y][0] - px[x, y][1]) > 5 or abs(px[x, y][1] - px[x, y][2]) > 5
            for x in range(34, w_96)
            for y in range(40)
        )
        self.assertTrue(left_has_red)
        self.assertFalse(right_has_color)

        # 验证预乘 BGRA 转换长度与字节序
        bgra = image_to_premultiplied_bgra(img_12)
        self.assertEqual(len(bgra), w_96 * 40 * 4)

    def test_mouse_capsule_style_rendering_and_icon_only_coloring(self):
        w_cap, h_cap = measure_widget_by_style(WidgetStyle.MOUSE_CAPSULE, dpi=96, bar_height=40)
        w_dual, _ = measure_widget_by_style(WidgetStyle.DUAL_RING, dpi=96, bar_height=40)
        self.assertEqual(h_cap, 40)
        self.assertLess(w_cap, w_dual)

        img_low = render_mouse_capsule_widget(
            mouse_percent=10,
            connected=True,
            online=True,
            charging=False,
            light_theme=False,
            dpi=96,
            bar_height=40,
        )
        img_full = render_mouse_capsule_widget(
            mouse_percent=100,
            connected=True,
            online=True,
            charging=False,
            light_theme=True,
            dpi=96,
            bar_height=40,
        )
        self.assertEqual(img_low.size, (w_cap, 40))
        self.assertEqual(img_full.size, (w_cap, 40))

        # 右侧单行文字像素保持灰阶不变色，左侧包含红色低电量液位与绿点
        px = img_low.load()
        left_has_green_dot = any(
            px[x, y][1] > 180 and px[x, y][0] < 120 and px[x, y][3] > 150
            for x in range(0, 30)
            for y in range(28, 40)
        )
        right_has_color = any(
            abs(px[x, y][0] - px[x, y][1]) > 5 or abs(px[x, y][1] - px[x, y][2]) > 5
            for x in range(32, w_cap)
            for y in range(40)
        )
        self.assertTrue(left_has_green_dot)
        self.assertFalse(right_has_color)

        tray_cap = render_tray_icon_by_style(
            WidgetStyle.MOUSE_CAPSULE,
            mouse_percent=40,
            keyboard_percent=100,
            size=64,
        )
        self.assertEqual(tray_cap.size, (64, 64))

        # 系统托盘模式下（compact_numeric=True）应生成满幅数字 + 进度条 + 状态圆点图标
        for sz in (16, 20, 24, 32):
            for val in (10, 30, 80, 100, None):
                conn = val is not None
                icon = render_tray_icon_by_style(
                    WidgetStyle.MOUSE_CAPSULE,
                    mouse_percent=val,
                    keyboard_percent=100,
                    mouse_connected=conn,
                    mouse_online=conn,
                    light_theme=False,
                    size=sz,
                    compact_numeric=True,
                )
                self.assertEqual(icon.size, (sz, sz))
                # 确认数字和底部进度条充分铺满横向空间而非缩在中心极小矩形里
                bbox = icon.getbbox()
                self.assertIsNotNone(bbox)
                self.assertGreaterEqual(bbox[2] - bbox[0], round(sz * 0.72))
                self.assertGreaterEqual(bbox[3] - bbox[1], round(sz * 0.72))

        # 校验低电量颜色：10% 含红色，30% 含黄色，80% 深色主题无彩色文字（除右下绿点外）
        ic_low = render_numeric_tray_icon(10, connected=True, online=True, light_theme=False, size=16)
        px_low = ic_low.load()
        self.assertTrue(
            any(px_low[x, y][0] > 200 and px_low[x, y][1] < 120 and px_low[x, y][3] > 120
                for x in range(16) for y in range(12))
        )
        ic_mid = render_numeric_tray_icon(30, connected=True, online=True, light_theme=False, size=16)
        px_mid = ic_mid.load()
        self.assertTrue(
            any(px_mid[x, y][0] > 200 and px_mid[x, y][1] > 140 and px_mid[x, y][2] < 100 and px_mid[x, y][3] > 120
                for x in range(16) for y in range(12))
        )

        # 校验充电状态：双环外圈、鼠标胶囊及紧凑数字托盘均变为绿色，而长条文字仍保持无彩色
        for style, text_start_x in ((WidgetStyle.DUAL_RING, 34), (WidgetStyle.MOUSE_CAPSULE, 32)):
            w_bar = render_widget_by_style(
                style=style,
                mouse_percent=40,
                keyboard_percent=100,
                mouse_connected=True,
                mouse_online=True,
                mouse_charging=True,
                light_theme=False,
                dpi=96,
                bar_height=40,
            )
            px_w = w_bar.load()
            self.assertTrue(
                any(px_w[x, y][1] > 180 and px_w[x, y][0] < 120 and px_w[x, y][3] > 150
                    for x in range(text_start_x) for y in range(40))
            )
            self.assertFalse(
                any(abs(px_w[x, y][0] - px_w[x, y][1]) > 5 or abs(px_w[x, y][1] - px_w[x, y][2]) > 5
                    for x in range(text_start_x, w_bar.width) for y in range(40))
            )

        ic_chg = render_numeric_tray_icon(
            40, connected=True, online=True, charging=True, light_theme=False, size=16
        )
        px_chg = ic_chg.load()
        self.assertTrue(
            any(px_chg[x, y][1] > 180 and px_chg[x, y][0] < 120 and px_chg[x, y][3] > 120
                for x in range(16) for y in range(12))
        )

        # G3 原始款开局即充电(0xFF 哨兵, percent=None) 时，双环外圈与托盘数字 "--" 均应呈现绿色
        ring_none_chg = render_tray_icon(None, 100, light_theme=False, size=32, mouse_charging=True)
        px_ring = ring_none_chg.load()
        self.assertTrue(
            any(px_ring[x, y][1] > 180 and px_ring[x, y][0] < 120 and px_ring[x, y][3] > 120
                for x in range(32) for y in range(32))
        )
        ic_none_chg = render_numeric_tray_icon(
            None, connected=True, online=True, charging=True, light_theme=False, size=16
        )
        px_none_chg = ic_none_chg.load()
        self.assertTrue(
            any(px_none_chg[x, y][1] > 180 and px_none_chg[x, y][0] < 120 and px_none_chg[x, y][3] > 120
                for x in range(16) for y in range(12))
        )

    def test_win10_taskbar_layout_computation(self):
        # 1. 标准 1920x1080 底部任务栏（无第三方插件）：在 ReBar 内让出右端空间
        tray = RectTuple(0, 1040, 1920, 1080)
        notify = RectTuple(1700, 1040, 1920, 1080)
        rebar = RectTuple(120, 1040, 1700, 1080)
        mstask = RectTuple(120, 1040, 1700, 1080)

        res = compute_win10_layout(
            tray, notify, rebar, bar_w=110, bar_h=40, scale=1.0, rc_mstask=mstask
        )
        self.assertTrue(res.is_horizontal)
        self.assertTrue(res.need_resize_mstask)
        self.assertEqual(res.bar_rect, RectTuple(1590, 1040, 1700, 1080))
        self.assertEqual(res.mstask_rect, RectTuple(120, 1040, 1590, 1080))

        # 2. TrafficMonitor 已在左邻位置稳定共存时（不重叠、不回推 mstask，零抖动）
        tm_settled = [RectTuple(1410, 1040, 1590, 1080)]
        mstask_with_tm = RectTuple(120, 1040, 1410, 1080)
        res_settled = compute_win10_layout(
            tray,
            notify,
            rebar,
            bar_w=110,
            bar_h=40,
            scale=1.0,
            rc_mstask=mstask_with_tm,
            sibling_widgets=tm_settled,
        )
        self.assertFalse(res_settled.need_resize_mstask)
        self.assertIsNone(res_settled.pushed_edge)

        # 3. TrafficMonitor 默认停靠在 ReBar 最右侧并占据 [1520, 1700] 时：
        #    默认 LEFT_OF_SIBLINGS 主动避让到其左侧 [1406, 1516]，且不改写 MSTaskSwWClass（杜绝与 TrafficMonitor 竞争闪烁）
        tm_overlapping = [RectTuple(1520, 1040, 1700, 1080)]
        mstask_tm_full = RectTuple(120, 1040, 1520, 1080)
        res_left = compute_win10_layout(
            tray,
            notify,
            rebar,
            bar_w=110,
            bar_h=40,
            scale=1.0,
            rc_mstask=mstask_tm_full,
            sibling_widgets=tm_overlapping,
            reclaim_gap=True,
            placement=TaskbarPlacement.LEFT_OF_SIBLINGS,
        )
        self.assertEqual(res_left.bar_rect, RectTuple(1406, 1040, 1516, 1080))
        self.assertFalse(res_left.need_resize_mstask)
        self.assertEqual(res_left.mstask_rect, mstask_tm_full)

        # 3b. 显式选择 RIGHT_OF_SIBLINGS 时，同样不拉扯 MSTaskSwWClass 覆盖 TrafficMonitor
        res_right = compute_win10_layout(
            tray,
            notify,
            rebar,
            bar_w=110,
            bar_h=40,
            scale=1.0,
            rc_mstask=mstask_tm_full,
            sibling_widgets=tm_overlapping,
            reclaim_gap=True,
            placement=TaskbarPlacement.RIGHT_OF_SIBLINGS,
        )
        self.assertEqual(res_right.bar_rect, RectTuple(1590, 1040, 1700, 1080))
        self.assertFalse(res_right.need_resize_mstask)

        # 4. 无兄弟组件且样式从宽切窄（110 -> 76）后自动回收空白，存在右侧组件时不强行扩宽覆盖它
        res_reclaim = compute_win10_layout(
            tray,
            notify,
            rebar,
            bar_w=76,
            bar_h=40,
            scale=1.0,
            rc_mstask=RectTuple(120, 1040, 1590, 1080),
            sibling_widgets=[],
            reclaim_gap=True,
        )
        self.assertTrue(res_reclaim.need_resize_mstask)
        self.assertEqual(res_reclaim.mstask_rect, RectTuple(120, 1040, 1624, 1080))

        res_keep_tm = compute_win10_layout(
            tray,
            notify,
            rebar,
            bar_w=76,
            bar_h=40,
            scale=1.0,
            rc_mstask=mstask_with_tm,
            sibling_widgets=tm_settled,
            reclaim_gap=True,
        )
        self.assertFalse(res_keep_tm.need_resize_mstask)
        self.assertEqual(res_keep_tm.mstask_rect, mstask_with_tm)

        # 5. 竖向任务栏（停靠左侧）
        v_tray = RectTuple(0, 0, 62, 1080)
        v_notify = RectTuple(0, 900, 62, 1080)
        v_rebar = RectTuple(0, 48, 62, 900)
        v_mstask = RectTuple(0, 48, 62, 900)
        res_v = compute_win10_layout(
            v_tray, v_notify, v_rebar, bar_w=110, bar_h=40, scale=1.0, rc_mstask=v_mstask
        )
        self.assertFalse(res_v.is_horizontal)
        self.assertTrue(res_v.need_resize_mstask)
        self.assertEqual(res_v.bar_rect, RectTuple(0, 860, 62, 900))
        self.assertEqual(res_v.mstask_rect, RectTuple(0, 48, 62, 860))

    def test_win11_taskbar_layout_computation(self):
        screen = RectTuple(0, 0, 1920, 1080)
        tray = RectTuple(0, 1032, 1920, 1080)
        notify = RectTuple(1660, 1032, 1920, 1080)

        # 1. 标准 Win11 底部 48px 任务栏：贴在 TrayNotifyWnd 左侧 4px 间距处
        rect1 = compute_win11_layout(tray, notify, bar_w=110, bar_h=48, scale=1.0, screen_rect=screen)
        self.assertEqual(rect1, RectTuple(1546, 1032, 1656, 1080))

        # 2. 右侧已运行同类悬浮工具（如 Win11 版 TrafficMonitor）时，默认向左邻避让；切为右侧则锚定贴盘右端
        siblings = [RectTuple(1490, 1032, 1656, 1080)]
        rect2 = compute_win11_layout(
            tray, notify, bar_w=110, bar_h=48, scale=1.0, sibling_widgets=siblings, screen_rect=screen
        )
        self.assertEqual(rect2, RectTuple(1376, 1032, 1486, 1080))
        rect2_right = compute_win11_layout(
            tray,
            notify,
            bar_w=110,
            bar_h=48,
            scale=1.0,
            sibling_widgets=siblings,
            screen_rect=screen,
            placement=TaskbarPlacement.RIGHT_OF_SIBLINGS,
        )
        self.assertEqual(rect2_right, RectTuple(1546, 1032, 1656, 1080))

        # 3. TrayNotifyWnd 缺失或异常全宽时，回退至安全保留宽度且不返回 None
        rect_no_notify = compute_win11_layout(tray, None, bar_w=110, bar_h=48, scale=1.0, screen_rect=screen)
        self.assertIsNotNone(rect_no_notify)
        self.assertEqual(rect_no_notify.right, 1920 - 240)
        rect_bad_notify = compute_win11_layout(
            tray, RectTuple(0, 1032, 1920, 1080), bar_w=110, bar_h=48, scale=1.0, screen_rect=screen
        )
        self.assertEqual(rect_bad_notify, rect_no_notify)

        # 4. 自动隐藏任务栏滑出屏幕时返回 None 隐藏悬浮条
        hidden_tray = RectTuple(0, 1078, 1920, 1126)
        self.assertIsNone(
            compute_win11_layout(hidden_tray, notify, bar_w=110, bar_h=48, scale=1.0, screen_rect=screen)
        )

        # 5. 实机 2560x1440 Win10 + TrafficMonitor (2057..2215) 以及超宽 600px TM 均应平滑向左避让
        mon_2k = RectTuple(0, 0, 2560, 1440)
        tray_2k = RectTuple(0, 1400, 2560, 1440)
        notify_2k = RectTuple(2213, 1400, 2560, 1440)
        tm_2k = [RectTuple(2057, 1404, 2215, 1436)]
        self.assertEqual(
            compute_win11_layout(
                tray_2k,
                notify_2k,
                bar_w=105,
                bar_h=40,
                scale=1.0,
                sibling_widgets=tm_2k,
                screen_rect=mon_2k,
                placement=TaskbarPlacement.LEFT_OF_SIBLINGS,
            ),
            RectTuple(1948, 1400, 2053, 1440),
        )
        tm_wide = [RectTuple(1600, 1404, 2215, 1436)]  # > 420px 宽的多指标水平 TM
        self.assertEqual(
            compute_win11_layout(
                tray_2k,
                notify_2k,
                bar_w=105,
                bar_h=40,
                scale=1.0,
                sibling_widgets=tm_wide,
                screen_rect=mon_2k,
                placement=TaskbarPlacement.LEFT_OF_SIBLINGS,
            ),
            RectTuple(1491, 1400, 1596, 1440),
        )

    def test_unplugged_battery_upward_hysteresis_prevents_boundary_bounce(self):
        def poll_raw(raw_pct, charging=False):
            return DevicePollResult(
                hardware_connected=True,
                reading=BatteryReading(
                    percent=quantize_battery_step10(raw_pct) if raw_pct is not None else None,
                    charging=charging,
                    online=True,
                    model_name="G3 V2",
                    connection_mode=ConnectionMode.WIRELESS_24G,
                    raw_percent=raw_pct,
                ),
            )

        script = [
            poll_raw(64),                 # 1. 初始 64% -> 显示 60%
            poll_raw(65),                 # 2. 小幅回升 +1% -> 抑制在 60%
            poll_raw(66),                 # 3. 仍在 +2% -> 不允许爬坡，仍抑制在 60%
            poll_raw(67),                 # 4. +3%（未超过 3%）-> 仍抑制在 60%
            poll_raw(64),                 # 5. 回落到 64% -> 显示 60%
            poll_raw(68),                 # 6. +4%（超过 3% 滞回）-> 升至 70%
            poll_raw(64),                 # 7. 正常掉电至 64% -> 立即降回 60%
            poll_raw(65),                 # 8. 再次小幅回弹 +1% -> 抑制在 60%
            poll_raw(65, charging=True),  # 9. 插线充电 -> 立即允许升至 70%
            poll_raw(64),                 # 10. 拔线掉到 64% -> 降回 60%
            poll_raw(None, charging=True),  # 11. 纯充电哨兵（如 G3 0xFF）应清除未充电锚点
            poll_raw(66),                 # 12. 充后拔线读到 66% -> 允许升至 70%
        ]
        mgr = DeviceManager(enumerate_fn=lambda: [], upward_hysteresis_percent=3)
        mgr.register_driver(MockMouseDriver(script))

        expected = [60, 60, 60, 60, 60, 70, 60, 60, 70, 60, 60, 70]
        actual = [mgr.refresh_all()[DeviceSlot.MOUSE].percent for _ in expected]
        self.assertEqual(actual, expected)

    def test_low_battery_notifier_does_not_retrigger_on_14_15_bounce(self):
        def poll_raw(raw_pct, charging=False):
            return DevicePollResult(
                hardware_connected=True,
                reading=BatteryReading(
                    percent=quantize_battery_step10(raw_pct),
                    charging=charging,
                    online=True,
                    model_name="G3 V2",
                    connection_mode=ConnectionMode.WIRELESS_24G,
                    raw_percent=raw_pct,
                ),
            )

        mgr = DeviceManager(enumerate_fn=lambda: [], upward_hysteresis_percent=3)
        mgr.register_driver(
            MockMouseDriver(
                [
                    poll_raw(14),
                    poll_raw(15),
                    poll_raw(14),
                    poll_raw(16),
                    poll_raw(14),
                    poll_raw(18),
                    poll_raw(14),
                ]
            )
        )
        notifier = LowBatteryNotifier(notify_threshold=10, reset_threshold=15)

        alerts = [notifier.evaluate(mgr.refresh_all()[DeviceSlot.MOUSE]) for _ in range(7)]
        self.assertIsNotNone(alerts[0])
        self.assertIsNone(alerts[1])
        self.assertIsNone(alerts[2])
        self.assertIsNone(alerts[3])
        self.assertIsNone(alerts[4])
        self.assertIsNone(alerts[5])
        self.assertIsNotNone(alerts[6])


if __name__ == "__main__":
    unittest.main()
