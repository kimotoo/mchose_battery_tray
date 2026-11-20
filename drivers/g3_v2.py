# -*- coding: utf-8 -*-
"""迈从 G3 V2 鼠标 HID 驱动与 0x55/0xAA 协议实现。

协议字节规范完整参考项目根目录 DESIGN.md 第 2.1 节（源自 M-RustHub 实机验证协议）。
"""

import time
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from core.interfaces import BaseDeviceDriver, IConfigurableMouse
from core.models import (
    BatteryReading,
    ConnectionMode,
    DevicePollResult,
    DeviceSlot,
    LodLevel,
    MouseConfig,
    PollingRate,
    quantize_battery_step10,
)

from . import hid_transport


VENDOR_ID_MCHOSE = 0x3837
VENDOR_ID_MCHOSE_ALT = (0xA8A4, 0xA8A5)
PID_G3_V2_WIRED = 0x418C
PID_G3_V2_WIRELESS = 0x4245
PID_G3_V2_PRO_WIRED = 0x4042
PID_A7E_WIRED = 0x4012
PID_A7E_PRO_WIRED = 0x4013
PID_A7E_WIRELESS = 0x1008
PID_ALT_WIRELESS = 0x2255

_WIRED_MODEL_PIDS: Dict[int, str] = {
    PID_G3_V2_WIRED: "G3 V2",
    PID_G3_V2_PRO_WIRED: "G3 V2 Pro",
    PID_A7E_WIRED: "A7e",
    PID_A7E_PRO_WIRED: "A7e Pro",
}
_WIRELESS_MODEL_PIDS: Dict[int, str] = {
    PID_G3_V2_WIRELESS: "G3 V2",
    PID_A7E_WIRELESS: "A7e",
}

USAGE_PAGE_CONFIG = 0xFF01
USAGE_CONFIG = 0x0010

MAGIC_TX = 0x55  # 85
MAGIC_RX = 0xAA  # 170

CMD_QUERY_VERSION = 0x03  # 3
CMD_GET_BUTTONS = 0x08    # 8
CMD_SET_BUTTONS = 0x09    # 9
CMD_GET_CONFIG = 0x0E     # 14
CMD_SET_CONFIG = 0x0F     # 15
CMD_GET_BATTERY = 0x30    # 48
CMD_GET_ONLINE = 0xED     # 237


