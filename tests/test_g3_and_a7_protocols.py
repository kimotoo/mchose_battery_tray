# -*- coding: utf-8 -*-
"""迈从 G3 原始款、G3 V2/Pro/V3 与 A7 全系列协议及驱动集成单元测试。"""

from contextlib import contextmanager
from pathlib import Path
import sys
import unittest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.manager import DeviceManager
from core.models import ConnectionMode, DeviceSlot
from drivers.a5_compx import MchoseA5CompxDriver, MchoseA5CompxProtocol, VID_COMPX
from drivers.a5_legacy import MchoseA5LegacyDriver, MchoseA5LegacyProtocol, VID_A5_LEGACY
from drivers.g3_classic import (
    G3ClassicDriver,
    G3ClassicProtocol,
    PID_G3_CLASSIC,
    USAGE_G3_CLASSIC,
    USAGE_PAGE_G3_CLASSIC,
    VID_SINO_WEALTH,
)
from drivers.g3_v2 import (
    G3V2Driver,
    PID_A7E_PRO_WIRED,
    PID_A7E_WIRELESS,
    PID_G3_V2_PRO_WIRED,
    USAGE_CONFIG,
    USAGE_PAGE_CONFIG,
    VENDOR_ID_MCHOSE,
)
from drivers.hid_transport import HidDeviceCaps
from drivers.mchose_family import (
    MchoseCatalog,
    MchoseClassicDriver,
    MchoseClassicProtocol,
    MchoseModernDriver,
    MchoseModernProtocol,
    VENDOR_ID_COMPX_A5,
    VENDOR_ID_REALTEK_A5,
)
from drivers.registry import create_default_manager
from ui.tray_app import format_slot_line


def _make_a5_legacy_response(
    level: int,
    charging: bool,
    ready: bool = True,
    offset: int = 1,
    page: int = 0x00,
    cmd: int = 0x83,
    length: int = 65,
) -> bytes:
    """构造 0x2023 初代 A5/AX5 Feature Report 响应帧。"""
    buf = bytearray(max(length, offset + 8))
    buf[offset] = 0xA1 if ready else 0xA0
    buf[offset + 4] = page & 0xFF
    buf[offset + 5] = cmd & 0xFF
    buf[offset + 6] = 1 if charging else 0
    buf[offset + 7] = level & 0xFF
    return bytes(buf)


def _make_classic_response(
    vid: int,
    pid: int,
    mode: int,
    linked: bool,
    battery: int,
    charge: int,
    length: int = 21,
    battery_at_dec11: bool = False,
) -> bytes:
    """构造 0x11/0xF9 经典反码测试响应包（含前 2 字节不取反头）。"""
    dec = bytearray(max(12, length - 2))
    dec[0] = vid & 0xFF
    dec[1] = (vid >> 8) & 0xFF
    dec[2] = pid & 0xFF
    dec[3] = (pid >> 8) & 0xFF
    dec[4:8] = b"\x01\x02\x00\x00"
    dec[8] = (mode & 0x07) | (0x08 if linked else 0x00)
    if battery_at_dec11:
        dec[9] = 0
        dec[11] = battery & 0xFF
    else:
        dec[9] = battery & 0xFF
    dec[10] = charge & 0xFF
    raw = bytearray([0x11, 0xF9])
    raw.extend(b ^ 0xFF for b in dec)
    return bytes(raw)


def _make_modern_response(
    vid: int,
    pid: int,
    conn_code: int,
    charge_code: int,
    battery: int,
    length: int = 64,
    bad_xor: bool = False,
) -> bytes:
    """构造 0x4D/0x0900 新一代状态校验响应包。"""
    raw = bytearray(max(24, length))
    payload_len = 14
    raw[0] = 0x4D
    raw[1] = 0x01
    raw[2] = 0x01
    raw[3] = payload_len
    raw[4] = 0x00
    raw[5] = 0x09
    raw[6] = 0x00
    raw[7] = 0x00
    raw[8] = vid & 0xFF
    raw[9] = (vid >> 8) & 0xFF
    raw[10] = pid & 0xFF
    raw[11] = (pid >> 8) & 0xFF
    raw[18] = conn_code & 0xFF
    raw[19] = charge_code & 0xFF
    raw[20] = battery & 0xFF
    xor_sum = 0
    for i in range(2, 8 + payload_len):
        xor_sum ^= raw[i]
    raw[8 + payload_len] = xor_sum ^ 0x5A if bad_xor else xor_sum
    return bytes(raw)


