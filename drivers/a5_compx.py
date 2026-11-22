# -*- coding: utf-8 -*-
"""迈从 A5 / A5 Pro Compx 方案 (VID=0x3554, Report ID 0x08 / Cmd 0x04) 驱动与协议实现。

实机验证（2026-11-19，VID=0x3554, PID=0xF550 'MCHOSE A5 2.4G'）：
- 通信接口：usage_page = 0xFF02, usage = 0x0002, Input=17B, Output=17B
- 查询帧（17 字节）：08 04 00 00 00 00 00 00 00 00 00 00 00 00 00 00 49
  （尾字节校验和满足 sum(bytes[0..16]) & 0xFF == 0x55）
- 应答帧（17 字节）：08 04 00 00 00 [status] [percent] [charging] [volt_hi] [volt_lo] ... [chk]
  实测示例：08 04 00 00 00 02 5A 00 0F D7 00 00 00 00 00 00 07 -> 90%, 未充电, 4055 mV
- 有线与无线 PID：0xF550（2.4G 接收器）、0xF551（有线直连）
"""

from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from core.interfaces import BaseDeviceDriver
from core.models import (
    BatteryReading,
    ConnectionMode,
    DevicePollResult,
    DeviceSlot,
    quantize_battery_step10,
)

from . import hid_transport


VID_COMPX = 0x3554

COMPX_A5_WIRED_PIDS: Dict[int, str] = {
    0xF551: "A5 / A5 Pro",
}

COMPX_A5_WIRELESS_PIDS: Dict[int, str] = {
    0xF550: "A5 / A5 Pro",
}

USAGE_PAGE_COMPX_VENDOR = 0xFF02
USAGE_COMPX_VENDOR = 0x0002

COMPX_REPORT_ID = 0x08
COMPX_REPLY_IDS = (0x08, 0x09)
COMPX_CMD_BATTERY = 0x04
COMPX_FRAME_LEN = 17
COMPX_CHECKSUM_SUM = 0x55


class MchoseA5CompxProtocol:
    """迈从 Compx (0x3554) 17 字节 HID 电量帧无状态编解码器。"""

    @staticmethod
    def build_battery_request(length: int = COMPX_FRAME_LEN) -> bytes:
        """构造 17 字节电量查询输出帧（08 04 ... 49）。"""
        size = max(COMPX_FRAME_LEN, int(length))
        frame = bytearray(size)
        frame[0] = COMPX_REPORT_ID
        frame[1] = COMPX_CMD_BATTERY
        frame[16] = (COMPX_CHECKSUM_SUM - sum(frame[:16])) & 0xFF
        return bytes(frame)

    @staticmethod
    def parse_battery_response(
        raw: Sequence[int],
    ) -> Optional[Tuple[bool, int, bool, int]]:
        """解析 Compx 17 字节应答帧，返回 (linked, raw_percent, charging, voltage_mv)。"""
        if not raw or len(raw) < COMPX_FRAME_LEN:
            return None
        if raw[0] not in COMPX_REPLY_IDS or raw[1] != COMPX_CMD_BATTERY:
            return None
        if (sum(int(b) & 0xFF for b in raw[:COMPX_FRAME_LEN]) & 0xFF) != COMPX_CHECKSUM_SUM:
            return None

        status = int(raw[2]) & 0xFF
        raw_percent = int(raw[6]) & 0xFF
        charging = (int(raw[7]) & 0xFF) > 0
        voltage_mv = ((int(raw[8]) & 0xFF) << 8) | (int(raw[9]) & 0xFF)

        if raw_percent > 100:
            return None
        if status != 0 or (raw_percent == 0 and voltage_mv == 0 and not charging):
            return False, 0, False, voltage_mv

        return True, raw_percent, charging, voltage_mv