class G3V2Protocol:
    """迈从 G3 V2 无状态二进制编解码器（纯函数，便于直接单元测试）。"""

    PACKET_PAYLOAD_LEN = 64
    PACKET_REPORT_LEN = 65

    @classmethod
    def build_packet_65(cls, payload: Sequence[int]) -> bytes:
        """构造 Windows hidapi 65 字节发送帧（Report ID 0x00 + 64 字节补零 Payload）。"""
        frame = bytearray(cls.PACKET_REPORT_LEN)
        frame[0] = 0x00
        copy_len = min(len(payload), cls.PACKET_PAYLOAD_LEN)
        frame[1 : 1 + copy_len] = payload[:copy_len]
        return bytes(frame)

    @classmethod
    def align_response_64(cls, raw: Sequence[int]) -> Optional[bytes]:
        """对齐接收包偏移，将带或不带 Report ID 的回包归一化为以 0xAA 开头的有效帧。"""
        if not raw:
            return None
        if raw[0] == MAGIC_RX and len(raw) >= 10:
            return bytes(raw[: cls.PACKET_PAYLOAD_LEN])
        if len(raw) >= 11 and raw[1] == MAGIC_RX:
            return bytes(raw[1 : 1 + cls.PACKET_PAYLOAD_LEN])
        return None

    @classmethod
    def build_online_request(cls) -> bytes:
        """Cmd 0xED (237)：无线在线状态查询请求帧。"""
        return cls.build_packet_65((MAGIC_TX, CMD_GET_ONLINE))

    @classmethod
    def build_battery_request(cls) -> bytes:
        """Cmd 0x30 (48)：电量百分比与充电状态查询请求帧。"""
        return cls.build_packet_65((MAGIC_TX, CMD_GET_BATTERY, 0xA5, 0x0B, 0x2E, 0x01, 0x01, 0x01, 0x00, 0x00))

    @classmethod
    def build_version_request(cls) -> bytes:
        """Cmd 0x03 (3)：固件版本与构建日期查询请求帧（预留）。"""
        return cls.build_packet_65((MAGIC_TX, CMD_QUERY_VERSION))

    @classmethod
    def build_get_config_request(cls) -> bytes:
        """Cmd 0x0E (14)：读取完整鼠标配置请求帧（预留）。"""
        return cls.build_packet_65((MAGIC_TX, CMD_GET_CONFIG, 0xA5, 0x0B, 0x30, 0x01, 0x01, 0x01, 0x00, 0x00))

    @classmethod
    def build_set_config_request(cls, cfg: MouseConfig) -> bytes:
        """Cmd 0x0F (15)：写入完整鼠标配置请求帧（预留）。"""
        pkt = bytearray(cls.PACKET_PAYLOAD_LEN)
        pkt[0:10] = bytes((MAGIC_TX, CMD_SET_CONFIG, 0xAE, 0x0A, 0x30, 0x01, 0x01, 0x01, 0x00, 0x00))

        stages_count = max(1, min(6, cfg.dpi_stages_count))
        active_stage = max(1, min(stages_count, cfg.active_dpi_stage))
        pkt[10] = cfg.report_rate.to_protocol_byte()
        pkt[11] = stages_count
        pkt[12] = active_stage

        for i in range(6):
            offset = 13 + i * 2
            val = int(cfg.dpi_stages[i]) & 0xFFFF
            pkt[offset] = val & 0xFF
            pkt[offset + 1] = (val >> 8) & 0xFF

        pkt[48] = cfg.scroll_flag & 0xFF
        pkt[49] = int(cfg.lod) & 0xFF
        pkt[50] = cfg.sensor_flags & 0xFF
        pkt[51] = cfg.debounce_ms & 0xFF
        pkt[52] = cfg.sleep_minutes & 0xFF
        pkt[53] = cfg.highspeed_mode & 0xFF
        pkt[55] = cfg.sensor_angle & 0xFF
        return cls.build_packet_65(pkt)

    @classmethod
    def build_get_buttons_request(cls) -> bytes:
        """Cmd 0x08 (8)：读取 8 槽位按键映射请求帧（预留）。"""
        return cls.build_packet_65((MAGIC_TX, CMD_GET_BUTTONS, 0xA5, 0x0B, 0x20, 0x00, 0x00, 0x00, 0x00, 0x00))

    @staticmethod
    def parse_online_response(resp: bytes) -> Optional[bool]:
        """解析 Cmd 0xED 响应，返回在线布尔值或 None（非匹配帧）。

        根据官方网页驱动与 G3 V2 实机抓包：resp[8] == 2 表示在线，1 表示休眠，0 表示未连接。
        """
        if len(resp) < 9 or resp[0] != MAGIC_RX or resp[1] != CMD_GET_ONLINE:
            return None
        return resp[8] >= 2

    @staticmethod
    def quantize_battery_step10(raw_percent: int) -> int:
        """按迈从官方 M HUB 规则将 1% 原始电量映射为整 10 挡位。"""
        return quantize_battery_step10(raw_percent)

    @staticmethod
    def parse_battery_response(resp: bytes) -> Optional[Tuple[int, bool]]:
        """解析 Cmd 0x30 响应，返回 (percent, charging) 或 None。"""
        if len(resp) < 10 or resp[0] != MAGIC_RX or resp[1] != CMD_GET_BATTERY:
            return None
        percent = resp[8]
        if not (1 <= percent <= 100):
            return None
        charging = resp[9] != 0
        return percent, charging

    @staticmethod
    def parse_version_response(resp: bytes) -> Optional[Tuple[str, str]]:
        """解析 Cmd 0x03 固件版本与构建日期（预留）。"""
        if len(resp) < 32 or resp[0] != MAGIC_RX or resp[1] != CMD_QUERY_VERSION:
            return None
        firmware = resp[8:23].decode("utf-8", errors="ignore").rstrip("\x00").strip()
        build_date = resp[26:32].decode("utf-8", errors="ignore").rstrip("\x00").strip()
        if not firmware:
            return None
        return firmware, build_date

    @staticmethod
    def parse_config_response(resp: bytes) -> Optional[MouseConfig]:
        """解析 Cmd 0x0E 完整鼠标配置响应（预留）。"""
        if len(resp) < 56 or resp[0] != MAGIC_RX or resp[1] != CMD_GET_CONFIG:
            return None

        dpi_stage1 = resp[13] | (resp[14] << 8)
        is_uninitialized = (
            (resp[13] == 0 and resp[14] == 0 and resp[15] == 0)
            or (resp[13] == 255 and resp[14] == 255 and resp[15] == 255)
            or resp[11] == 0
            or dpi_stage1 < 50
        )
        if is_uninitialized:
            return None

        report_rate = PollingRate.from_protocol_byte(resp[10])
        dpi_stages_count = max(1, min(6, resp[11]))
        active_dpi_stage = max(1, min(dpi_stages_count, resp[12]))

        dpi_stages = [400, 800, 1600, 3200, 6400, 12000]
        for i in range(6):
            offset = 13 + i * 2
            val = resp[offset] | (resp[offset + 1] << 8)
            if 50 <= val <= 50000:
                dpi_stages[i] = val

        sensor_angle = resp[55] - 256 if resp[55] >= 128 else resp[55]
        return MouseConfig(
            report_rate=report_rate,
            active_dpi_stage=active_dpi_stage,
            dpi_stages_count=dpi_stages_count,
            dpi_stages=dpi_stages,
            lod=LodLevel.LOW_1MM if resp[49] == 0 else LodLevel.HIGH_2MM,
            debounce_ms=resp[51],
            sleep_minutes=resp[52],
            sensor_flags=resp[50],
            scroll_flag=resp[48],
            highspeed_mode=resp[53],
            sensor_angle=sensor_angle,
        )


