# -*- coding: utf-8 -*-
"""迈从初代 A5 / A5 Pro / A5 Pro Max / AX5 (VID=0x2023, Cmd 0x83) HID 驱动与协议实现。

协议规范综合来自 DriverCore 逆向 (MouseBattery) 与 HaloBattery 实机验证：
- Vendor ID：0x2023
- Product ID：
    有线：0xF017 (A5 Pro)、0xF019 (A5 Pro Max)、0xF026 (AX5 Pro)、0xF022 (AX5 Pro Max)
    无线：0xF013 (A5 1K 接收器)、0xF015 (A5 4K/通配接收器)、0xF024 (AX5 接收器)
- 接口安全：匹配 usage_page == 0xFFFF（绝对跳过相邻 0xFFA0）且 FeatureReportByteLength >= 65
- Feature Report (65 字节，Report ID 0x00)：
    发送：[0x00, 0x00, 0x00, 0x02, 0x02, 0x00, 0x83] + [0x00] * 58
    响应：识别 ready 标志 0xA1（可能位于 resp[0] 或 resp[1]；0xA0 为未就绪回显，必须忽略）
          偏移 at+4 == 0x00 (page), at+5 == 0x83 (cmd), at+6 = charging (>0), at+7 = level (0..100)
"""

import time
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


VID_A5_LEGACY = 0x2023

A5_LEGACY_WIRED_PIDS: Dict[int, str] = {
    0xF017: "A5 Pro",
    0xF019: "A5 Pro Max",
    0xF026: "AX5 Pro",
    0xF022: "AX5 Pro Max",
}

A5_LEGACY_WIRELESS_PIDS: Dict[int, str] = {
    0xF013: "A5 系列",
    0xF015: "A5 系列",
    0xF024: "AX5 系列",
}

USAGE_PAGE_A5_PREFERRED = 0xFFFF
USAGE_PAGE_A5_FORBIDDEN = 0xFFA0

A5_REPORT_ID = 0x00
A5_REPORT_LEN = 65
A5_ROUTE = 0x02
A5_LEN = 0x02
A5_PAGE = 0x00
A5_CMD_BATTERY = 0x83
A5_READY_MARKER = 0xA1


class MchoseA5LegacyProtocol:
    """迈从 0x2023 初代 A5/AX5 65 字节 Feature Report 无状态编解码器。"""

    @staticmethod
    def build_battery_request() -> bytes:
        """构造 65 字节电量查询 Feature Report（Report ID 0x00, route 2, len 2, page 0, cmd 0x83）。"""
        frame = bytearray(A5_REPORT_LEN)
        frame[0] = A5_REPORT_ID
        frame[3] = A5_ROUTE
        frame[4] = A5_LEN
        frame[5] = A5_PAGE
        frame[6] = A5_CMD_BATTERY
        return bytes(frame)

    @staticmethod
    def parse_battery_response(raw: Sequence[int]) -> Optional[Tuple[int, bool]]:
        """解析 0x2023 Feature 应答帧，过滤 0xA0 未就绪包，返回 (raw_percent, charging)。"""
        if not raw or len(raw) < 9:
            return None

        if raw[0] == A5_READY_MARKER:
            at = 0
        elif raw[1] == A5_READY_MARKER:
            at = 1
        else:
            return None

        if len(raw) < at + 8:
            return None
        if raw[at + 4] != A5_PAGE or raw[at + 5] != A5_CMD_BATTERY:
            return None

        level = int(raw[at + 7])
        if not (1 <= level <= 100):
            return None
        charging = raw[at + 6] > 0
        return level, charging


