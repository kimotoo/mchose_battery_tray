# -*- coding: utf-8 -*-
"""WOBKEY Crush 80 三模键盘（蓝牙 BLE / USB 有线 / 2.4G 接收器）电量驱动实现。

支持三种连接方式：
1. USB 有线（VID=0x320F, PID=0x5055）：
   通过 EVision 厂商接口 0xFF1C:0x0092 发送只读 Report 0x04 / Cmd 0x1A 查询电量与充电状态；
   若当前固件版本未在有线通道返回非零电量，仍报告 USB 在线状态并保留历史缓存。
2. 蓝牙 BLE（Windows BTHLEDEVICE, VID&02245A_PID&8276 / 名称含 Crush 80）：
   参考 Chargeview，通过 Windows cfgmgr32.dll 原生读取电池服务 {0000180F-...} 的
   DEVPKEY_Device_BatteryLevel 与 DEVPKEY_Device_IsConnected（无需启动 PowerShell）。
3. 2.4G 无线接收器（VID=0x320F, PID=0x5088）：
   通过 0xFF1C:0x0092 发送 Report 0x04 / Cmd 0x1A 查询电量，并以 Cmd 0xAA 检测链路在线状态；
   若原厂固件 2.4G 通道暂未返回非零电量，将如实标记在线/休眠并保留此前有效缓存（如蓝牙读数）。
"""

import ctypes
from ctypes import wintypes
from dataclasses import dataclass
import re
import sys
import time
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple
import uuid

from core.interfaces import BaseDeviceDriver
from core.models import (
    BatteryReading,
    ConnectionMode,
    DevicePollResult,
    DeviceSlot,
    quantize_battery_step10,
)

from . import hid_transport


VID_CRUSH80_USB = 0x320F
PID_CRUSH80_WIRED = 0x5055
PID_CRUSH80_WIRELESS = 0x5088

USAGE_PAGE_EVISION_VENDOR = 0xFF1C
USAGE_EVISION_VENDOR = 0x0092
USAGE_PAGE_VIA = 0xFF60
USAGE_VIA = 0x0061

EVISION_REPORT_ID = 0x04
EVISION_CMD_BATTERY = 0x1A
EVISION_CMD_ONLINE = 0xAA
EVISION_MIN_FRAME_LEN = 32
EVISION_DEFAULT_FRAME_LEN = 64

# 排除明确属于其他品牌的 0x320F 同 VID/PID 设备
_EXCLUDED_PRODUCT_KEYWORDS = (
    "ATK ",
    "V75X",
    "VGN ",
    "VXE ",
    "INKY",
    "AULA ",
)

# Windows Bluetooth LE 电池服务与 Crush 80 BLE 设备匹配规则
_BLE_BATTERY_SERVICE_RE = re.compile(
    r"^BTHLEDEVICE\\\{0000180F-0000-1000-8000-00805F9B34FB\}_",
    re.I,
)
_BLE_CRUSH80_VID_PID_RE = re.compile(
    r"_DEV_VID&(?:\d{2})?245A_PID&8276_",
    re.I,
)


class _CFGGUID(ctypes.Structure):
    _fields_ = [("bytes", ctypes.c_ubyte * 16)]

    @classmethod
    def parse(cls, value: str) -> "_CFGGUID":
        return cls.from_buffer_copy(uuid.UUID(value).bytes_le)


class _DEVPROPKEY(ctypes.Structure):
    _fields_ = [("fmtid", _CFGGUID), ("pid", wintypes.DWORD)]


_DEVPKEY_BATTERY_LEVEL = _DEVPROPKEY(_CFGGUID.parse("104ea319-6ee2-4701-bd47-8ddbf425bbe5"), 2)
_DEVPKEY_IS_CONNECTED = _DEVPROPKEY(_CFGGUID.parse("83da6326-97a6-4088-9453-a1923f573b29"), 15)
_DEVPKEY_FRIENDLY_NAME = _DEVPROPKEY(_CFGGUID.parse("a45c254e-df1c-4efd-8020-67d146a850e0"), 14)

