# -*- coding: utf-8 -*-
"""迈从经典 (0x11/0x06) 与新一代 (0x4D/0x0900) 鼠标家族 HID 协议与驱动。

完整覆盖：
- 经典协议 (0x11/0x06 Feature Report 反码)：
  A7 / A7 Pro / A7 Ultra / A7X Ultra / A7 Ultra(RE) / A7 V2 Pro / A7 V2 Pro+ /
  A7 V2 Ultra / A7 V2 Ultra+ / M7 / M7 Pro / M7 Ultra / L7 / L7 Pro / L7 Pro+ /
  L7 Ultra / L7 Ultra+ / AX5 V2 / A5 Ultra / A5 V2 Ultra / K7 Ultra / G3 Ultra
- 新一代协议 (0x4D/0x0900 XOR 校验帧)：
  G3 V3 / A7 V3 / A7 V3 Pro / A7 V3 Pro+ / A7 V3 Ultra / A7 V3 Ultra+ / A7 V3-X /
  A7 V3 Magnetic / A5 V3 Pro / A5 V3 Ultra+ / K7 V2 / K5 / R7 Ultra / V7
"""

import time
from typing import Any, Callable, Collection, Dict, List, Optional, Sequence, Tuple

from core.interfaces import BaseDeviceDriver
from core.models import (
    BatteryReading,
    ConnectionMode,
    DevicePollResult,
    DeviceSlot,
    quantize_battery_step10,
)

from . import hid_transport


VALID_MCHOSE_VIDS = (0x3837, 0x5253)
VENDOR_ID_PRIMARY = 0x3837
VENDOR_ID_COMPX_A5 = 0x41E4
VENDOR_ID_REALTEK_A5 = 0x0BDA
VALID_CLASSIC_RESPONSE_VIDS = (
    *VALID_MCHOSE_VIDS,
    VENDOR_ID_COMPX_A5,
    VENDOR_ID_REALTEK_A5,
)
MOUSE_CONFIG_USAGE_PAGES = (0xFF01, 0xFF0B)

MCHOSE_CLASSIC_DONGLE_PIDS = frozenset(
    {0x1000, 0x1001, 0x1009, 0x100A, 0x100B, 0x100C, 0x1020, 0x1021}
)
COMPX_A5_PIDS = frozenset({0x1101, 0x1102})
REALTEK_A5_PIDS = frozenset({0x2B67, 0x4762})
CLASSIC_DONGLE_PIDS = MCHOSE_CLASSIC_DONGLE_PIDS | frozenset({0x1102, 0x4762})
MODERN_DONGLE_PIDS = frozenset({0x1014, 0x1016, 0x1018, 0x101C, 0x101D})


class MchoseCatalog:
    """迈从经典与新一代协议官方内部/直连 PID 型号字典。"""

    CLASSIC_MODELS: Dict[int, str] = {
        0x0010: "A7 Pro",
        0x0011: "A7 Ultra",
        0x0020: "M7",
        0x0030: "M7 Pro",
        0x0031: "M7 Ultra",
        0x0070: "A7",
        0x00A1: "AX5 V2",
        0x00B0: "L7 Pro",
        0x00B1: "L7 Ultra",
        0x00C0: "L7",
        0x1101: "A5 V2 Ultra",
        0x2B67: "A5 Ultra",
        0x4010: "AX5 V2",
        0x4011: "A7X Ultra",
        0x4015: "L7 Pro+",
        0x4016: "L7 Ultra+",
        0x4018: "A7 V2 Pro",
        0x4019: "A7 V2 Ultra",
        0x4021: "A7 V2 Ultra+",
        0x4022: "A5 V2 Ultra",
        0x4023: "A7 V2 Pro+",
        0x4110: "A7 Ultra(RE)",
        0x4150: "K7 Ultra",
    }

    CLASSIC_DONGLE_NAMES: Dict[int, str] = {
        0x1102: "A5 V2 Ultra",
        0x4762: "A5 Ultra / G3 Ultra",
    }

    MODERN_MODELS: Dict[int, str] = {
        0x4026: "A5 V3 Ultra+",
        0x4027: "K7 V2 Pro+",
        0x4028: "K7 V2 Ultra+",
        0x4029: "G3 V3",
        0x402A: "V7",
        0x4030: "A7 V3",
        0x4031: "A7 V3 Pro",
        0x4032: "A7 V3 Pro+",
        0x4033: "A7 V3 Ultra+",
        0x4034: "A5 V3 Ultra+(3955)",
        0x4035: "A5 V3 Pro",
        0x4036: "R7 Ultra",
        0x4037: "K5 Pro",
        0x4038: "K5 Ultra",
        0x403A: "A7 V3-X",
        0x403B: "K7 V2 Pro",
        0x403C: "K7 V2 Ultra",
        0x403D: "A7 V3 Ultra",
        0x403E: "V7 Pro",
        0x403F: "A7 V3 Magnetic",
        0x4040: "K5 V2",
    }

    @classmethod
    def is_classic_device(cls, vid: int, pid: int) -> bool:
        if vid in VALID_MCHOSE_VIDS:
            return pid in cls.CLASSIC_MODELS or pid in MCHOSE_CLASSIC_DONGLE_PIDS
        if vid == VENDOR_ID_COMPX_A5:
            return pid in COMPX_A5_PIDS
        if vid == VENDOR_ID_REALTEK_A5:
            return pid in REALTEK_A5_PIDS
        return False

    @classmethod
    def is_modern_device(cls, vid: int, pid: int) -> bool:
        return vid == VENDOR_ID_PRIMARY and (pid in cls.MODERN_MODELS or pid in MODERN_DONGLE_PIDS)

    @classmethod
    def resolve_name(cls, pid: int, fallback: str = "") -> str:
        cleaned = (fallback or "").strip()
        if pid == 0x4762:
            lower = cleaned.lower()
            if "g3" in lower:
                return "G3 Ultra"
            if "a5" in lower:
                return "A5 Ultra"
        if pid in cls.CLASSIC_MODELS:
            return cls.CLASSIC_MODELS[pid]
        if pid in cls.MODERN_MODELS:
            return cls.MODERN_MODELS[pid]
        if pid in cls.CLASSIC_DONGLE_NAMES:
            return cls.CLASSIC_DONGLE_NAMES[pid]
        if cleaned:
            return cleaned
        return f"MCHOSE {pid:04X}"


