# -*- coding: utf-8 -*-
"""WOBKEY Crush 80 三模键盘（USB / 蓝牙 BLE / 2.4G）协议与驱动单元测试。"""

from contextlib import contextmanager
from pathlib import Path
import sys
import unittest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.manager import DeviceManager
from core.models import ConnectionMode, DeviceSlot
from drivers.crush80 import (
    BleBatteryReading,
    Crush80Driver,
    Crush80Protocol,
    EVISION_CMD_BATTERY,
    EVISION_CMD_ONLINE,
    PID_CRUSH80_WIRED,
    PID_CRUSH80_WIRELESS,
    USAGE_EVISION_VENDOR,
    USAGE_PAGE_EVISION_VENDOR,
    USAGE_PAGE_VIA,
    USAGE_VIA,
    VID_CRUSH80_USB,
)
from drivers.hid_transport import HidDeviceCaps
from drivers.registry import create_default_manager
from drivers.virtual_keyboard import VirtualKeyboardDriver


class _FakeCrush80Device:
    """按发送命令与序列号动态应答的模拟 EVision HID 设备。"""

    def __init__(self, handler):
        self._handler = handler
        self._pending = []
        self.writes = []

    def set_nonblocking(self, _flag):
        pass

    def write(self, data):
        raw = bytes(data)
        self.writes.append(raw)
        resp = self._handler(raw)
        if resp:
            self._pending.append(bytes(resp))
        return len(raw)

    def read(self, size, timeout_ms=None):
        if not self._pending:
            return []
        return list(self._pending.pop(0))


def _reply_battery(seq: int, level: int, state: int = 0) -> bytes:
    buf = bytearray(64)
    buf[0] = 0x04
    buf[1] = seq & 0xFF
    buf[2] = (seq >> 8) & 0xFF
    buf[3] = EVISION_CMD_BATTERY
    buf[8] = level & 0xFF
    buf[9] = state & 0xFF
    return bytes(buf)


def _reply_online(seq: int, online: bool) -> bytes:
    buf = bytearray(64)
    buf[0] = 0x04
    buf[1] = seq & 0xFF
    buf[2] = (seq >> 8) & 0xFF
    buf[3] = EVISION_CMD_ONLINE
    buf[8] = 0xFF if online else 0x00
    return bytes(buf)