_CM_GETIDLIST_FILTER_PRESENT = 0x00000100
_CM_GETIDLIST_FILTER_ENUMERATOR = 0x00000001
_CR_SUCCESS = 0x00000000
_CR_BUFFER_SMALL = 0x0000001A
_DEVPROP_TYPE_BYTE = 3
_DEVPROP_TYPE_BOOLEAN = 17


@dataclass(frozen=True)
class BleBatteryReading:
    """Windows 蓝牙 LE 设备电量与在线状态快照。"""

    percent: Optional[int]
    connected: bool
    model_name: str = "Crush 80"


class Crush80Protocol:
    """Crush 80 EVision (0xFF1C:0x0092, Report 0x04) 无状态编解码器。"""

    @staticmethod
    def build_command_request(cmd: int, sequence: int = 1, length: int = EVISION_DEFAULT_FRAME_LEN) -> bytes:
        """构造 Report ID 0x04 只读查询帧（按接口 OutputReportByteLength 对齐，至少 32 字节）。"""
        size = max(EVISION_MIN_FRAME_LEN, int(length))
        seq = int(sequence) & 0xFFFF
        frame = bytearray(size)
        frame[0] = EVISION_REPORT_ID
        frame[1] = seq & 0xFF
        frame[2] = (seq >> 8) & 0xFF
        frame[3] = int(cmd) & 0xFF
        return bytes(frame)

    @classmethod
    def build_battery_request(cls, sequence: int = 1, length: int = EVISION_DEFAULT_FRAME_LEN) -> bytes:
        """构造 0x1A 电量查询帧。"""
        return cls.build_command_request(EVISION_CMD_BATTERY, sequence=sequence, length=length)

    @classmethod
    def build_online_request(cls, sequence: int = 1, length: int = EVISION_DEFAULT_FRAME_LEN) -> bytes:
        """构造 0xAA 在线状态查询帧。"""
        return cls.build_command_request(EVISION_CMD_ONLINE, sequence=sequence, length=length)

    @staticmethod
    def _matches_reply_header(raw: Sequence[int], cmd: int, sequence: int) -> bool:
        if not raw or len(raw) < 10:
            return False
        seq = int(sequence) & 0xFFFF
        if int(raw[0]) != EVISION_REPORT_ID:
            return False
        if int(raw[1]) != (seq & 0xFF) or int(raw[2]) != ((seq >> 8) & 0xFF):
            return False
        if int(raw[3]) != (int(cmd) & 0xFF):
            return False
        if int(raw[5]) != 0 or int(raw[6]) != 0:
            return False
        return True

    @classmethod
    def parse_battery_response(cls, raw: Sequence[int], sequence: int) -> Optional[Tuple[int, bool]]:
        """解析 0x1A 电量应答帧，返回 (raw_percent, charging) 或 None。"""
        if not cls._matches_reply_header(raw, EVISION_CMD_BATTERY, sequence):
            return None
        level = int(raw[8]) & 0xFF
        state = int(raw[9]) & 0xFF
        if level > 100 or state not in (0, 1, 2):
            return None
        return level, state in (1, 2)

    @classmethod
    def parse_online_response(cls, raw: Sequence[int], sequence: int) -> Optional[bool]:
        """解析 0xAA 在线应答帧：byte[8]==0xFF 表示在线，0x00 表示离线/休眠。"""
        if not cls._matches_reply_header(raw, EVISION_CMD_ONLINE, sequence):
            return None
        state = int(raw[8]) & 0xFF
        if state == 0xFF:
            return True
        if state == 0x00:
            return False
        return None


def _read_devnode_byte(cfg: Any, devnode: int, key: _DEVPROPKEY, expected_type: int) -> Optional[int]:
    prop_type = wintypes.ULONG()
    size = wintypes.ULONG(1)
    value = ctypes.c_ubyte()
    rc = cfg.CM_Get_DevNode_PropertyW(
        devnode,
        ctypes.byref(key),
        ctypes.byref(prop_type),
        ctypes.byref(value),
        ctypes.byref(size),
        0,
    )
    if rc == _CR_SUCCESS and prop_type.value == expected_type and size.value == 1:
        return int(value.value)
    return None