def _decode_connection_mode(code: int) -> ConnectionMode:
    if code == 0:
        return ConnectionMode.USB
    if code == 1:
        return ConnectionMode.WIRELESS_24G
    if code == 2:
        return ConnectionMode.BLUETOOTH
    return ConnectionMode.UNKNOWN


def _is_unready_battery_frame(
    parsed: Tuple[int, str, ConnectionMode, bool, int, int],
) -> bool:
    """判断已解析响应是否属于未就绪电量占位帧（已连接但电量为 0 且非充满哨兵）。"""
    _pid, _name, _mode, linked, raw_bat, charge_code = parsed
    return linked and raw_bat == 0 and charge_code != 2


def _build_family_poll_result(
    vid: int,
    model_pid: int,
    model_name: str,
    mode: ConnectionMode,
    linked: bool,
    raw_bat: int,
    charge_code: int,
) -> DevicePollResult:
    """将经典或新一代解析结果统一转换为整十档位与标准读取结构。"""
    if not linked:
        return DevicePollResult(
            hardware_connected=True,
            reading=BatteryReading(
                percent=None,
                charging=False,
                online=False,
                model_name=model_name,
                connection_mode=mode,
                vid=vid,
                pid=model_pid,
                raw_charge_code=charge_code,
            ),
        )

    if charge_code == 2 and raw_bat == 0:
        shown_raw = 100
    elif 1 <= raw_bat <= 100:
        shown_raw = raw_bat
    else:
        return DevicePollResult(
            hardware_connected=True,
            reading=BatteryReading(
                percent=None,
                charging=(charge_code == 1),
                online=True,
                model_name=model_name,
                connection_mode=mode,
                vid=vid,
                pid=model_pid,
                raw_charge_code=charge_code,
            ),
        )

    return DevicePollResult(
        hardware_connected=True,
        reading=BatteryReading(
            percent=quantize_battery_step10(shown_raw),
            charging=(charge_code == 1),
            online=True,
            model_name=model_name,
            connection_mode=mode,
            vid=vid,
            pid=model_pid,
            raw_charge_code=charge_code,
            raw_percent=shown_raw,
        ),
    )