class MchoseA5LegacyDriver(BaseDeviceDriver):
    """迈从初代 A5 Pro / A5 Pro Max / AX5 (0x2023) 鼠标驱动。"""

    slot = DeviceSlot.MOUSE
    driver_id = "mchose_a5_legacy"
    display_name = "A5 Pro"
    priority = 18
    is_virtual = False

    def __init__(
        self,
        open_device_fn: Optional[Callable[[Any], Any]] = None,
        get_caps_fn: Optional[Callable[[Any], Optional[hid_transport.HidDeviceCaps]]] = None,
    ) -> None:
        self._open_device_fn = open_device_fn or hid_transport.open_device
        self._get_caps_fn = get_caps_fn or hid_transport.get_device_caps

    @classmethod
    def is_supported_pid(cls, vid: int, pid: int) -> bool:
        return vid == VID_A5_LEGACY and (pid in A5_LEGACY_WIRED_PIDS or pid in A5_LEGACY_WIRELESS_PIDS)

    @classmethod
    def _resolve_model_name(cls, target: Dict[str, Any], pid: int) -> str:
        if pid in A5_LEGACY_WIRED_PIDS:
            return A5_LEGACY_WIRED_PIDS[pid]
        prod = str(target.get("product_string") or "")
        for tag in ("A5 Pro Max", "A5 Pro", "AX5 Pro Max", "AX5 Pro"):
            if tag.lower() in prod.lower():
                return tag
        return A5_LEGACY_WIRELESS_PIDS.get(pid) or cls.display_name

    @classmethod
    def find_targets(cls, hid_devices: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """筛选 0x2023 设备，有线优先于无线，0xFFFF 集合优先并严格跳过 0xFFA0。"""
        wired_pref: List[Dict[str, Any]] = []
        wired_other: List[Dict[str, Any]] = []
        wireless_pref: List[Dict[str, Any]] = []
        wireless_other: List[Dict[str, Any]] = []

        for info in hid_devices:
            vid = int(info.get("vendor_id") or 0)
            pid = int(info.get("product_id") or 0)
            if not cls.is_supported_pid(vid, pid):
                continue
            up = int(info.get("usage_page") or 0)
            if up == USAGE_PAGE_A5_FORBIDDEN:
                continue

            is_wired = pid in A5_LEGACY_WIRED_PIDS
            if up == USAGE_PAGE_A5_PREFERRED:
                (wired_pref if is_wired else wireless_pref).append(info)
            elif up >= 0xFF00 or up == 0:
                (wired_other if is_wired else wireless_other).append(info)

        return wired_pref + wired_other + wireless_pref + wireless_other

    @staticmethod
    def _caps_ok(caps: Optional[hid_transport.HidDeviceCaps]) -> bool:
        if caps is None:
            return True
        if caps.usage_page == USAGE_PAGE_A5_FORBIDDEN:
            return False
        return caps.feature_len >= A5_REPORT_LEN

    def _query_target(self, target: Dict[str, Any]) -> Optional[Tuple[int, bool]]:
        path = target["path"]
        caps = self._get_caps_fn(path)
        if not self._caps_ok(caps):
            return None

        req = MchoseA5LegacyProtocol.build_battery_request()
        with self._open_device_fn(path) as dev:
            hid_transport.send_feature_report(dev, req)
            time.sleep(0.034)
            for attempt in range(8):
                if attempt == 3:
                    hid_transport.send_feature_report(dev, req)
                    time.sleep(0.034)
                try:
                    resp = hid_transport.get_feature_report(dev, A5_REPORT_ID, A5_REPORT_LEN)
                except Exception:
                    resp = []
                parsed = MchoseA5LegacyProtocol.parse_battery_response(resp)
                if parsed is not None:
                    return parsed
                time.sleep(0.03)
        return None

    def poll(self, hid_devices: List[Dict[str, Any]]) -> Optional[DevicePollResult]:
        targets = self.find_targets(hid_devices)
        if not targets:
            return None

        first = targets[0]
        first_pid = int(first.get("product_id") or 0)
        fallback_mode = (
            ConnectionMode.USB if first_pid in A5_LEGACY_WIRED_PIDS else ConnectionMode.WIRELESS_24G
        )
        fallback_name = self._resolve_model_name(first, first_pid)

        saw_accessible = False
        for target in targets:
            if not self._caps_ok(self._get_caps_fn(target["path"])):
                continue
            saw_accessible = True
            pid = int(target.get("product_id") or 0)
            mode = ConnectionMode.USB if pid in A5_LEGACY_WIRED_PIDS else ConnectionMode.WIRELESS_24G
            model_name = self._resolve_model_name(target, pid)

            try:
                parsed = self._query_target(target)
            except Exception:
                continue
            if parsed is None:
                continue

            raw_percent, charging = parsed
            return DevicePollResult(
                hardware_connected=True,
                reading=BatteryReading(
                    percent=quantize_battery_step10(raw_percent),
                    charging=charging,
                    online=True,
                    model_name=model_name,
                    connection_mode=mode,
                    vid=VID_A5_LEGACY,
                    pid=pid,
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
                model_name=fallback_name,
                connection_mode=fallback_mode,
                vid=VID_A5_LEGACY,
                pid=first_pid,
            ),
        )