def _read_devnode_string(cfg: Any, devnode: int, key: _DEVPROPKEY) -> str:
    prop_type = wintypes.ULONG()
    size = wintypes.ULONG(256)
    buf = ctypes.create_unicode_buffer(128)
    rc = cfg.CM_Get_DevNode_PropertyW(
        devnode,
        ctypes.byref(key),
        ctypes.byref(prop_type),
        buf,
        ctypes.byref(size),
        0,
    )
    if rc == _CR_SUCCESS and buf.value:
        return buf.value.strip()
    return ""


def read_crush80_ble_battery() -> Optional[BleBatteryReading]:
    """通过 Windows cfgmgr32.dll 原生读取 Crush 80 蓝牙 LE 连接态与电量百分比。"""
    if sys.platform != "win32":
        return None
    try:
        cfg = ctypes.WinDLL("cfgmgr32", use_last_error=False)
        flags = _CM_GETIDLIST_FILTER_PRESENT | _CM_GETIDLIST_FILTER_ENUMERATOR
        raw_ids: Optional[str] = None
        for _ in range(2):
            size = wintypes.ULONG()
            if cfg.CM_Get_Device_ID_List_SizeW(ctypes.byref(size), "BTHLEDEVICE", flags) != _CR_SUCCESS:
                return None
            if size.value <= 1:
                return None
            buf = ctypes.create_unicode_buffer(size.value)
            rc = cfg.CM_Get_Device_ID_ListW("BTHLEDEVICE", buf, size.value, flags)
            if rc == _CR_SUCCESS:
                raw_ids = buf[:]
                break
            if rc != _CR_BUFFER_SMALL:
                return None
        if not raw_ids:
            return None

        for dev_id in raw_ids.split("\0"):
            if not dev_id or not _BLE_BATTERY_SERVICE_RE.match(dev_id):
                continue
            service_node = wintypes.DWORD()
            parent_node = wintypes.DWORD()
            if cfg.CM_Locate_DevNodeW(ctypes.byref(service_node), dev_id, 0) != _CR_SUCCESS:
                continue
            if cfg.CM_Get_Parent(ctypes.byref(parent_node), service_node.value, 0) != _CR_SUCCESS:
                continue

            friendly_name = _read_devnode_string(cfg, parent_node.value, _DEVPKEY_FRIENDLY_NAME)
            upper_name = friendly_name.upper()
            is_crush80 = bool(_BLE_CRUSH80_VID_PID_RE.search(dev_id)) or (
                "CRUSH 80" in upper_name or "CRUSH80" in upper_name
            )
            if not is_crush80:
                continue

            conn_raw = _read_devnode_byte(cfg, parent_node.value, _DEVPKEY_IS_CONNECTED, _DEVPROP_TYPE_BOOLEAN)
            if conn_raw is None or conn_raw == 0:
                continue

            level = _read_devnode_byte(cfg, parent_node.value, _DEVPKEY_BATTERY_LEVEL, _DEVPROP_TYPE_BYTE)
            valid_level = level if (level is not None and 0 <= level <= 100) else None
            return BleBatteryReading(
                percent=valid_level,
                connected=True,
                model_name=friendly_name or "Crush 80",
            )
    except Exception:
        return None
    return None