def _poll_family_targets(
    hid_devices: List[Dict[str, Any]],
    find_targets_fn: Callable[[List[Dict[str, Any]]], List[Dict[str, Any]]],
    caps_ok_fn: Callable[[Optional[hid_transport.HidDeviceCaps]], bool],
    get_caps_fn: Callable[[Any], Optional[hid_transport.HidDeviceCaps]],
    query_fn: Callable[[Dict[str, Any]], Optional[Tuple[int, str, ConnectionMode, bool, int, int]]],
    direct_pids: Collection[int],
) -> Optional[DevicePollResult]:
    """统一遍历多接口候选设备，执行首命中查询并构造在线/离线回包。"""
    targets = find_targets_fn(hid_devices)
    if not targets:
        return None

    first = targets[0]
    usb_vid = int(first.get("vendor_id") or 0)
    usb_pid = int(first.get("product_id") or 0)
    fallback_mode = ConnectionMode.USB if usb_pid in direct_pids else ConnectionMode.WIRELESS_24G
    fallback_name = MchoseCatalog.resolve_name(usb_pid, str(first.get("product_string") or ""))

    saw_accessible = False
    for target in targets:
        if not caps_ok_fn(get_caps_fn(target["path"])):
            continue
        saw_accessible = True
        t_vid = int(target.get("vendor_id") or 0)
        try:
            parsed = query_fn(target)
        except Exception:
            continue
        if parsed is None:
            continue

        model_pid, model_name, mode, linked, raw_bat, charge_code = parsed
        return _build_family_poll_result(
            vid=t_vid,
            model_pid=model_pid,
            model_name=model_name,
            mode=mode,
            linked=linked,
            raw_bat=raw_bat,
            charge_code=charge_code,
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
            vid=usb_vid,
            pid=usb_pid,
        ),
    )


class MchoseClassicProtocol:
    """迈从经典 0x11/0x06 Feature Report 反码编解码器（纯函数）。"""

    REPORT_ID = 0x11
    ECHO_BYTE = 0xF9  # 0x06 ^ 0xFF
    DEFAULT_LENGTH = 21

    @classmethod
    def build_feature_request(cls, length: int = DEFAULT_LENGTH) -> bytes:
        """构造经典协议查询 Feature Report（首字节 0x11, 0xF9，后续全部填充 0xFF）。"""
        size = max(cls.DEFAULT_LENGTH, int(length))
        buf = bytearray([0xFF] * size)
        buf[0] = cls.REPORT_ID
        buf[1] = cls.ECHO_BYTE
        return bytes(buf)

    @classmethod
    def parse_feature_response(
        cls,
        raw: Sequence[int],
        fallback_name: str = "",
    ) -> Optional[Tuple[int, str, ConnectionMode, bool, int, int]]:
        """解析经典协议 Feature Report，返回 (model_pid, model_name, mode, linked, raw_bat, charge_code)。"""
        if not raw or len(raw) < 13 or raw[0] != cls.REPORT_ID or raw[1] != cls.ECHO_BYTE:
            return None

        dec = [b ^ 0xFF for b in raw[2:]]
        if len(dec) < 11:
            return None

        vid = dec[0] | (dec[1] << 8)
        pid = dec[2] | (dec[3] << 8)
        if vid not in VALID_CLASSIC_RESPONSE_VIDS or pid == 0 or pid == 0xFFFF:
            return None

        flags = dec[8]
        conn_mode = _decode_connection_mode(flags & 0x07)
        linked = (flags & 0x08) != 0
        raw_bat = dec[9]
        charge_code = dec[10]

        if raw_bat > 100:
            return None
        # 兼容极少数旧版 A7 接收器固件在 dec[11] (raw[13]) 返回有效电量的情况
        if raw_bat == 0 and linked and len(dec) > 11 and 1 <= dec[11] <= 100:
            raw_bat = dec[11]

        model_name = MchoseCatalog.resolve_name(pid, fallback_name)
        return pid, model_name, conn_mode, linked, int(raw_bat), int(charge_code)