class _FakeFeatureDevice:
    def __init__(self, responses):
        self._responses = list(responses)
        self.sent_features = []

    def send_feature_report(self, data):
        self.sent_features.append(bytes(data))
        return len(data)

    def get_feature_report(self, report_id, length):
        if self._responses:
            return list(self._responses.pop(0))
        return []


class _FakeModernDevice:
    def __init__(self, reads):
        self._reads = list(reads)
        self.writes = []

    def set_nonblocking(self, _flag):
        pass

    def write(self, data):
        self.writes.append(bytes(data))
        return len(data)

    def read(self, size, timeout_ms=None):
        if not self._reads:
            return []
        item = self._reads.pop(0)
        return list(item)


class TestG3ClassicAndA7Protocols(unittest.TestCase):
    def test_g3_classic_protocol_and_manager_charging_cache(self):
        # 1. 电量帧、前置 Report ID 兼容、充电哨兵 0xFF、休眠与 DPI 唤醒帧
        evt_bat = G3ClassicProtocol.parse_input_frame([0x09, 0x0A, 0x05, 84, 0, 0, 0, 0])
        self.assertEqual((evt_bat.kind, evt_bat.raw_percent), ("battery", 84))

        evt_pad = G3ClassicProtocol.parse_input_frame([0x00, 0x09, 0x0A, 0x05, 36, 0, 0, 0, 0])
        self.assertEqual((evt_pad.kind, evt_pad.raw_percent), ("battery", 36))

        evt_chg = G3ClassicProtocol.parse_input_frame([0x09, 0x0A, 0x05, 0xFF, 0, 0, 0, 0])
        self.assertEqual((evt_chg.kind, evt_chg.raw_percent), ("charging", None))
        # 0% 未就绪电量帧必须忽略
        self.assertIsNone(G3ClassicProtocol.parse_input_frame([0x09, 0x0A, 0x05, 0x00, 0, 0, 0, 0]))

        self.assertEqual(G3ClassicProtocol.parse_input_frame([0x09, 0x0A, 0x02, 0, 0, 0, 0, 0]).kind, "sleep")
        self.assertEqual(G3ClassicProtocol.parse_input_frame([0x09, 0x0A, 0x03, 1, 0, 0, 0, 0]).kind, "activity")

        # 2. 驱动状态机 + DeviceManager 集成：充电 0xFF 不应清除已有电量或误报休眠
        now = [100.0]
        woke = [0]
        hid_list = [
            {
                "vendor_id": VID_SINO_WEALTH,
                "product_id": PID_G3_CLASSIC,
                "usage_page": USAGE_PAGE_G3_CLASSIC,
                "usage": USAGE_G3_CLASSIC,
                "path": b"g3_col07",
            }
        ]
        drv = G3ClassicDriver(
            on_update=lambda: woke.__setitem__(0, woke[0] + 1),
            clock_fn=lambda: now[0],
            stale_after_sec=120.0,
            start_listener=False,
        )
        mgr = DeviceManager(enumerate_fn=lambda: hid_list)
        mgr.register_driver(drv)

        # 首轮尚未收到 48s 自发帧 -> 2.4G 读取中
        st0 = mgr.refresh_all()[DeviceSlot.MOUSE]
        self.assertTrue(st0.connected)
        self.assertTrue(st0.online)
        self.assertIsNone(st0.percent)
        self.assertIn("读取中", format_slot_line(st0))

        # 收到 84% 帧 -> 量化 80% 并立刻触发唤醒回调
        drv.process_frame([0x09, 0x0A, 0x05, 84, 0, 0, 0, 0])
        self.assertEqual(woke[0], 1)
        st1 = mgr.refresh_all()[DeviceSlot.MOUSE]
        self.assertEqual(st1.model_name, "G3")
        self.assertEqual(st1.percent, 80)
        self.assertFalse(st1.charging)

        # 收到 0% 异常帧时忽略且保留已有 80% 电量
        self.assertFalse(drv.process_frame([0x09, 0x0A, 0x05, 0x00, 0, 0, 0, 0]))
        self.assertEqual(mgr.refresh_all()[DeviceSlot.MOUSE].percent, 80)

        # 插线充电收到 0xFF 哨兵 -> online=True, charging=True，并保留上一次的 80%
        drv.process_frame([0x09, 0x0A, 0x05, 0xFF, 0, 0, 0, 0])
        st2 = mgr.refresh_all()[DeviceSlot.MOUSE]
        self.assertTrue(st2.online)
        self.assertTrue(st2.charging)
        self.assertEqual(st2.percent, 80)
        self.assertIn("充电中", format_slot_line(st2))

        # 收到休眠帧 -> online=False，仍保留 80%
        drv.process_frame([0x09, 0x0A, 0x02, 0x00, 0, 0, 0, 0])
        st3 = mgr.refresh_all()[DeviceSlot.MOUSE]
        self.assertFalse(st3.online)
        self.assertEqual(st3.percent, 80)

        # 超过 120s 无帧 -> 超时离线；DPI 按键唤醒后重新在线
        now[0] += 130.0
        drv.process_frame([0x09, 0x0A, 0x03, 0x01, 0, 0, 0, 0])
        drv.process_frame([0x09, 0x0A, 0x05, 92, 0, 0, 0, 0])
        st4 = mgr.refresh_all()[DeviceSlot.MOUSE]
        self.assertTrue(st4.online)
        self.assertEqual(st4.percent, 90)

        # 未收到任何电量时直接收到 0xFF 充电帧 -> 文案显示“2.4G 充电中”
        drv_fresh = G3ClassicDriver(clock_fn=lambda: now[0], start_listener=False)
        mgr_fresh = DeviceManager(enumerate_fn=lambda: hid_list)
        mgr_fresh.register_driver(drv_fresh)
        drv_fresh.process_frame([0x09, 0x0A, 0x05, 0xFF, 0, 0, 0, 0])
        st_fresh = mgr_fresh.refresh_all()[DeviceSlot.MOUSE]
        self.assertTrue(st_fresh.charging)
        self.assertIsNone(st_fresh.percent)
        self.assertIn("2.4G 充电中", format_slot_line(st_fresh))

    def test_classic_a7_protocol_and_driver_variants(self):
        # 1. 标准 A7 V2 Pro (0x4018) 通过共享接收器 0x1009 查询
        resp_a7_v2 = _make_classic_response(
            vid=0x3837, pid=0x4018, mode=1, linked=True, battery=66, charge=0, length=21
        )
        parsed = MchoseClassicProtocol.parse_feature_response(resp_a7_v2)
        self.assertIsNotNone(parsed)
        pid, name, mode, linked, bat, charge = parsed
        self.assertEqual((pid, name, mode, linked, bat, charge), (0x4018, "A7 V2 Pro", ConnectionMode.WIRELESS_24G, True, 66, 0))

        # 2. 旧款 A7 接收器 (0x5253:0x1021) 65B 且电量在 dec[11] 兼容偏移 + 充电中
        resp_old_a7 = _make_classic_response(
            vid=0x5253, pid=0x1021, mode=1, linked=True, battery=38, charge=1, length=65, battery_at_dec11=True
        )
        parsed_old = MchoseClassicProtocol.parse_feature_response(resp_old_a7)
        self.assertIsNotNone(parsed_old)
        self.assertEqual(parsed_old[4], 38)
        self.assertEqual(parsed_old[5], 1)

        # 3. 验证 FeatureLength 过滤、直连优先以及整十映射
        devices = [
            {"vendor_id": 0x5253, "product_id": 0x1021, "usage_page": 0xFF01, "usage": 1, "path": b"bad_small"},
            {"vendor_id": 0x5253, "product_id": 0x1021, "usage_page": 0xFF0B, "usage": 1, "path": b"good_a7", "product_string": "MCHOSE A7"},
        ]

        @contextmanager
        def fake_open(path):
            assert path == b"good_a7"
            yield _FakeFeatureDevice([resp_old_a7])

        def fake_caps(path):
            if path == b"bad_small":
                return HidDeviceCaps(0xFF01, 1, 8, 8, 8)
            return HidDeviceCaps(0xFF0B, 1, 65, 65, 65)

        drv = MchoseClassicDriver(open_device_fn=fake_open, get_caps_fn=fake_caps)
        res = drv.poll(devices)
        self.assertIsNotNone(res)
        self.assertEqual(res.reading.model_name, "MCHOSE A7")
        self.assertEqual(res.reading.percent, 40)
        self.assertTrue(res.reading.charging)

    def test_modern_g3_v3_and_a7_v3_protocol_and_driver(self):
        req = MchoseModernProtocol.build_status_request(64)
        self.assertEqual(len(req), 64)
        self.assertEqual(list(req[:9]), [0x4D, 0x01, 0x01, 0x00, 0x00, 0x09, 0x00, 0x00, 0x08])

        # XOR 错误校验拒绝
        bad_pkt = _make_modern_response(0x3837, 0x4030, conn_code=1, charge_code=0, battery=74, bad_xor=True)
        self.assertIsNone(MchoseModernProtocol.parse_status_response(bad_pkt, usb_pid=0x1018))

        # 忙碌响应 + 无关输入帧过滤 + A7 V3 (0x4030) 成功解析
        busy_pkt = bytearray(64)
        busy_pkt[0:6] = b"\x4D\x01\x01\x00\x00\x09"
        busy_pkt[8] = 0xFF
        unrelated_pkt = bytes([0x01, 0x02, 0x03])
        good_a7_v3 = _make_modern_response(0x3837, 0x4030, conn_code=1, charge_code=0, battery=74)

        dev_holder = []

        @contextmanager
        def fake_open(_path):
            d = _FakeModernDevice([[], unrelated_pkt, bytes(busy_pkt), good_a7_v3])
            dev_holder.append(d)
            yield d

        # 注意 flush_input 先清空空列表 [] 后读到 unrelated_pkt -> busy_pkt -> 重发 -> good_a7_v3
        drv = MchoseModernDriver(
            open_device_fn=fake_open,
            get_caps_fn=lambda _p: HidDeviceCaps(0xFF01, 1, 64, 64, 0),
        )
        res = drv.poll(
            [{"vendor_id": 0x3837, "product_id": 0x1018, "usage_page": 0xFF01, "usage": 1, "path": b"mod_dongle"}]
        )
        self.assertIsNotNone(res)
        self.assertEqual(res.reading.model_name, "A7 V3")
        self.assertEqual(res.reading.connection_mode, ConnectionMode.WIRELESS_24G)
        self.assertEqual(res.reading.percent, 70)
        self.assertEqual(len(dev_holder[0].writes), 2)

        # 验证 G3 V3 直连有线识别
        g3_v3_wired = _make_modern_response(0x3837, 0x4029, conn_code=1, charge_code=1, battery=55)
        parsed_g3v3 = MchoseModernProtocol.parse_status_response(g3_v3_wired, usb_pid=0x4029)
        self.assertEqual(parsed_g3v3[1], "G3 V3")
        self.assertEqual(parsed_g3v3[2], ConnectionMode.USB)
        self.assertEqual(parsed_g3v3[5], 1)

    def test_g3_v2_pro_and_a7e_extensions_and_registry(self):
        self.assertIsNotNone(
            G3V2Driver.find_target(
                [
                    {
                        "vendor_id": VENDOR_ID_MCHOSE,
                        "product_id": PID_G3_V2_PRO_WIRED,
                        "usage_page": USAGE_PAGE_CONFIG,
                        "usage": USAGE_CONFIG,
                        "path": b"g3v2pro",
                    }
                ]
            )
        )
        self.assertIsNotNone(
            G3V2Driver.find_target(
                [
                    {
                        "vendor_id": VENDOR_ID_MCHOSE,
                        "product_id": PID_A7E_WIRELESS,
                        "usage_page": USAGE_PAGE_CONFIG,
                        "usage": USAGE_CONFIG,
                        "path": b"a7e",
                    }
                ]
            )
        )
        self.assertEqual(
            G3V2Driver._resolve_model_name({"product_string": "MCHOSE A7e Pro"}, PID_A7E_PRO_WIRED),
            "A7e Pro",
        )

        # 验证默认注册管理器包含 G3 V2、G3 原始款、Classic、Modern、A5 Pro (0x2023)、A5 Compx (0x3554) 以及虚拟键盘
        mgr = create_default_manager(enumerate_fn=lambda: [])
        driver_ids = [d.driver_id for d in mgr._drivers[DeviceSlot.MOUSE]]
        self.assertEqual(
            driver_ids,
            [
                "mchose_g3_v2",
                "mchose_g3_classic",
                "mchose_classic",
                "mchose_modern",
                "mchose_a5_legacy",
                "mchose_a5_compx",
            ],
        )
        self.assertIn(0x0070, MchoseCatalog.CLASSIC_MODELS)
        self.assertIn(0x4029, MchoseCatalog.MODERN_MODELS)

    def test_a5_pro_legacy_0x2023_and_a5_ultra_extensions(self) -> None:
        """验证早期 A5 Pro (0x2023) 安全解析及 A5 Ultra / A5 V2 Ultra 扩展支持。"""
        req = MchoseA5LegacyProtocol.build_battery_request()
        self.assertEqual(len(req), 65)
        self.assertEqual(list(req[:7]), [0x00, 0x00, 0x00, 0x02, 0x02, 0x00, 0x83])

        # 0xA0 未就绪包及 level=0% 异常包必须丢弃，避免误报 0%/10%
        self.assertIsNone(
            MchoseA5LegacyProtocol.parse_battery_response(
                _make_a5_legacy_response(0, False, ready=False, offset=1)
            )
        )
        self.assertIsNone(
            MchoseA5LegacyProtocol.parse_battery_response(
                _make_a5_legacy_response(0, False, ready=True, offset=1)
            )
        )
        # ready=0xA1 兼容 offset=0 与 offset=1，超界电量与错误命令必须丢弃
        self.assertEqual(
            MchoseA5LegacyProtocol.parse_battery_response(
                _make_a5_legacy_response(85, True, ready=True, offset=0)
            ),
            (85, True),
        )
        self.assertEqual(
            MchoseA5LegacyProtocol.parse_battery_response(
                _make_a5_legacy_response(64, False, ready=True, offset=1)
            ),
            (64, False),
        )
        self.assertIsNone(
            MchoseA5LegacyProtocol.parse_battery_response(
                _make_a5_legacy_response(120, False, ready=True, offset=1)
            )
        )
        self.assertIsNone(
            MchoseA5LegacyProtocol.parse_battery_response(
                _make_a5_legacy_response(75, False, ready=True, offset=1, cmd=0x81)
            )
        )

        # 验证驱动严格跳过 0xFFA0 及短 Feature 集合，只打开 0xFFFF 且长度>=65 接口，并等待 0xA1 就绪
        opened_paths = []
        fake_dev = _FakeFeatureDevice(
            [
                _make_a5_legacy_response(0, False, ready=False, offset=1),
                _make_a5_legacy_response(85, True, ready=True, offset=1),
            ]
        )

        @contextmanager
        def open_a5(path):
            opened_paths.append(path)
            yield fake_dev

        caps_map = {
            b"a5_ffa0": HidDeviceCaps(usage_page=0xFFA0, usage=1, input_len=65, output_len=65, feature_len=65),
            b"a5_ffff_short": HidDeviceCaps(usage_page=0xFFFF, usage=1, input_len=21, output_len=21, feature_len=21),
            b"a5_ffff_ok": HidDeviceCaps(usage_page=0xFFFF, usage=1, input_len=65, output_len=65, feature_len=65),
        }
        driver = MchoseA5LegacyDriver(open_device_fn=open_a5, get_caps_fn=lambda p: caps_map.get(p))
        hid_list = [
            {
                "path": b"a5_ffa0",
                "vendor_id": VID_A5_LEGACY,
                "product_id": 0xF015,
                "usage_page": 0xFFA0,
                "usage": 1,
                "product_string": "MCHOSE A5 Pro Receiver",
            },
            {
                "path": b"a5_ffff_short",
                "vendor_id": VID_A5_LEGACY,
                "product_id": 0xF015,
                "usage_page": 0xFFFF,
                "usage": 1,
                "product_string": "MCHOSE A5 Pro Receiver",
            },
            {
                "path": b"a5_ffff_ok",
                "vendor_id": VID_A5_LEGACY,
                "product_id": 0xF015,
                "usage_page": 0xFFFF,
                "usage": 1,
                "product_string": "MCHOSE A5 Pro Receiver",
            },
        ]
        res = driver.poll(hid_list)
        self.assertIsNotNone(res)
        self.assertEqual(opened_paths, [b"a5_ffff_ok"])
        self.assertEqual(res.reading.percent, 90)
        self.assertEqual(res.reading.raw_percent, 85)
        self.assertTrue(res.reading.charging)
        self.assertTrue(res.reading.online)
        self.assertEqual(res.reading.model_name, "A5 Pro")
        self.assertEqual(res.reading.connection_mode, ConnectionMode.WIRELESS_24G)

        # 验证有线 0xF017 (A5 Pro) 优先级高于无线接收器，且 0xF019 识别为 A5 Pro Max
        opened_wired = []
        wired_dev = _FakeFeatureDevice([_make_a5_legacy_response(74, False, ready=True, offset=0)])

        @contextmanager
        def open_wired(path):
            opened_wired.append(path)
            yield wired_dev

        wired_driver = MchoseA5LegacyDriver(
            open_device_fn=open_wired,
            get_caps_fn=lambda _p: HidDeviceCaps(usage_page=0xFFFF, usage=1, input_len=65, output_len=65, feature_len=65),
        )
        wired_list = [
            {
                "path": b"a5_rx",
                "vendor_id": VID_A5_LEGACY,
                "product_id": 0xF013,
                "usage_page": 0xFFFF,
                "usage": 1,
                "product_string": "",
            },
            {
                "path": b"a5_usb",
                "vendor_id": VID_A5_LEGACY,
                "product_id": 0xF017,
                "usage_page": 0xFFFF,
                "usage": 1,
                "product_string": "",
            },
        ]
        wired_res = wired_driver.poll(wired_list)
        self.assertIsNotNone(wired_res)
        self.assertEqual(opened_wired, [b"a5_usb"])
        self.assertEqual(wired_res.reading.model_name, "A5 Pro")
        self.assertEqual(wired_res.reading.connection_mode, ConnectionMode.USB)
        self.assertEqual(wired_res.reading.percent, 70)

        max_res = wired_driver.poll(
            [
                {
                    "path": b"a5_max",
                    "vendor_id": VID_A5_LEGACY,
                    "product_id": 0xF019,
                    "usage_page": 0xFFFF,
                    "usage": 1,
                }
            ]
        )
        self.assertIsNotNone(max_res)
        self.assertEqual(max_res.reading.model_name, "A5 Pro Max")

        # 验证 0x2023 接收器在线但鼠标休眠（全部回 0xA0）时保留最后电量并显示休眠
        rx_devices = [
            {
                "path": b"a5_rx_sleep",
                "vendor_id": VID_A5_LEGACY,
                "product_id": 0xF013,
                "usage_page": 0xFFFF,
                "usage": 1,
                "product_string": "MCHOSE A5 Pro",
            }
        ]
        sleep_queue = [_make_a5_legacy_response(56, False, ready=True)] + [
            _make_a5_legacy_response(0, False, ready=False) for _ in range(10)
        ]
        sleep_dev = _FakeFeatureDevice(sleep_queue)

        @contextmanager
        def open_sleep(_path):
            yield sleep_dev

        sleep_driver = MchoseA5LegacyDriver(
            open_device_fn=open_sleep,
            get_caps_fn=lambda _p: HidDeviceCaps(usage_page=0xFFFF, usage=1, input_len=65, output_len=65, feature_len=65),
        )
        mgr_a5 = DeviceManager(enumerate_fn=lambda: rx_devices)
        mgr_a5.register_driver(sleep_driver)
        snap1 = mgr_a5.refresh_all()
        self.assertEqual(snap1[DeviceSlot.MOUSE].percent, 60)
        snap2 = mgr_a5.refresh_all()
        self.assertFalse(snap2[DeviceSlot.MOUSE].online)
        self.assertEqual(snap2[DeviceSlot.MOUSE].percent, 60)
        self.assertEqual(snap2[DeviceSlot.MOUSE].model_name, "A5 Pro")

        # 验证 A5 V2 Ultra (0x41E4:0x1101/0x1102) 与 A5 Ultra (0x0BDA:0x4762/0x2B67) 经典分支及防误识别
        self.assertTrue(MchoseCatalog.is_classic_device(VENDOR_ID_COMPX_A5, 0x1101))
        self.assertTrue(MchoseCatalog.is_classic_device(VENDOR_ID_COMPX_A5, 0x1102))
        self.assertFalse(MchoseCatalog.is_classic_device(VENDOR_ID_COMPX_A5, 0x2001))
        self.assertTrue(MchoseCatalog.is_classic_device(VENDOR_ID_REALTEK_A5, 0x4762))
        self.assertFalse(MchoseCatalog.is_classic_device(VENDOR_ID_REALTEK_A5, 0x9999))

        a5v2_pkt = _make_classic_response(VENDOR_ID_COMPX_A5, 0x1101, mode=1, linked=True, battery=64, charge=0)
        parsed_v2 = MchoseClassicProtocol.parse_feature_response(a5v2_pkt)
        self.assertEqual(parsed_v2, (0x1101, "A5 V2 Ultra", ConnectionMode.WIRELESS_24G, True, 64, 0))

        a5u_pkt = _make_classic_response(VENDOR_ID_REALTEK_A5, 0x2B67, mode=1, linked=True, battery=88, charge=0)
        parsed_u = MchoseClassicProtocol.parse_feature_response(a5u_pkt)
        self.assertEqual(parsed_u, (0x2B67, "A5 Ultra", ConnectionMode.WIRELESS_24G, True, 88, 0))
        self.assertEqual(MchoseCatalog.resolve_name(0x4762, "MCHOSE G3 Ultra"), "G3 Ultra")
        self.assertEqual(MchoseCatalog.resolve_name(0x4762, "MCHOSE A5 Ultra"), "A5 Ultra")

    def test_a5_compx_0x3554_protocol_and_driver(self) -> None:
        """验证 0x3554 Compx (A5 / A5 Pro 0xF550/0xF551) 协议解析与驱动安全仲裁。"""
        req = MchoseA5CompxProtocol.build_battery_request()
        self.assertEqual(
            req,
            bytes.fromhex("08 04 00 00 00 00 00 00 00 00 00 00 00 00 00 00 49"),
        )

        # 实机抓包还原验证：90%, 未充电, 4055 mV
        live_pkt = bytes.fromhex("08 04 00 00 00 02 5A 00 0F D7 00 00 00 00 00 00 07")
        self.assertEqual(
            MchoseA5CompxProtocol.parse_battery_response(live_pkt),
            (True, 90, False, 4055),
        )

        # 校验和错误/非法电量/非 0x04 帧必须丢弃，状态非零视为离线
        bad_chk = bytearray(live_pkt)
        bad_chk[16] ^= 0x01
        self.assertIsNone(MchoseA5CompxProtocol.parse_battery_response(bad_chk))

        offline_pkt = bytearray(live_pkt)
        offline_pkt[2] = 0x01
        offline_pkt[16] = (0x55 - sum(offline_pkt[:16])) & 0xFF
        self.assertEqual(
            MchoseA5CompxProtocol.parse_battery_response(offline_pkt),
            (False, 0, False, 4055),
        )

        # 构造充电包：raw=64%, charging=True -> 60%
        chg_pkt = bytearray(17)
        chg_pkt[0] = 0x08
        chg_pkt[1] = 0x04
        chg_pkt[5] = 0x02
        chg_pkt[6] = 64
        chg_pkt[7] = 1
        chg_pkt[8] = 0x10
        chg_pkt[9] = 0x36
        chg_pkt[16] = (0x55 - sum(chg_pkt[:16])) & 0xFF

        # 白名单与防误匹配测试
        self.assertTrue(MchoseA5CompxDriver.is_supported_device(VID_COMPX, 0xF550, "MCHOSE A5 2.4G"))
        self.assertTrue(MchoseA5CompxDriver.is_supported_device(VID_COMPX, 0xF551, ""))
        self.assertFalse(MchoseA5CompxDriver.is_supported_device(VID_COMPX, 0xFA09, "MCHOSE K99"))
        self.assertFalse(MchoseA5CompxDriver.is_supported_device(VID_COMPX, 0xF58A, "VXE R1"))

        opened = []
        fake_dev = _FakeModernDevice([[0x01, 0x02], [], live_pkt])

        @contextmanager
        def open_rx(path):
            opened.append(path)
            yield fake_dev

        caps_map = {
            b"a5_ff04": HidDeviceCaps(usage_page=0xFF04, usage=0x0002, input_len=0, output_len=0, feature_len=8),
            b"a5_ff02": HidDeviceCaps(usage_page=0xFF02, usage=0x0002, input_len=17, output_len=17, feature_len=0),
        }
        driver = MchoseA5CompxDriver(open_device_fn=open_rx, get_caps_fn=lambda p: caps_map.get(p))
        hid_list = [
            {
                "path": b"a5_ff04",
                "vendor_id": VID_COMPX,
                "product_id": 0xF550,
                "usage_page": 0xFF04,
                "usage": 0x0002,
                "product_string": "MCHOSE A5 2.4G",
            },
            {
                "path": b"a5_ff02",
                "vendor_id": VID_COMPX,
                "product_id": 0xF550,
                "usage_page": 0xFF02,
                "usage": 0x0002,
                "product_string": "MCHOSE A5 2.4G",
            },
        ]
        res = driver.poll(hid_list)
        self.assertIsNotNone(res)
        self.assertEqual(opened, [b"a5_ff02"])
        self.assertEqual(res.reading.percent, 90)
        self.assertEqual(res.reading.connection_mode, ConnectionMode.WIRELESS_24G)
        self.assertEqual(res.reading.model_name, "A5 / A5 Pro")
        self.assertFalse(res.reading.charging)

        # 验证有线 0xF551 优先于无线 0xF550 且正确识别充电状态 + 休眠保留缓存
        wired_opened = []
        wired_dev = _FakeModernDevice([[], bytes(chg_pkt)])

        @contextmanager
        def open_usb(path):
            wired_opened.append(path)
            yield wired_dev

        usb_driver = MchoseA5CompxDriver(
            open_device_fn=open_usb,
            get_caps_fn=lambda _p: HidDeviceCaps(usage_page=0xFF02, usage=0x0002, input_len=17, output_len=17, feature_len=0),
        )
        usb_res = usb_driver.poll(
            hid_list
            + [
                {
                    "path": b"a5_usb_ff02",
                    "vendor_id": VID_COMPX,
                    "product_id": 0xF551,
                    "usage_page": 0xFF02,
                    "usage": 0x0002,
                    "product_string": "MCHOSE A5 Pro",
                }
            ]
        )
        self.assertIsNotNone(usb_res)
        self.assertEqual(wired_opened, [b"a5_usb_ff02"])
        self.assertEqual(usb_res.reading.connection_mode, ConnectionMode.USB)
        self.assertEqual(usb_res.reading.percent, 60)
        self.assertEqual(usb_res.reading.model_name, "A5 Pro")
        self.assertTrue(usb_res.reading.charging)

        # 接收器在线但鼠标超时代休眠时保留历史缓存电量
        sleep_dev = _FakeModernDevice([[], live_pkt])

        @contextmanager
        def open_sleep(_p):
            yield sleep_dev

        sleep_driver = MchoseA5CompxDriver(
            open_device_fn=open_sleep,
            get_caps_fn=lambda _p: HidDeviceCaps(usage_page=0xFF02, usage=0x0002, input_len=17, output_len=17, feature_len=0),
        )
        mgr = DeviceManager(enumerate_fn=lambda: [hid_list[1]])
        mgr.register_driver(sleep_driver)
        s1 = mgr.refresh_all()
        self.assertEqual(s1[DeviceSlot.MOUSE].percent, 90)
        s2 = mgr.refresh_all()
        self.assertFalse(s2[DeviceSlot.MOUSE].online)
        self.assertEqual(s2[DeviceSlot.MOUSE].percent, 90)

        # Compx 遇到 status=0 且 raw_percent=0 的未就绪帧时保留上次 90%，不覆盖为 0%/10%
        zero_pkt = bytearray(live_pkt)
        zero_pkt[6] = 0
        zero_pkt[16] = (0x55 - sum(zero_pkt[:16])) & 0xFF
        sleep_dev._reads = [[], bytes(zero_pkt)]
        s3 = mgr.refresh_all()
        self.assertTrue(s3[DeviceSlot.MOUSE].online)
        self.assertEqual(s3[DeviceSlot.MOUSE].percent, 90)

        # 经典/新一代协议遇到 linked=True 但 raw_bat=0 (未就绪) 时同样保留上次有效电量，且会在同轮重试中拾取后续有效帧
        mod_unready = _make_modern_response(0x3837, 0x4030, conn_code=1, charge_code=0, battery=0)
        mod_valid = _make_modern_response(0x3837, 0x4030, conn_code=1, charge_code=0, battery=74)
        mod_reads = [[[], mod_unready, mod_valid], [[], mod_unready]]

        @contextmanager
        def open_mod(_p):
            yield _FakeModernDevice(mod_reads.pop(0))

        mod_driver = MchoseModernDriver(
            open_device_fn=open_mod,
            get_caps_fn=lambda _p: HidDeviceCaps(0xFF01, 1, 64, 64, 0),
        )
        mgr_mod = DeviceManager(
            enumerate_fn=lambda: [{"vendor_id": 0x3837, "product_id": 0x1018, "usage_page": 0xFF01, "usage": 1, "path": b"m0"}]
        )
        mgr_mod.register_driver(mod_driver)
        sm1 = mgr_mod.refresh_all()
        self.assertEqual(sm1[DeviceSlot.MOUSE].percent, 70)
        sm2 = mgr_mod.refresh_all()
        self.assertEqual(sm2[DeviceSlot.MOUSE].percent, 70)


if __name__ == "__main__":
    unittest.main()