class TestCrush80ProtocolAndDriver(unittest.TestCase):
    def test_protocol_requests_and_response_parsing(self) -> None:
        req = Crush80Protocol.build_battery_request(sequence=0x0102, length=64)
        self.assertEqual(len(req), 64)
        self.assertEqual(list(req[:4]), [0x04, 0x02, 0x01, EVISION_CMD_BATTERY])

        req_min = Crush80Protocol.build_online_request(sequence=1, length=16)
        self.assertEqual(len(req_min), 32)
        self.assertEqual(list(req_min[:4]), [0x04, 0x01, 0x00, EVISION_CMD_ONLINE])

        self.assertEqual(
            Crush80Protocol.parse_battery_response(_reply_battery(0x0102, 76, 0), 0x0102),
            (76, False),
        )
        self.assertEqual(
            Crush80Protocol.parse_battery_response(_reply_battery(5, 64, 1), 5),
            (64, True),
        )
        self.assertEqual(
            Crush80Protocol.parse_battery_response(_reply_battery(6, 100, 2), 6),
            (100, True),
        )
        # 序列号不匹配 / 非法电量 / 非法状态拒绝
        self.assertIsNone(Crush80Protocol.parse_battery_response(_reply_battery(5, 64, 0), 6))
        self.assertIsNone(Crush80Protocol.parse_battery_response(_reply_battery(5, 101, 0), 5))
        self.assertIsNone(Crush80Protocol.parse_battery_response(_reply_battery(5, 50, 9), 5))

        self.assertIs(Crush80Protocol.parse_online_response(_reply_online(3, True), 3), True)
        self.assertIs(Crush80Protocol.parse_online_response(_reply_online(3, False), 3), False)
        self.assertIsNone(Crush80Protocol.parse_online_response(_reply_online(3, True), 4))

    def test_device_whitelist_excludes_unrelated_brands(self) -> None:
        self.assertTrue(
            Crush80Driver.is_supported_hid_device(
                VID_CRUSH80_USB, PID_CRUSH80_WIRELESS, "Wireless Gaming Keyboard"
            )
        )
        self.assertTrue(
            Crush80Driver.is_supported_hid_device(VID_CRUSH80_USB, PID_CRUSH80_WIRED, "Crush 80")
        )
        self.assertFalse(
            Crush80Driver.is_supported_hid_device(
                VID_CRUSH80_USB, PID_CRUSH80_WIRELESS, "ATK V75X 2.4G Dongle"
            )
        )
        self.assertFalse(
            Crush80Driver.is_supported_hid_device(VID_CRUSH80_USB, 0x22A0, "AULA V9")
        )

    def test_tri_mode_arbitration_and_cache_preservation(self) -> None:
        hid_wired = [
            {
                "path": b"crush_usb_ff1c",
                "vendor_id": VID_CRUSH80_USB,
                "product_id": PID_CRUSH80_WIRED,
                "usage_page": USAGE_PAGE_EVISION_VENDOR,
                "usage": USAGE_EVISION_VENDOR,
                "product_string": "Crush 80",
            }
        ]
        hid_24g = [
            {
                "path": b"crush_24g_via",
                "vendor_id": VID_CRUSH80_USB,
                "product_id": PID_CRUSH80_WIRELESS,
                "usage_page": USAGE_PAGE_VIA,
                "usage": USAGE_VIA,
                "product_string": "Wireless Gaming Keyboard",
            },
            {
                "path": b"crush_24g_ff1c",
                "vendor_id": VID_CRUSH80_USB,
                "product_id": PID_CRUSH80_WIRELESS,
                "usage_page": USAGE_PAGE_EVISION_VENDOR,
                "usage": USAGE_EVISION_VENDOR,
                "product_string": "Wireless Gaming Keyboard",
            },
        ]

        state = {
            "hid": list(hid_wired + hid_24g),
            "ble": BleBatteryReading(percent=84, connected=True, model_name="Crush80"),
            "usb_level": 74,
            "usb_charging": True,
            "24g_level": 0,
            "24g_online": True,
        }

        def handle_packet(path, raw):
            seq = raw[1] | (raw[2] << 8)
            cmd = raw[3]
            if path == b"crush_usb_ff1c" and cmd == EVISION_CMD_BATTERY:
                return _reply_battery(seq, state["usb_level"], 1 if state["usb_charging"] else 0)
            if path == b"crush_24g_ff1c":
                if cmd == EVISION_CMD_BATTERY and state["24g_level"] is not None:
                    return _reply_battery(seq, state["24g_level"], 0)
                if cmd == EVISION_CMD_ONLINE and state["24g_online"] is not None:
                    return _reply_online(seq, state["24g_online"])
            return None

        @contextmanager
        def open_dev(path):
            yield _FakeCrush80Device(lambda raw: handle_packet(path, raw))

        driver = Crush80Driver(
            open_device_fn=open_dev,
            get_caps_fn=lambda _p: HidDeviceCaps(USAGE_PAGE_EVISION_VENDOR, USAGE_EVISION_VENDOR, 64, 64, 0),
            ble_reader_fn=lambda: state["ble"],
        )
        mgr = DeviceManager(enumerate_fn=lambda: state["hid"])
        mgr.register_driver(driver)
        mgr.register_driver(VirtualKeyboardDriver())

        # 1. 三模同时存在时 USB 有线优先：74% -> 70%, 充电中
        s1 = mgr.refresh_all()[DeviceSlot.KEYBOARD]
        self.assertTrue(s1.connected)
        self.assertTrue(s1.online)
        self.assertTrue(s1.charging)
        self.assertEqual(s1.percent, 70)
        self.assertEqual(s1.connection_mode, ConnectionMode.USB)
        self.assertFalse(s1.is_virtual)

        # 2. 拔掉 USB 线，蓝牙连接着 + 2.4G 接收器也插着：蓝牙优先（84% -> 80%）
        state["hid"] = list(hid_24g)
        s2 = mgr.refresh_all()[DeviceSlot.KEYBOARD]
        self.assertTrue(s2.connected)
        self.assertTrue(s2.online)
        self.assertFalse(s2.charging)
        self.assertEqual(s2.percent, 80)
        self.assertEqual(s2.connection_mode, ConnectionMode.BLUETOOTH)

        # 3. 切到 2.4G（蓝牙断开）：2.4G 返回 0x1A->0（暂不提供非零值）且 0xAA->0xFF 在线，保留 80% 缓存
        state["ble"] = None
        s3 = mgr.refresh_all()[DeviceSlot.KEYBOARD]
        self.assertTrue(s3.connected)
        self.assertTrue(s3.online)
        self.assertEqual(s3.percent, 80)
        self.assertEqual(s3.connection_mode, ConnectionMode.WIRELESS_24G)

        # 4. 2.4G 读到真实电量 66% -> 70%
        state["24g_level"] = 66
        s4 = mgr.refresh_all()[DeviceSlot.KEYBOARD]
        self.assertTrue(s4.online)
        self.assertEqual(s4.percent, 70)
        self.assertEqual(s4.connection_mode, ConnectionMode.WIRELESS_24G)

        # 5. 2.4G 休眠无应答：保留 70% 缓存并标为休眠
        state["24g_level"] = None
        state["24g_online"] = False
        s5 = mgr.refresh_all()[DeviceSlot.KEYBOARD]
        self.assertTrue(s5.connected)
        self.assertFalse(s5.online)
        self.assertEqual(s5.percent, 70)

        # 6. 拔出接收器且无蓝牙：重置未连接
        state["hid"] = []
        s6 = mgr.refresh_all()[DeviceSlot.KEYBOARD]
        self.assertFalse(s6.connected)
        self.assertIsNone(s6.percent)

    def test_registry_registers_crush80_before_virtual_keyboard(self) -> None:
        mgr = create_default_manager(enumerate_fn=lambda: [])
        kb_ids = [d.driver_id for d in mgr._drivers[DeviceSlot.KEYBOARD]]
        self.assertEqual(kb_ids, ["wobkey_crush80", "virtual_keyboard_default"])


if __name__ == "__main__":
    unittest.main()
