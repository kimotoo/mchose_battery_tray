# -*- coding: utf-8 -*-
"""G3 V2 协议编解码与驱动行为单元测试（无真实硬件依赖）。"""

from contextlib import contextmanager
from pathlib import Path
import sys
import unittest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.models import ConnectionMode, LodLevel, MouseConfig, PollingRate
from drivers.g3_v2 import (
    CMD_GET_BATTERY,
    CMD_GET_CONFIG,
    CMD_GET_ONLINE,
    CMD_QUERY_VERSION,
    CMD_SET_CONFIG,
    G3V2Driver,
    G3V2Protocol,
    MAGIC_RX,
    MAGIC_TX,
    PID_G3_V2_WIRED,
    PID_G3_V2_WIRELESS,
    USAGE_CONFIG,
    USAGE_PAGE_CONFIG,
    VENDOR_ID_MCHOSE,
)


class FakeHidDevice:
    """模拟 cython-hidapi 的设备句柄行为。"""

    def __init__(self, responses):
        self.responses = list(responses)
        self.written = []
        self.nonblocking_history = []
        self.closed = False

    def set_nonblocking(self, flag):
        self.nonblocking_history.append(flag)

    def write(self, data):
        self.written.append(bytes(data))
        return len(data)

    def read(self, size, timeout_ms=0):
        # 仅在阻塞查询模式下返回响应；flush 阶段 (nonblocking=1) 返回空
        if self.nonblocking_history and self.nonblocking_history[-1] == 1:
            return []
        if not self.responses:
            return []
        return list(self.responses.pop(0))

    def close(self):
        self.closed = True


class TestG3V2Protocol(unittest.TestCase):
    def test_build_requests_layout(self):
        online_pkt = G3V2Protocol.build_online_request()
        self.assertEqual(len(online_pkt), 65)
        self.assertEqual(online_pkt[0], 0x00)
        self.assertEqual(online_pkt[1], MAGIC_TX)
        self.assertEqual(online_pkt[2], CMD_GET_ONLINE)
        self.assertTrue(all(b == 0 for b in online_pkt[3:]))

        bat_pkt = G3V2Protocol.build_battery_request()
        self.assertEqual(len(bat_pkt), 65)
        self.assertEqual(list(bat_pkt[:11]), [0x00, 0x55, 0x30, 0xA5, 0x0B, 0x2E, 0x01, 0x01, 0x01, 0x00, 0x00])
        self.assertTrue(all(b == 0 for b in bat_pkt[11:]))

    def test_align_response_with_and_without_report_id(self):
        direct = bytearray(64)
        direct[0] = MAGIC_RX
        direct[1] = CMD_GET_BATTERY
        direct[8] = 87
        direct[9] = 1
        aligned_direct = G3V2Protocol.align_response_64(direct)
        self.assertIsNotNone(aligned_direct)
        self.assertEqual(len(aligned_direct), 64)
        self.assertEqual(G3V2Protocol.parse_battery_response(aligned_direct), (87, True))

        shifted = bytearray(65)
        shifted[0] = 0x00
        shifted[1] = MAGIC_RX
        shifted[2] = CMD_GET_BATTERY
        shifted[9] = 42
        shifted[10] = 0
        aligned_shifted = G3V2Protocol.align_response_64(shifted)
        self.assertIsNotNone(aligned_shifted)
        self.assertEqual(G3V2Protocol.parse_battery_response(aligned_shifted), (42, False))

        # 拒绝非 0xAA 起始帧
        self.assertIsNone(G3V2Protocol.align_response_64([0x01] * 64))

    def test_battery_bounds_rejected(self):
        for invalid_pct in (0, 101, 255):
            pkt = bytearray(64)
            pkt[0] = MAGIC_RX
            pkt[1] = CMD_GET_BATTERY
            pkt[8] = invalid_pct
            self.assertIsNone(G3V2Protocol.parse_battery_response(bytes(pkt)))

    def test_config_roundtrip_and_version_parse(self):
        cfg = MouseConfig(
            report_rate=PollingRate.HZ_1000,
            active_dpi_stage=2,
            dpi_stages_count=4,
            dpi_stages=[400, 1600, 3200, 6400, 8000, 12000],
            lod=LodLevel.LOW_1MM,
            debounce_ms=4,
            sleep_minutes=10,
            sensor_flags=2,
            scroll_flag=1,
            highspeed_mode=1,
            sensor_angle=-3,
        )
        tx = G3V2Protocol.build_set_config_request(cfg)
        self.assertEqual(tx[1], MAGIC_TX)
        self.assertEqual(tx[2], CMD_SET_CONFIG)

        # 构造 Cmd 0x0E 响应镜像验证解析一致性
        rx = bytearray(tx[1:65])
        rx[0] = MAGIC_RX
        rx[1] = CMD_GET_CONFIG
        parsed = G3V2Protocol.parse_config_response(bytes(rx))
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.report_rate, PollingRate.HZ_1000)
        self.assertEqual(parsed.active_dpi_stage, 2)
        self.assertEqual(parsed.dpi_stages_count, 4)
        self.assertEqual(parsed.dpi_stages, [400, 1600, 3200, 6400, 8000, 12000])
        self.assertEqual(parsed.lod, LodLevel.LOW_1MM)
        self.assertEqual(parsed.debounce_ms, 4)
        self.assertEqual(parsed.sleep_minutes, 10)
        self.assertEqual(parsed.sensor_angle, -3)

        # 验证版本响应解析
        ver_rx = bytearray(64)
        ver_rx[0] = MAGIC_RX
        ver_rx[1] = CMD_QUERY_VERSION
        ver_rx[8:15] = b"v1.2.3\x00"
        ver_rx[26:32] = b"250318"
        self.assertEqual(G3V2Protocol.parse_version_response(bytes(ver_rx)), ("v1.2.3", "250318"))