class MchoseModernProtocol:
    """迈从新一代 0x4D/0x0900 校验帧编解码器（纯函数）。"""

    REPORT_ID = 0x4D
    CMD_STATUS_LO = 0x00
    CMD_STATUS_HI = 0x09
    MIN_REQ_LEN = 64
    MAX_LEN = 512

    @classmethod
    def build_status_request(cls, length: int = MIN_REQ_LEN) -> bytes:
        """构造新一代状态查询 Output Report（长度 64..512，包含首字节 0x4D 与 XOR 校验）。"""
        size = max(cls.MIN_REQ_LEN, min(cls.MAX_LEN, int(length)))
        buf = bytearray(size)
        buf[0] = cls.REPORT_ID
        buf[1] = 0x01
        buf[2] = 0x01
        buf[3] = 0x00
        buf[4] = cls.CMD_STATUS_LO
        buf[5] = cls.CMD_STATUS_HI
        buf[6] = 0x00
        buf[7] = 0x00
        buf[8] = 0x08
        return bytes(buf)

    @classmethod
    def is_busy_response(cls, raw: Sequence[int]) -> bool:
        """判断是否为设备忙指示帧（raw[8] == 0xFF）。"""
        return (
            len(raw) > 8
            and raw[0] == cls.REPORT_ID
            and raw[4] == cls.CMD_STATUS_LO
            and raw[5] == cls.CMD_STATUS_HI
            and raw[8] == 0xFF
        )

    @classmethod
    def parse_status_response(
        cls,
        raw: Sequence[int],
        usb_pid: int,
        fallback_name: str = "",
    ) -> Optional[Tuple[int, str, ConnectionMode, bool, int, int]]:
        """校验并解析 0x4D/0x0900 响应帧，返回 (model_pid, model_name, mode, linked, raw_bat, charge_code)。"""
        if (
            not raw
            or len(raw) < 22
            or raw[0] != cls.REPORT_ID
            or raw[1] != 0x01
            or raw[4] != cls.CMD_STATUS_LO
            or raw[5] != cls.CMD_STATUS_HI
            or raw[6] != 0x00
            or raw[7] != 0x00
        ):
            return None

        payload_len = raw[3]
        if payload_len < 14 or payload_len > len(raw) - 8:
            return None

        if (raw[2] & 0x01) != 0:
            if len(raw) <= 8 + payload_len:
                return None
            xor_sum = 0
            for i in range(2, 8 + payload_len):
                xor_sum ^= raw[i]
            if xor_sum != raw[8 + payload_len]:
                return None

        vid = raw[8] | (raw[9] << 8)
        pid = raw[10] | (raw[11] << 8)
        raw_bat = raw[20]
        if vid not in VALID_MCHOSE_VIDS or pid not in MchoseCatalog.MODERN_MODELS or raw_bat > 100:
            return None

        conn_code = raw[18]
        charge_code = raw[19]
        linked = (conn_code != 0) or (raw_bat > 0) or (charge_code in (1, 2))
        mode = ConnectionMode.USB if usb_pid in MchoseCatalog.MODERN_MODELS else ConnectionMode.WIRELESS_24G
        model_name = MchoseCatalog.resolve_name(pid, fallback_name)
        return pid, model_name, mode, linked, int(raw_bat), int(charge_code)


