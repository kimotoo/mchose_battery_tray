# -*- coding: utf-8 -*-
"""ATK X1 Ultimate (0x373B:0x11D9) 与 Compx 公共基类协议及驱动单元测试。"""

from contextlib import contextmanager
from pathlib import Path
import sys
import unittest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.manager import DeviceManager
from core.models import ConnectionMode, DeviceSlot
from drivers.a5_compx import MchoseA5CompxDriver, VID_COMPX
from drivers.atk_x1 import (
    AtkX1Driver,
    AtkX1Protocol,
    PID_ATK_X1_ULTIMATE_8K,
    PID_ATK_X1_ULTIMATE_8K_ALT,
    VID_ATK,
)
from drivers.hid_transport import HidDeviceCaps
from drivers.registry import create_default_manager


class _FakeCompxDevice:
    """模拟 cython-hidapi 的 Compx HID 设备句柄。"""

    def __init__(self, reads):
        self._reads = list(reads)
        self.writes = []
        self.nonblocking_history = []

    def set_nonblocking(self, flag):
        self.nonblocking_history.append(flag)

    def write(self, data):
        self.writes.append(bytes(data))
        return len(data)

    def read(self, size, timeout_ms=None):
        if self.nonblocking_history and self.nonblocking_history[-1] == 1:
            return []
        if not self._reads:
            return []
        return list(self._reads.pop(0))


def _make_compx_battery_pkt(
    percent: int,
    charging: bool = False,
    status: int = 0,
    voltage_mv: int = 4055,
) -> bytes:
    """构造 17 字节 Compx Report ID 0x08 / Cmd 0x04 电量应答帧。"""
    pkt = bytearray(17)
    pkt[0] = 0x08
    pkt[1] = 0x04
    pkt[2] = status & 0xFF
    pkt[5] = 0x02
    pkt[6] = percent & 0xFF
    pkt[7] = 1 if charging else 0
    pkt[8] = (voltage_mv >> 8) & 0xFF
    pkt[9] = voltage_mv & 0xFF
    pkt[16] = (0x55 - sum(pkt[:16])) & 0xFF
    return bytes(pkt)


