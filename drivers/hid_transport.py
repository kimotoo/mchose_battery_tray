# -*- coding: utf-8 -*-
"""HID 底层通信传输封装，隔离 hidapi 具体绑定细节。"""

from contextlib import contextmanager
import ctypes
from ctypes import wintypes
from dataclasses import dataclass
import sys
import time
from typing import Any, Dict, Iterator, List, Optional, Sequence

try:
    import hid as _hid_module
except ImportError:  # pragma: no cover - 运行环境缺少 hidapi 时延迟提示
    _hid_module = None


def is_hid_available() -> bool:
    """检查当前环境是否已正确安装 hidapi。"""
    return _hid_module is not None


def enumerate_devices(vid: int = 0) -> List[Dict[str, Any]]:
    """枚举系统中的 HID 设备列表。"""
    if _hid_module is None:
        return []
    try:
        return list(_hid_module.enumerate(vid))
    except Exception:
        return []


@contextmanager
def open_device(path: Any) -> Iterator[Any]:
    """按路径打开 HID 设备并在退出上下文时确保关闭句柄。"""
    if _hid_module is None:
        raise RuntimeError("未安装 hidapi 库，无法打开 HID 设备")
    dev = _hid_module.device()
    dev.open_path(path)
    try:
        yield dev
    finally:
        try:
            dev.close()
        except Exception:
            pass


def flush_input(dev: Any, read_size: int = 65, max_reads: int = 16) -> None:
    """清空操作系统 HID 输入缓冲中的残余旧报文。"""
    try:
        dev.set_nonblocking(1)
        for _ in range(max_reads):
            data = dev.read(read_size)
            if not data:
                break
    except Exception:
        pass
    finally:
        try:
            dev.set_nonblocking(0)
        except Exception:
            pass


def read_with_timeout(dev: Any, size: int, timeout_ms: int) -> List[int]:
    """在指定超时内读取 HID 输入报告，兼容不同 hidapi 绑定版本。"""
    timeout_ms = max(1, int(timeout_ms))
    try:
        data = dev.read(size, timeout_ms)
        return list(data or ())
    except TypeError:
        # 兼容极少数不接受 timeout_ms 参数的绑定：退化为非阻塞短轮询
        try:
            dev.set_nonblocking(1)
        except Exception:
            pass
        deadline = time.monotonic() + (timeout_ms / 1000.0)
        while time.monotonic() < deadline:
            data = dev.read(size)
            if data:
                return list(data)
            time.sleep(0.005)
        return []


def write_packet(dev: Any, packet: Sequence[int]) -> int:
    """向 HID 设备发送输出报文。"""
    return int(dev.write(bytes(packet)))


def send_feature_report(dev: Any, packet: Sequence[int]) -> int:
    """向 HID 设备发送 Feature Report。"""
    return int(dev.send_feature_report(bytes(packet)))


def get_feature_report(dev: Any, report_id: int, length: int) -> List[int]:
    """从 HID 设备读取 Feature Report，并归一化返回字节列表。"""
    data = dev.get_feature_report(int(report_id), max(1, int(length)))
    return list(data or ())


@dataclass(frozen=True)
class HidDeviceCaps:
    """Windows HID 集合描述符报表长度能力。"""

    usage_page: int
    usage: int
    input_len: int
    output_len: int
    feature_len: int


class _HIDP_CAPS(ctypes.Structure):
    _fields_ = [
        ("Usage", wintypes.USHORT),
        ("UsagePage", wintypes.USHORT),
        ("InputReportByteLength", wintypes.USHORT),
        ("OutputReportByteLength", wintypes.USHORT),
        ("FeatureReportByteLength", wintypes.USHORT),
        ("Reserved", wintypes.USHORT * 17),
        ("NumberLinkCollectionNodes", wintypes.USHORT),
        ("NumberInputButtonCaps", wintypes.USHORT),
        ("NumberInputValueCaps", wintypes.USHORT),
        ("NumberInputDataIndices", wintypes.USHORT),
        ("NumberOutputButtonCaps", wintypes.USHORT),
        ("NumberOutputValueCaps", wintypes.USHORT),
        ("NumberOutputDataIndices", wintypes.USHORT),
        ("NumberFeatureButtonCaps", wintypes.USHORT),
        ("NumberFeatureValueCaps", wintypes.USHORT),
        ("NumberFeatureDataIndices", wintypes.USHORT),
    ]


def get_device_caps(path: Any) -> Optional[HidDeviceCaps]:
    """以零独占权限读取 Windows HID 设备能力（输入/输出/Feature 报表长度）。"""
    if sys.platform != "win32" or not path:
        return None
    if isinstance(path, bytes):
        path_str = path.decode("utf-8", errors="ignore")
    else:
        path_str = str(path)
    if not path_str.startswith("\\\\"):
        return None

    try:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        hid_dll = ctypes.WinDLL("hid", use_last_error=True)

        k32.CreateFileW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.c_void_p,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        ]
        k32.CreateFileW.restype = wintypes.HANDLE
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        k32.CloseHandle.restype = wintypes.BOOL

        hid_dll.HidD_GetPreparsedData.argtypes = [wintypes.HANDLE, ctypes.POINTER(ctypes.c_void_p)]
        hid_dll.HidD_GetPreparsedData.restype = wintypes.BOOLEAN
        hid_dll.HidD_FreePreparsedData.argtypes = [ctypes.c_void_p]
        hid_dll.HidD_FreePreparsedData.restype = wintypes.BOOLEAN
        hid_dll.HidP_GetCaps.argtypes = [ctypes.c_void_p, ctypes.POINTER(_HIDP_CAPS)]
        hid_dll.HidP_GetCaps.restype = ctypes.c_long

        invalid_handle = ctypes.c_void_p(-1).value
        handle = k32.CreateFileW(path_str, 0, 3, None, 3, 0, None)
        if not handle or handle == invalid_handle:
            return None
        try:
            preparsed = ctypes.c_void_p()
            if not hid_dll.HidD_GetPreparsedData(handle, ctypes.byref(preparsed)) or not preparsed.value:
                return None
            try:
                caps = _HIDP_CAPS()
                status = hid_dll.HidP_GetCaps(preparsed, ctypes.byref(caps))
                if status != 0x00110000:
                    return None
                return HidDeviceCaps(
                    usage_page=int(caps.UsagePage),
                    usage=int(caps.Usage),
                    input_len=int(caps.InputReportByteLength),
                    output_len=int(caps.OutputReportByteLength),
                    feature_len=int(caps.FeatureReportByteLength),
                )
            finally:
                hid_dll.HidD_FreePreparsedData(preparsed)
        finally:
            k32.CloseHandle(handle)
    except Exception:
        return None