class TestG3V2Driver(unittest.TestCase):
    @staticmethod
    def _make_info(pid, usage_page=USAGE_PAGE_CONFIG, usage=USAGE_CONFIG, path=b"path"):
        return {
            "vendor_id": VENDOR_ID_MCHOSE,
            "product_id": pid,
            "usage_page": usage_page,
            "usage": usage,
            "path": path,
        }

    def test_wired_preferred_over_wireless(self):
        devices = [
            self._make_info(PID_G3_V2_WIRELESS, path=b"wireless"),
            self._make_info(PID_G3_V2_WIRED, usage_page=0xFF00, path=b"wrong_page"),
            self._make_info(PID_G3_V2_WIRED, path=b"wired"),
        ]
        chosen = G3V2Driver.find_target(devices)
        self.assertIsNotNone(chosen)
        self.assertEqual(chosen["path"], b"wired")

    def test_wireless_online_and_battery_read(self):
        online_resp = bytearray(65)
        online_resp[0] = 0x00
        online_resp[1] = MAGIC_RX
        online_resp[2] = CMD_GET_ONLINE
        online_resp[9] = 2

        bat_resp = bytearray(64)
        bat_resp[0] = MAGIC_RX
        bat_resp[1] = CMD_GET_BATTERY
        bat_resp[8] = 73
        bat_resp[9] = 0

        fake_dev = FakeHidDevice([online_resp, bat_resp])

        @contextmanager
        def open_fake(_path):
            try:
                yield fake_dev
            finally:
                fake_dev.close()

        driver = G3V2Driver(open_device_fn=open_fake)
        res = driver.poll([self._make_info(PID_G3_V2_WIRELESS)])
        self.assertIsNotNone(res)
        self.assertTrue(res.hardware_connected)
        self.assertIsNotNone(res.reading)
        self.assertTrue(res.reading.online)
        self.assertEqual(res.reading.raw_percent, 73)
        self.assertEqual(res.reading.percent, 70)
        self.assertFalse(res.reading.charging)
        self.assertEqual(res.reading.connection_mode, ConnectionMode.WIRELESS_24G)
        self.assertTrue(fake_dev.closed)
        self.assertEqual(len(fake_dev.written), 2)

    def test_quantize_battery_step10_matches_official_ranges(self):
        expected = [
            (0, 10), (1, 10), (14, 10),
            (15, 20), (24, 20),
            (25, 30), (34, 30),
            (35, 40), (36, 40), (44, 40),
            (45, 50), (54, 50),
            (55, 60), (64, 60),
            (65, 70), (74, 70),
            (75, 80), (84, 80),
            (85, 90), (94, 90),
            (95, 100), (100, 100),
        ]
        for raw, step in expected:
            self.assertEqual(G3V2Protocol.quantize_battery_step10(raw), step)

    def test_wireless_sleeping_skips_battery_query(self):
        for sleep_code in (0, 1):
            offline_resp = bytearray(64)
            offline_resp[0] = MAGIC_RX
            offline_resp[1] = CMD_GET_ONLINE
            offline_resp[8] = sleep_code

            fake_dev = FakeHidDevice([offline_resp])

            @contextmanager
            def open_fake(_path, dev=fake_dev):
                yield dev

            driver = G3V2Driver(open_device_fn=open_fake)
            res = driver.poll([self._make_info(PID_G3_V2_WIRELESS)])
            self.assertIsNotNone(res)
            self.assertTrue(res.hardware_connected)
            self.assertIsNotNone(res.reading)
            self.assertFalse(res.reading.online)
            self.assertIsNone(res.reading.percent)
            self.assertEqual(len(fake_dev.written), 1)

    def test_wired_skips_online_check_and_reads_charging(self):
        bat_resp = bytearray(64)
        bat_resp[0] = MAGIC_RX
        bat_resp[1] = CMD_GET_BATTERY
        bat_resp[8] = 88
        bat_resp[9] = 1

        fake_dev = FakeHidDevice([bat_resp])

        @contextmanager
        def open_fake(_path):
            yield fake_dev

        driver = G3V2Driver(open_device_fn=open_fake)
        res = driver.poll([self._make_info(PID_G3_V2_WIRED)])
        self.assertTrue(res.hardware_connected)
        self.assertEqual(res.reading.raw_percent, 88)
        self.assertEqual(res.reading.percent, 90)
        self.assertTrue(res.reading.charging)
        self.assertEqual(res.reading.connection_mode, ConnectionMode.USB)
        self.assertEqual(len(fake_dev.written), 1)


if __name__ == "__main__":
    unittest.main()