class G3V2Driver(BaseDeviceDriver, IConfigurableMouse):
    """迈从 G3 V2 驱动：支持有线/2.4G 电量读取及预留参数配置能力。"""

    slot = DeviceSlot.MOUSE
    driver_id = "mchose_g3_v2"
    display_name = "G3 V2"
    priority = 10
    is_virtual = False

    def __init__(
        self,
        open_device_fn: Optional[Callable[[Any], Any]] = None,
        enumerate_fn: Optional[Callable[[], List[Dict[str, Any]]]] = None,
    ) -> None:
        self._open_device_fn = open_device_fn or hid_transport.open_device
        self._enumerate_fn = enumerate_fn or (lambda: hid_transport.enumerate_devices(VENDOR_ID_MCHOSE))

    @staticmethod
    def is_target_interface(dev_info: Dict[str, Any], expected_pid: int) -> bool:
        """校验是否匹配 0x55/0xAA 系列鼠标的 0xFF01 / 0x0010 配置接口。"""
        vid = dev_info.get("vendor_id")
        pid = dev_info.get("product_id")
        if pid != expected_pid:
            return False
        valid_vid = (vid == VENDOR_ID_MCHOSE) or (vid in VENDOR_ID_MCHOSE_ALT and pid == PID_ALT_WIRELESS)
        return (
            valid_vid
            and dev_info.get("usage_page") == USAGE_PAGE_CONFIG
            and dev_info.get("usage") == USAGE_CONFIG
        )

    @classmethod
    def find_target(cls, hid_devices: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """按有线直连优先于 2.4G 无线接收器的策略选择 0x55/0xAA 家族设备接口。"""
        candidate_pids = (
            *_WIRED_MODEL_PIDS.keys(),
            *_WIRELESS_MODEL_PIDS.keys(),
            PID_ALT_WIRELESS,
        )
        for pid in candidate_pids:
            for info in hid_devices:
                if cls.is_target_interface(info, pid):
                    return info
        return None

    @classmethod
    def _resolve_model_name(cls, target: Dict[str, Any], pid: int) -> str:
        prod = str(target.get("product_string") or "")
        for tag in ("G3 V2 Pro", "G3 V2", "A7e Pro", "A7e"):
            if tag.lower() in prod.lower():
                return tag
        return _WIRED_MODEL_PIDS.get(pid) or _WIRELESS_MODEL_PIDS.get(pid) or cls.display_name

    def _send_and_recv(
        self,
        dev: Any,
        packet: bytes,
        expected_cmd: int,
        timeout_ms: int = 150,
    ) -> Optional[bytes]:
        """发送请求并在超时内等待匹配命令号的归一化响应帧。"""
        hid_transport.flush_input(dev)
        hid_transport.write_packet(dev, packet)

        deadline = time.monotonic() + (timeout_ms / 1000.0)
        while True:
            remaining_ms = int((deadline - time.monotonic()) * 1000)
            if remaining_ms <= 0:
                return None
            raw = hid_transport.read_with_timeout(dev, G3V2Protocol.PACKET_REPORT_LEN, remaining_ms)
            if not raw:
                return None
            aligned = G3V2Protocol.align_response_64(raw)
            if aligned is not None and aligned[1] == expected_cmd:
                return aligned

    def _query_online(self, dev: Any, is_wireless: bool) -> bool:
        if not is_wireless:
            return True
        req = G3V2Protocol.build_online_request()
        for attempt in range(3):
            if attempt > 0:
                time.sleep(0.04)
            try:
                resp = self._send_and_recv(dev, req, CMD_GET_ONLINE, timeout_ms=150)
            except Exception:
                continue
            if resp is not None:
                parsed = G3V2Protocol.parse_online_response(resp)
                if parsed is not None:
                    return parsed
        return False

    def _query_battery(self, dev: Any) -> Optional[Tuple[int, bool]]:
        req = G3V2Protocol.build_battery_request()
        for attempt in range(3):
            if attempt > 0:
                time.sleep(0.04)
            try:
                resp = self._send_and_recv(dev, req, CMD_GET_BATTERY, timeout_ms=150)
            except Exception:
                continue
            if resp is not None:
                parsed = G3V2Protocol.parse_battery_response(resp)
                if parsed is not None:
                    return parsed
        return None

    def poll(self, hid_devices: List[Dict[str, Any]]) -> Optional[DevicePollResult]:
        """检测并读取 G3 V2 电量与连接状态。"""
        target = self.find_target(hid_devices)
        if target is None:
            return None

        pid = int(target.get("product_id", 0))
        vid = int(target.get("vendor_id", VENDOR_ID_MCHOSE))
        is_wireless = pid not in _WIRED_MODEL_PIDS
        mode = ConnectionMode.WIRELESS_24G if is_wireless else ConnectionMode.USB
        model_name = self._resolve_model_name(target, pid)

        try:
            with self._open_device_fn(target["path"]) as dev:
                if not self._query_online(dev, is_wireless):
                    return DevicePollResult(
                        hardware_connected=True,
                        reading=BatteryReading(
                            percent=None,
                            charging=False,
                            online=False,
                            model_name=model_name,
                            connection_mode=mode,
                            vid=vid,
                            pid=pid,
                        ),
                    )

                bat = self._query_battery(dev)
                if bat is None:
                    return DevicePollResult(
                        hardware_connected=True,
                        reading=BatteryReading(
                            percent=None,
                            charging=False,
                            online=True,
                            model_name=model_name,
                            connection_mode=mode,
                            vid=vid,
                            pid=pid,
                        ),
                    )

                raw_percent, charging = bat
                step_percent = G3V2Protocol.quantize_battery_step10(raw_percent)
                return DevicePollResult(
                    hardware_connected=True,
                    reading=BatteryReading(
                        percent=step_percent,
                        charging=charging,
                        online=True,
                        model_name=model_name,
                        connection_mode=mode,
                        vid=vid,
                        pid=pid,
                        raw_charge_code=1 if charging else 0,
                        raw_percent=raw_percent,
                    ),
                )
        except Exception:
            # 设备刚插入或系统短暂占用时视为硬件仍在，等待下轮刷新
            return DevicePollResult(
                hardware_connected=True,
                reading=BatteryReading(
                    percent=None,
                    charging=False,
                    online=False,
                    model_name=model_name,
                    connection_mode=mode,
                    vid=vid,
                    pid=pid,
                ),
            )

    # ------------------------------------------------------------------
    # IConfigurableMouse 扩展接口实现（预留：后续挂载托盘快捷调参功能）
    # ------------------------------------------------------------------
    def get_config(self) -> Optional[MouseConfig]:
        target = self.find_target(self._enumerate_fn())
        if target is None:
            return None
        is_wireless = target.get("product_id") == PID_G3_V2_WIRELESS
        try:
            with self._open_device_fn(target["path"]) as dev:
                if not self._query_online(dev, is_wireless):
                    return None
                req = G3V2Protocol.build_get_config_request()
                for attempt in range(4):
                    if attempt > 0:
                        time.sleep(0.06)
                    resp = self._send_and_recv(dev, req, CMD_GET_CONFIG, timeout_ms=150)
                    if resp is not None:
                        cfg = G3V2Protocol.parse_config_response(resp)
                        if cfg is not None:
                            return cfg
        except Exception:
            return None
        return None

    def set_config(self, config: MouseConfig) -> bool:
        target = self.find_target(self._enumerate_fn())
        if target is None:
            return False
        is_wireless = target.get("product_id") == PID_G3_V2_WIRELESS
        try:
            with self._open_device_fn(target["path"]) as dev:
                if not self._query_online(dev, is_wireless):
                    return False
                req = G3V2Protocol.build_set_config_request(config)
                for attempt in range(3):
                    if attempt > 0:
                        time.sleep(0.04)
                    resp = self._send_and_recv(dev, req, CMD_SET_CONFIG, timeout_ms=150)
                    if resp is not None:
                        time.sleep(0.03)
                        return True
        except Exception:
            return False
        return False

    def set_active_dpi_stage(self, stage: int) -> bool:
        cfg = self.get_config()
        if cfg is None:
            return False
        cfg.active_dpi_stage = max(1, min(cfg.dpi_stages_count, stage))
        return self.set_config(cfg)

    def set_polling_rate(self, rate: PollingRate) -> bool:
        cfg = self.get_config()
        if cfg is None:
            return False
        cfg.report_rate = rate
        return self.set_config(cfg)

    def set_sleep_timeout(self, minutes: int) -> bool:
        cfg = self.get_config()
        if cfg is None:
            return False
        cfg.sleep_minutes = max(0, min(100, minutes))
        return self.set_config(cfg)