class MchoseClassicDriver(BaseDeviceDriver):
    """迈从经典协议（0x11/0x06 Feature Report）小鼠全系驱动（A7 / A7 V2 / M7 / L7 等）。"""

    slot = DeviceSlot.MOUSE
    driver_id = "mchose_classic"
    display_name = "A7"
    priority = 14
    is_virtual = False

    def __init__(
        self,
        open_device_fn: Optional[Callable[[Any], Any]] = None,
        get_caps_fn: Optional[Callable[[Any], Optional[hid_transport.HidDeviceCaps]]] = None,
    ) -> None:
        self._open_device_fn = open_device_fn or hid_transport.open_device
        self._get_caps_fn = get_caps_fn or hid_transport.get_device_caps

    @classmethod
    def find_targets(cls, hid_devices: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """筛选经典协议 0xFF01/0xFF0B 接口，按有线直连优先于共享接收器排序。"""
        wired: List[Dict[str, Any]] = []
        wireless: List[Dict[str, Any]] = []
        for info in hid_devices:
            vid = int(info.get("vendor_id") or 0)
            pid = int(info.get("product_id") or 0)
            if not MchoseCatalog.is_classic_device(vid, pid):
                continue
            if info.get("usage_page") not in MOUSE_CONFIG_USAGE_PAGES:
                continue
            if pid in MchoseCatalog.CLASSIC_MODELS:
                wired.append(info)
            else:
                wireless.append(info)
        return wired + wireless

    @staticmethod
    def _caps_match_classic(caps: Optional[hid_transport.HidDeviceCaps]) -> bool:
        if caps is None:
            return True
        return caps.feature_len >= MchoseClassicProtocol.DEFAULT_LENGTH

    def _query_target(self, target: Dict[str, Any]) -> Optional[Tuple[int, str, ConnectionMode, bool, int, int]]:
        path = target["path"]
        caps = self._get_caps_fn(path)
        if not self._caps_match_classic(caps):
            return None

        probe_lengths = [caps.feature_len] if caps is not None else [21, 65]
        fallback_name = str(target.get("product_string") or "")

        unready_fallback: Optional[Tuple[int, str, ConnectionMode, bool, int, int]] = None
        with self._open_device_fn(path) as dev:
            for req_len in probe_lengths:
                req = MchoseClassicProtocol.build_feature_request(req_len)
                for _attempt in range(3):
                    try:
                        hid_transport.send_feature_report(dev, req)
                        time.sleep(0.10)
                        raw = hid_transport.get_feature_report(dev, MchoseClassicProtocol.REPORT_ID, req_len)
                    except Exception:
                        time.sleep(0.08)
                        continue
                    parsed = MchoseClassicProtocol.parse_feature_response(raw, fallback_name=fallback_name)
                    if parsed is not None:
                        if not _is_unready_battery_frame(parsed):
                            return parsed
                        unready_fallback = parsed
                    time.sleep(0.08)
                if unready_fallback is not None:
                    return unready_fallback
        return None

    def poll(self, hid_devices: List[Dict[str, Any]]) -> Optional[DevicePollResult]:
        return _poll_family_targets(
            hid_devices=hid_devices,
            find_targets_fn=self.find_targets,
            caps_ok_fn=self._caps_match_classic,
            get_caps_fn=self._get_caps_fn,
            query_fn=self._query_target,
            direct_pids=MchoseCatalog.CLASSIC_MODELS,
        )


class MchoseModernDriver(BaseDeviceDriver):
    """迈从新一代协议（0x4D/0x0900）鼠标全系驱动（G3 V3 / A7 V3 / K7 V2 等）。"""

    slot = DeviceSlot.MOUSE
    driver_id = "mchose_modern"
    display_name = "A7 V3"
    priority = 16
    is_virtual = False

    def __init__(
        self,
        open_device_fn: Optional[Callable[[Any], Any]] = None,
        get_caps_fn: Optional[Callable[[Any], Optional[hid_transport.HidDeviceCaps]]] = None,
    ) -> None:
        self._open_device_fn = open_device_fn or hid_transport.open_device
        self._get_caps_fn = get_caps_fn or hid_transport.get_device_caps

    @classmethod
    def find_targets(cls, hid_devices: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """筛选新一代协议 0xFF01/0xFF0B 接口，按有线直连优先于共享接收器排序。"""
        wired: List[Dict[str, Any]] = []
        wireless: List[Dict[str, Any]] = []
        for info in hid_devices:
            vid = int(info.get("vendor_id") or 0)
            pid = int(info.get("product_id") or 0)
            if not MchoseCatalog.is_modern_device(vid, pid):
                continue
            if info.get("usage_page") not in MOUSE_CONFIG_USAGE_PAGES:
                continue
            if pid in MchoseCatalog.MODERN_MODELS:
                wired.append(info)
            else:
                wireless.append(info)
        return wired + wireless

    @staticmethod
    def _caps_match_modern(caps: Optional[hid_transport.HidDeviceCaps]) -> bool:
        if caps is None:
            return True
        return 23 <= caps.input_len <= 512 and 64 <= caps.output_len <= 512

    def _query_target(self, target: Dict[str, Any]) -> Optional[Tuple[int, str, ConnectionMode, bool, int, int]]:
        path = target["path"]
        usb_pid = int(target.get("product_id") or 0)
        fallback_name = str(target.get("product_string") or "")
        caps = self._get_caps_fn(path)
        if not self._caps_match_modern(caps):
            return None

        in_len = caps.input_len if caps is not None else 64
        out_len = caps.output_len if caps is not None else 64
        req = MchoseModernProtocol.build_status_request(out_len)

        unready_fallback: Optional[Tuple[int, str, ConnectionMode, bool, int, int]] = None
        with self._open_device_fn(path) as dev:
            hid_transport.flush_input(dev, read_size=in_len)
            hid_transport.write_packet(dev, req)
            deadline = time.monotonic() + 1.5
            while True:
                remaining_ms = int((deadline - time.monotonic()) * 1000)
                if remaining_ms <= 0:
                    return unready_fallback
                raw = hid_transport.read_with_timeout(dev, in_len, min(250, remaining_ms))
                if not raw:
                    return unready_fallback
                if MchoseModernProtocol.is_busy_response(raw):
                    time.sleep(0.03)
                    hid_transport.write_packet(dev, req)
                    continue
                parsed = MchoseModernProtocol.parse_status_response(raw, usb_pid=usb_pid, fallback_name=fallback_name)
                if parsed is not None:
                    if not _is_unready_battery_frame(parsed):
                        return parsed
                    unready_fallback = parsed
                    time.sleep(0.03)
                    hid_transport.write_packet(dev, req)

    def poll(self, hid_devices: List[Dict[str, Any]]) -> Optional[DevicePollResult]:
        return _poll_family_targets(
            hid_devices=hid_devices,
            find_targets_fn=self.find_targets,
            caps_ok_fn=self._caps_match_modern,
            get_caps_fn=self._get_caps_fn,
            query_fn=self._query_target,
            direct_pids=MchoseCatalog.MODERN_MODELS,
        )