class CompxBatteryDriverBase(BaseDeviceDriver):
    """基于 Compx 17 字节 Report 0x08/Cmd 0x04 协议的鼠标驱动基类。"""

    slot = DeviceSlot.MOUSE
    is_virtual = False

    def __init__(
        self,
        open_device_fn: Optional[Callable[[Any], Any]] = None,
        get_caps_fn: Optional[Callable[[Any], Optional[hid_transport.HidDeviceCaps]]] = None,
    ) -> None:
        self._open_device_fn = open_device_fn or hid_transport.open_device
        self._get_caps_fn = get_caps_fn or hid_transport.get_device_caps

    @classmethod
    def is_supported_device(cls, vid: int, pid: int, product_string: str = "") -> bool:
        raise NotImplementedError

    @classmethod
    def _is_wired_target(cls, target: Dict[str, Any]) -> bool:
        return False

    @classmethod
    def _resolve_model_name(cls, target: Dict[str, Any]) -> str:
        return cls.display_name

    @classmethod
    def find_targets(cls, hid_devices: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """筛选 0xFF02 控制接口，按有线优先于无线及 usage==0x0002 排序。"""
        wired_pref: List[Dict[str, Any]] = []
        wired_other: List[Dict[str, Any]] = []
        wireless_pref: List[Dict[str, Any]] = []
        wireless_other: List[Dict[str, Any]] = []

        for info in hid_devices:
            vid = int(info.get("vendor_id") or 0)
            pid = int(info.get("product_id") or 0)
            prod = str(info.get("product_string") or "")
            if not cls.is_supported_device(vid, pid, prod):
                continue
            if int(info.get("usage_page") or 0) != USAGE_PAGE_COMPX_VENDOR:
                continue

            is_wired = cls._is_wired_target(info)
            pref = int(info.get("usage") or 0) == USAGE_COMPX_VENDOR
            if is_wired:
                (wired_pref if pref else wired_other).append(info)
            else:
                (wireless_pref if pref else wireless_other).append(info)

        return wired_pref + wired_other + wireless_pref + wireless_other

    @staticmethod
    def _caps_ok(caps: Optional[hid_transport.HidDeviceCaps]) -> bool:
        if caps is None:
            return True
        if caps.usage_page != USAGE_PAGE_COMPX_VENDOR:
            return False
        return caps.input_len >= COMPX_FRAME_LEN and caps.output_len >= COMPX_FRAME_LEN

    def _query_target(self, target: Dict[str, Any]) -> Optional[Tuple[bool, int, bool, int]]:
        path = target["path"]
        caps = self._get_caps_fn(path)
        if not self._caps_ok(caps):
            return None

        in_len = caps.input_len if caps is not None else COMPX_FRAME_LEN
        out_len = caps.output_len if caps is not None else COMPX_FRAME_LEN
        req = MchoseA5CompxProtocol.build_battery_request(out_len)

        unready_fallback: Optional[Tuple[bool, int, bool, int]] = None
        with self._open_device_fn(path) as dev:
            hid_transport.flush_input(dev, read_size=max(COMPX_FRAME_LEN, in_len), max_reads=8)
            for _attempt in range(3):
                hid_transport.write_packet(dev, req)
                for _read in range(4):
                    raw = hid_transport.read_with_timeout(dev, max(COMPX_FRAME_LEN, in_len), 140)
                    if not raw:
                        break
                    parsed = MchoseA5CompxProtocol.parse_battery_response(raw)
                    if parsed is not None:
                        linked, raw_percent, _charging, _mv = parsed
                        if not linked or raw_percent > 0:
                            return parsed
                        unready_fallback = parsed
        return unready_fallback

    def poll(self, hid_devices: List[Dict[str, Any]]) -> Optional[DevicePollResult]:
        targets = self.find_targets(hid_devices)
        if not targets:
            return None

        first = targets[0]
        usb_vid = int(first.get("vendor_id") or 0)
        usb_pid = int(first.get("product_id") or 0)
        mode = ConnectionMode.USB if self._is_wired_target(first) else ConnectionMode.WIRELESS_24G
        model_name = self._resolve_model_name(first)

        saw_accessible = False
        for target in targets:
            if not self._caps_ok(self._get_caps_fn(target["path"])):
                continue
            saw_accessible = True
            t_vid = int(target.get("vendor_id") or 0)
            t_pid = int(target.get("product_id") or 0)
            t_mode = ConnectionMode.USB if self._is_wired_target(target) else ConnectionMode.WIRELESS_24G
            t_name = self._resolve_model_name(target)
            try:
                parsed = self._query_target(target)
            except Exception:
                continue
            if parsed is None:
                continue

            linked, raw_percent, charging, _voltage_mv = parsed
            if not linked:
                return DevicePollResult(
                    hardware_connected=True,
                    reading=BatteryReading(
                        percent=None,
                        charging=False,
                        online=False,
                        model_name=t_name,
                        connection_mode=t_mode,
                        vid=t_vid,
                        pid=t_pid,
                    ),
                )

            if raw_percent == 0:
                return DevicePollResult(
                    hardware_connected=True,
                    reading=BatteryReading(
                        percent=None,
                        charging=charging,
                        online=True,
                        model_name=t_name,
                        connection_mode=t_mode,
                        vid=t_vid,
                        pid=t_pid,
                        raw_charge_code=1 if charging else 0,
                    ),
                )

            return DevicePollResult(
                hardware_connected=True,
                reading=BatteryReading(
                    percent=quantize_battery_step10(raw_percent),
                    charging=charging,
                    online=True,
                    model_name=t_name,
                    connection_mode=t_mode,
                    vid=t_vid,
                    pid=t_pid,
                    raw_charge_code=1 if charging else 0,
                    raw_percent=raw_percent,
                ),
            )

        if not saw_accessible:
            return None

        return DevicePollResult(
            hardware_connected=True,
            reading=BatteryReading(
                percent=None,
                charging=False,
                online=False,
                model_name=model_name,
                connection_mode=mode,
                vid=usb_vid,
                pid=usb_pid,
            ),
        )


class MchoseA5CompxDriver(CompxBatteryDriverBase):
    """迈从 A5 / A5 Pro Compx 方案（VID=0x3554）鼠标驱动。"""

    driver_id = "mchose_a5_compx"
    display_name = "A5 / A5 Pro"
    priority = 19

    @classmethod
    def is_supported_device(cls, vid: int, pid: int, product_string: str = "") -> bool:
        if vid != VID_COMPX:
            return False
        if pid in COMPX_A5_WIRED_PIDS or pid in COMPX_A5_WIRELESS_PIDS:
            return True
        upper = (product_string or "").upper()
        return "MCHOSE" in upper and "A5" in upper

    @classmethod
    def _is_wired_target(cls, target: Dict[str, Any]) -> bool:
        return int(target.get("product_id") or 0) in COMPX_A5_WIRED_PIDS

    @classmethod
    def _resolve_model_name(cls, target: Dict[str, Any]) -> str:
        prod = str(target.get("product_string") or "").strip()
        upper = prod.upper()
        if "A5 PRO MAX" in upper:
            return "A5 Pro Max"
        if "A5 PRO" in upper:
            return "A5 Pro"
        if "A5" in upper:
            return "A5 / A5 Pro"
        pid = int(target.get("product_id") or 0)
        return (
            COMPX_A5_WIRED_PIDS.get(pid)
            or COMPX_A5_WIRELESS_PIDS.get(pid)
            or cls.display_name
        )