class Crush80Driver(BaseDeviceDriver):
    """WOBKEY Crush 80 三模键盘驱动（支持 USB 有线 / 蓝牙 BLE / 2.4G 接收器）。"""

    slot = DeviceSlot.KEYBOARD
    driver_id = "wobkey_crush80"
    display_name = "Crush 80"
    priority = 20
    is_virtual = False

    def __init__(
        self,
        open_device_fn: Optional[Callable[[Any], Any]] = None,
        get_caps_fn: Optional[Callable[[Any], Optional[hid_transport.HidDeviceCaps]]] = None,
        ble_reader_fn: Optional[Callable[[], Optional[BleBatteryReading]]] = None,
    ) -> None:
        self._open_device_fn = open_device_fn or hid_transport.open_device
        self._get_caps_fn = get_caps_fn or hid_transport.get_device_caps
        self._ble_reader_fn = ble_reader_fn or read_crush80_ble_battery
        self._sequence = 0

    @classmethod
    def is_supported_hid_device(cls, vid: int, pid: int, product_string: str = "") -> bool:
        if vid != VID_CRUSH80_USB or pid not in (PID_CRUSH80_WIRED, PID_CRUSH80_WIRELESS):
            return False
        upper = (product_string or "").upper()
        if any(tag in upper for tag in _EXCLUDED_PRODUCT_KEYWORDS):
            return False
        return True

    @classmethod
    def _resolve_model_name(cls, target: Dict[str, Any]) -> str:
        prod = str(target.get("product_string") or "").strip()
        upper = prod.upper()
        if "CRUSH" in upper:
            return prod
        return cls.display_name

    @classmethod
    def find_hid_targets(
        cls, hid_devices: List[Dict[str, Any]]
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """分别筛选 USB 有线与 2.4G 接收器中的 EVision 控制口与 VIA/存在标志接口。"""
        wired_ctrl: List[Dict[str, Any]] = []
        wired_presence: List[Dict[str, Any]] = []
        wireless_ctrl: List[Dict[str, Any]] = []
        wireless_presence: List[Dict[str, Any]] = []

        for info in hid_devices:
            vid = int(info.get("vendor_id") or 0)
            pid = int(info.get("product_id") or 0)
            prod = str(info.get("product_string") or "")
            if not cls.is_supported_hid_device(vid, pid, prod):
                continue

            up = int(info.get("usage_page") or 0)
            u = int(info.get("usage") or 0)
            is_ctrl = up == USAGE_PAGE_EVISION_VENDOR and u == USAGE_EVISION_VENDOR
            is_presence = is_ctrl or (up == USAGE_PAGE_VIA and u == USAGE_VIA)
            if not is_presence:
                continue

            if pid == PID_CRUSH80_WIRED:
                (wired_ctrl if is_ctrl else wired_presence).append(info)
            else:
                (wireless_ctrl if is_ctrl else wireless_presence).append(info)

        return wired_ctrl + wired_presence, wireless_ctrl + wireless_presence

    def _next_sequence(self) -> int:
        self._sequence = (self._sequence % 0xFFFF) + 1
        return self._sequence

    def _transact(
        self,
        dev: Any,
        cmd: int,
        in_len: int,
        out_len: int,
        timeout_sec: float = 0.32,
    ) -> Optional[Tuple[int, Sequence[int]]]:
        seq = self._next_sequence()
        req = Crush80Protocol.build_command_request(cmd, sequence=seq, length=out_len)
        hid_transport.flush_input(dev, read_size=max(EVISION_MIN_FRAME_LEN, in_len), max_reads=6)
        hid_transport.write_packet(dev, req)

        deadline = time.monotonic() + timeout_sec
        while time.monotonic() < deadline:
            raw = hid_transport.read_with_timeout(dev, max(EVISION_MIN_FRAME_LEN, in_len), 80)
            if not raw:
                continue
            if cmd == EVISION_CMD_BATTERY:
                parsed = Crush80Protocol.parse_battery_response(raw, seq)
                if parsed is not None:
                    return seq, raw
            elif cmd == EVISION_CMD_ONLINE:
                online = Crush80Protocol.parse_online_response(raw, seq)
                if online is not None:
                    return seq, raw
        return None

    def _query_evision_target(
        self, target: Dict[str, Any], is_wired: bool
    ) -> Tuple[Optional[int], bool, bool]:
        """查询 EVision 0xFF1C:0x0092 接口，返回 (raw_percent_or_None, charging, online)。"""
        path = target["path"]
        caps = self._get_caps_fn(path)
        if caps is not None and (
            caps.usage_page != USAGE_PAGE_EVISION_VENDOR
            or caps.input_len < EVISION_MIN_FRAME_LEN
            or caps.output_len < EVISION_MIN_FRAME_LEN
        ):
            return None, False, is_wired

        in_len = caps.input_len if caps is not None else EVISION_DEFAULT_FRAME_LEN
        out_len = caps.output_len if caps is not None else EVISION_DEFAULT_FRAME_LEN

        with self._open_device_fn(path) as dev:
            online = is_wired
            if not is_wired:
                # 2.4G 下先用 0xAA 检测链路状态（实机 ~10ms 响应），离线/休眠时跳过 0x1A 等待
                online_res = self._transact(dev, EVISION_CMD_ONLINE, in_len, out_len, timeout_sec=0.25)
                if online_res is None:
                    return None, False, False
                seq, raw = online_res
                online_flag = Crush80Protocol.parse_online_response(raw, seq)
                if online_flag is False:
                    return None, False, False
                online = bool(online_flag)

            bat_res = self._transact(dev, EVISION_CMD_BATTERY, in_len, out_len, timeout_sec=0.35)
            if bat_res is not None:
                seq, raw = bat_res
                parsed = Crush80Protocol.parse_battery_response(raw, seq)
                if parsed is not None:
                    level, charging = parsed
                    if level > 0:
                        return level, charging, True

        return None, False, online

    def _poll_hid_group(
        self, targets: List[Dict[str, Any]], mode: ConnectionMode
    ) -> Optional[DevicePollResult]:
        if not targets:
            return None

        first = targets[0]
        is_wired = mode == ConnectionMode.USB
        vid = int(first.get("vendor_id") or 0)
        pid = int(first.get("product_id") or 0)
        model_name = self._resolve_model_name(first)

        for target in targets:
            if (
                int(target.get("usage_page") or 0) != USAGE_PAGE_EVISION_VENDOR
                or int(target.get("usage") or 0) != USAGE_EVISION_VENDOR
            ):
                continue
            try:
                raw_percent, charging, online = self._query_evision_target(target, is_wired=is_wired)
            except Exception:
                continue

            if raw_percent is not None:
                return DevicePollResult(
                    hardware_connected=True,
                    reading=BatteryReading(
                        percent=quantize_battery_step10(raw_percent),
                        charging=charging,
                        online=True,
                        model_name=self._resolve_model_name(target),
                        connection_mode=mode,
                        vid=int(target.get("vendor_id") or vid),
                        pid=int(target.get("product_id") or pid),
                        raw_charge_code=1 if charging else 0,
                        raw_percent=raw_percent,
                    ),
                )

            return DevicePollResult(
                hardware_connected=True,
                reading=BatteryReading(
                    percent=None,
                    charging=False,
                    online=online,
                    model_name=self._resolve_model_name(target),
                    connection_mode=mode,
                    vid=int(target.get("vendor_id") or vid),
                    pid=int(target.get("product_id") or pid),
                ),
            )

        return DevicePollResult(
            hardware_connected=True,
            reading=BatteryReading(
                percent=None,
                charging=False,
                online=is_wired,
                model_name=model_name,
                connection_mode=mode,
                vid=vid,
                pid=pid,
            ),
        )

    def poll(self, hid_devices: List[Dict[str, Any]]) -> Optional[DevicePollResult]:
        wired_targets, wireless_targets = self.find_hid_targets(hid_devices)

        # 1. USB 有线最优先（可同时充电并保持绝对在线）
        if wired_targets:
            return self._poll_hid_group(wired_targets, ConnectionMode.USB)

        # 2. 蓝牙 BLE 已连接优先于插在电脑上但未切过去的 2.4G 接收器
        try:
            ble_reading = self._ble_reader_fn()
        except Exception:
            ble_reading = None
        if ble_reading is not None and ble_reading.connected:
            pct = (
                quantize_battery_step10(ble_reading.percent)
                if ble_reading.percent is not None
                else None
            )
            return DevicePollResult(
                hardware_connected=True,
                reading=BatteryReading(
                    percent=pct,
                    charging=False,
                    online=True,
                    model_name=ble_reading.model_name or self.display_name,
                    connection_mode=ConnectionMode.BLUETOOTH,
                    vid=0x245A,
                    pid=0x8276,
                    raw_percent=ble_reading.percent,
                ),
            )

        # 3. 2.4G 无线接收器
        if wireless_targets:
            return self._poll_hid_group(wireless_targets, ConnectionMode.WIRELESS_24G)

        return None