class TestAtkX1ProtocolAndDriver(unittest.TestCase):
    def test_protocol_request_and_response(self) -> None:
        req = AtkX1Protocol.build_battery_request()
        self.assertEqual(
            req,
            bytes.fromhex("08 04 00 00 00 00 00 00 00 00 00 00 00 00 00 00 49"),
        )

        pkt_86 = _make_compx_battery_pkt(86, charging=False)
        self.assertEqual(
            AtkX1Protocol.parse_battery_response(pkt_86),
            (True, 86, False, 4055),
        )

        pkt_chg = _make_compx_battery_pkt(44, charging=True, voltage_mv=4150)
        self.assertEqual(
            AtkX1Protocol.parse_battery_response(pkt_chg),
            (True, 44, True, 4150),
        )

        bad_chk = bytearray(pkt_86)
        bad_chk[16] ^= 0xFF
        self.assertIsNone(AtkX1Protocol.parse_battery_response(bad_chk))

        sleep_pkt = _make_compx_battery_pkt(0, charging=False, status=1)
        self.assertEqual(
            AtkX1Protocol.parse_battery_response(sleep_pkt),
            (False, 0, False, 4055),
        )

    def test_device_matching_and_whitelist(self) -> None:
        self.assertTrue(AtkX1Driver.is_supported_device(VID_ATK, PID_ATK_X1_ULTIMATE_8K, ""))
        self.assertTrue(AtkX1Driver.is_supported_device(VID_ATK, PID_ATK_X1_ULTIMATE_8K_ALT, "ATK Mouse 8K Dongle"))
        self.assertTrue(
            AtkX1Driver.is_supported_device(VID_ATK, PID_ATK_X1_ULTIMATE_8K, "ATK 8K Dongle")
        )
        self.assertTrue(AtkX1Driver.is_supported_device(VID_ATK, 0x11DA, "ATK X1 Ultimate"))
        self.assertFalse(AtkX1Driver.is_supported_device(VID_COMPX, 0xF550, "MCHOSE A5 2.4G"))
        self.assertFalse(AtkX1Driver.is_supported_device(VID_ATK, 0x9999, "ATK F1 Ultimate"))

        # 实机抓包验证 (0x373B:0x101B, 50%, 3883 mV)
        live_pkt = bytes.fromhex("08 04 00 00 00 02 32 00 0f 2b 00 00 00 00 00 00 db")
        self.assertEqual(
            AtkX1Protocol.parse_battery_response(live_pkt),
            (True, 50, False, 3883),
        )

        # 确保 MchoseA5CompxDriver 不会误匹配 ATK 设备
        self.assertFalse(
            MchoseA5CompxDriver.is_supported_device(VID_ATK, PID_ATK_X1_ULTIMATE_8K, "ATK X1 Ultimate")
        )

    def test_driver_poll_sleep_cache_and_charging(self) -> None:
        pkt_online = _make_compx_battery_pkt(76, charging=False)
        pkt_charging = _make_compx_battery_pkt(74, charging=True)
        pkt_zero = _make_compx_battery_pkt(0, charging=False)

        # 第一轮在线(76->80%)，第二轮超时休眠(保留80%)，第三轮未就绪0%帧(保留80%)，第四轮充电(74->70%)
        scripted_reads = [
            [pkt_online],
            [],
            [pkt_zero],
            [pkt_charging],
        ]
        opened_paths = []

        @contextmanager
        def open_rx(path):
            opened_paths.append(path)
            yield _FakeCompxDevice(scripted_reads.pop(0))

        caps_map = {
            b"atk_ff04": HidDeviceCaps(usage_page=0xFF04, usage=0x0002, input_len=0, output_len=0, feature_len=8),
            b"atk_ff02_other": HidDeviceCaps(usage_page=0xFF02, usage=0x0001, input_len=17, output_len=17, feature_len=0),
            b"atk_ff02_ctrl": HidDeviceCaps(usage_page=0xFF02, usage=0x0002, input_len=17, output_len=17, feature_len=0),
        }

        hid_devices = [
            {
                "path": b"atk_ff04",
                "vendor_id": VID_ATK,
                "product_id": PID_ATK_X1_ULTIMATE_8K,
                "usage_page": 0xFF04,
                "usage": 0x0002,
                "product_string": "ATK X1 Ultimate 8K Dongle",
            },
            {
                "path": b"atk_ff02_other",
                "vendor_id": VID_ATK,
                "product_id": PID_ATK_X1_ULTIMATE_8K,
                "usage_page": 0xFF02,
                "usage": 0x0001,
                "product_string": "ATK X1 Ultimate 8K Dongle",
            },
            {
                "path": b"atk_ff02_ctrl",
                "vendor_id": VID_ATK,
                "product_id": PID_ATK_X1_ULTIMATE_8K,
                "usage_page": 0xFF02,
                "usage": 0x0002,
                "product_string": "ATK X1 Ultimate 8K Dongle",
            },
        ]

        # 仅保留控制口避免超时回退尝试 secondary 接口
        ctrl_only_devices = [hid_devices[0], hid_devices[2]]
        current_devices = list(ctrl_only_devices)
        driver = AtkX1Driver(open_device_fn=open_rx, get_caps_fn=lambda p: caps_map.get(p))
        mgr = DeviceManager(enumerate_fn=lambda: current_devices)
        mgr.register_driver(driver)

        # 同时验证 usage==0x0002 排序优先于其他 usage
        self.assertEqual(
            [d["path"] for d in AtkX1Driver.find_targets(hid_devices)],
            [b"atk_ff02_ctrl", b"atk_ff02_other"],
        )

        # 1. 在线读取：76 -> 80%
        s1 = mgr.refresh_all()
        self.assertEqual(opened_paths, [b"atk_ff02_ctrl"])
        self.assertTrue(s1[DeviceSlot.MOUSE].connected)
        self.assertTrue(s1[DeviceSlot.MOUSE].online)
        self.assertFalse(s1[DeviceSlot.MOUSE].charging)
        self.assertEqual(s1[DeviceSlot.MOUSE].percent, 80)
        self.assertEqual(s1[DeviceSlot.MOUSE].model_name, "ATK X1 Ultimate")
        self.assertEqual(s1[DeviceSlot.MOUSE].connection_mode, ConnectionMode.WIRELESS_24G)

        # 2. 休眠无应答：保留 80%
        s2 = mgr.refresh_all()
        self.assertTrue(s2[DeviceSlot.MOUSE].connected)
        self.assertFalse(s2[DeviceSlot.MOUSE].online)
        self.assertEqual(s2[DeviceSlot.MOUSE].percent, 80)

        # 3. 瞬态 0% 未就绪帧：在线但不覆盖上次 80%
        s3 = mgr.refresh_all()
        self.assertTrue(s3[DeviceSlot.MOUSE].online)
        self.assertEqual(s3[DeviceSlot.MOUSE].percent, 80)

        # 4. 充电状态：74 -> 70%, charging=True
        s4 = mgr.refresh_all()
        self.assertTrue(s4[DeviceSlot.MOUSE].online)
        self.assertTrue(s4[DeviceSlot.MOUSE].charging)
        self.assertEqual(s4[DeviceSlot.MOUSE].percent, 70)

        # 5. 拔出接收器：重置未连接状态
        current_devices.clear()
        s5 = mgr.refresh_all()
        self.assertFalse(s5[DeviceSlot.MOUSE].connected)
        self.assertIsNone(s5[DeviceSlot.MOUSE].percent)

    def test_mchose_a5_compx_regression_and_registry(self) -> None:
        # 确认默认管理器已注册 AtkX1Driver 且不影响 MchoseA5CompxDriver
        mgr = create_default_manager(enumerate_fn=lambda: [])
        mouse_drivers = mgr._drivers[DeviceSlot.MOUSE]
        driver_ids = [d.driver_id for d in mouse_drivers]
        self.assertIn("atk_x1", driver_ids)
        self.assertIn("mchose_a5_compx", driver_ids)

        # 回归验证 MchoseA5CompxDriver 在重构基类后仍正常工作
        pkt_90 = _make_compx_battery_pkt(90, charging=False)

        @contextmanager
        def open_a5(_path):
            yield _FakeCompxDevice([pkt_90])

        a5_driver = MchoseA5CompxDriver(
            open_device_fn=open_a5,
            get_caps_fn=lambda _p: HidDeviceCaps(0xFF02, 0x0002, 17, 17, 0),
        )
        res = a5_driver.poll(
            [
                {
                    "path": b"a5_ff02",
                    "vendor_id": VID_COMPX,
                    "product_id": 0xF550,
                    "usage_page": 0xFF02,
                    "usage": 0x0002,
                    "product_string": "MCHOSE A5 2.4G",
                }
            ]
        )
        self.assertIsNotNone(res)
        self.assertEqual(res.reading.percent, 90)
        self.assertEqual(res.reading.model_name, "A5 / A5 Pro")
        self.assertEqual(res.reading.connection_mode, ConnectionMode.WIRELESS_24G)


if __name__ == "__main__":
    unittest.main()
