# -*- coding: utf-8 -*-
"""迈从 G3 原始款（SINO WEALTH 鑫联胜 258A:0138）HID 驱动与被动输入流协议实现。

协议规范参考：https://github.com/Lw520Y/MCHOSE-G3-
- 硬件标识：VID = 0x258A，PID = 0x0138（2.4G 接收器）
- 状态接口：usage_page = 0xFF02（Col07，优先 usage = 0x0002），仅被动输入流（每 48 秒自发一帧）
- 帧格式（首字节 0x09）：
    09 0a 05 XX ... : 电量 XX%（0..100），XX == 0xFF 表示充电中哨兵值（不报百分比）
    09 0a 02 00 ... : 鼠标进入休眠事件
    09 0a 03 01/02  : DPI 按键按下/释放事件
- 超过 STALE_AFTER_SEC（120 秒）未收到有效帧判定为休眠/离线。
"""

from dataclasses import dataclass
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Sequence

from core.interfaces import BaseDeviceDriver
from core.models import (
    BatteryReading,
    ConnectionMode,
    DevicePollResult,
    DeviceSlot,
    quantize_battery_step10,
)

from . import hid_transport


VID_SINO_WEALTH = 0x258A
PID_G3_CLASSIC = 0x0138
USAGE_PAGE_G3_CLASSIC = 0xFF02
USAGE_G3_CLASSIC = 0x0002

FRAME_HEADER = 0x09
EVT_SLEEP = 0x02
EVT_DPI = 0x03
EVT_BATTERY = 0x05
CHARGE_SENTINEL = 0xFF
STALE_AFTER_SEC = 120.0


@dataclass(frozen=True)
class G3ClassicEvent:
    """原始款 G3 单帧被动事件解码结果。"""

    kind: str  # "battery" | "charging" | "sleep" | "activity"
    raw_percent: Optional[int] = None


class G3ClassicProtocol:
    """迈从 G3 原始款无状态二进制帧解码器。"""

    @staticmethod
    def align_frame(raw: Sequence[int]) -> Optional[bytes]:
        """对齐接收包，剥离可选前导 Report ID 0x00，确保以 0x09 开头。"""
        if not raw:
            return None
        if len(raw) >= 4 and raw[0] == FRAME_HEADER:
            return bytes(raw)
        if len(raw) >= 5 and raw[0] == 0x00 and raw[1] == FRAME_HEADER:
            return bytes(raw[1:])
        return None

    @classmethod
    def parse_input_frame(cls, raw: Sequence[int]) -> Optional[G3ClassicEvent]:
        """解析 Col07 8 字节输入帧；不匹配或未知事件返回 None。"""
        frame = cls.align_frame(raw)
        if frame is None:
            return None

        event_type = frame[2]
        if event_type == EVT_BATTERY:
            val = int(frame[3])
            if val == CHARGE_SENTINEL:
                return G3ClassicEvent(kind="charging", raw_percent=None)
            if 1 <= val <= 100:
                return G3ClassicEvent(kind="battery", raw_percent=val)
            return None

        if event_type == EVT_SLEEP:
            return G3ClassicEvent(kind="sleep")

        if event_type == EVT_DPI:
            return G3ClassicEvent(kind="activity")

        return None


class G3ClassicDriver(BaseDeviceDriver):
    """迈从 G3 原始款（258A:0138）非阻塞后台监听驱动。"""

    slot = DeviceSlot.MOUSE
    driver_id = "mchose_g3_classic"
    display_name = "G3"
    priority = 12
    is_virtual = False

    def __init__(
        self,
        open_device_fn: Optional[Callable[[Any], Any]] = None,
        on_update: Optional[Callable[[], None]] = None,
        clock_fn: Optional[Callable[[], float]] = None,
        stale_after_sec: float = STALE_AFTER_SEC,
        start_listener: bool = True,
    ) -> None:
        self._open_device_fn = open_device_fn or hid_transport.open_device
        self._update_callback = on_update
        self._clock_fn = clock_fn or time.monotonic
        self._stale_after_sec = float(stale_after_sec)
        self._start_listener_enabled = start_listener

        self._lock = threading.Lock()
        self._raw_percent: Optional[int] = None
        self._charging: bool = False
        self._sleeping: bool = False
        self._last_frame_ts: Optional[float] = None

        self._stop_event: Optional[threading.Event] = None
        self._thread: Optional[threading.Thread] = None
        self._listener_path: Any = None

    @staticmethod
    def find_target(hid_devices: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """查找 258A:0138 的 FF02 状态通道（优先匹配 usage == 0x0002）。"""
        fallback: Optional[Dict[str, Any]] = None
        for info in hid_devices:
            if (
                info.get("vendor_id") == VID_SINO_WEALTH
                and info.get("product_id") == PID_G3_CLASSIC
                and info.get("usage_page") == USAGE_PAGE_G3_CLASSIC
            ):
                if info.get("usage") == USAGE_G3_CLASSIC:
                    return info
                if fallback is None:
                    fallback = info
        return fallback

    def set_update_callback(self, callback: Optional[Callable[[], None]]) -> None:
        with self._lock:
            self._update_callback = callback

    def stop(self) -> None:
        self._stop_listener()

    def process_frame(self, raw: Sequence[int]) -> bool:
        """处理一帧输入报告并在关键状态变更时触发异步唤醒回调。"""
        evt = G3ClassicProtocol.parse_input_frame(raw)
        if evt is None:
            return False

        now = self._clock_fn()
        changed = False
        callback: Optional[Callable[[], None]] = None

        with self._lock:
            if evt.kind == "battery" and evt.raw_percent is not None:
                old_step = quantize_battery_step10(self._raw_percent) if self._raw_percent is not None else None
                new_step = quantize_battery_step10(evt.raw_percent)
                changed = (
                    self._raw_percent is None
                    or old_step != new_step
                    or self._charging
                    or self._sleeping
                )
                self._raw_percent = evt.raw_percent
                self._charging = False
                self._sleeping = False
                self._last_frame_ts = now
            elif evt.kind == "charging":
                changed = (not self._charging) or self._sleeping or (self._last_frame_ts is None)
                self._charging = True
                self._sleeping = False
                self._raw_percent = None
                self._last_frame_ts = now
            elif evt.kind == "sleep":
                changed = not self._sleeping
                self._sleeping = True
                self._charging = False
                self._last_frame_ts = now
            elif evt.kind == "activity":
                changed = self._sleeping
                self._sleeping = False
                if self._last_frame_ts is not None:
                    self._last_frame_ts = now

            if changed:
                callback = self._update_callback

        if changed and callback is not None:
            try:
                callback()
            except Exception:
                pass
        return True

    def _ensure_listener(self, path: Any) -> None:
        if not self._start_listener_enabled:
            return
        with self._lock:
            if (
                self._thread is not None
                and self._thread.is_alive()
                and self._listener_path == path
            ):
                return

        self._stop_listener()
        stop_evt = threading.Event()
        thread = threading.Thread(
            target=self._reader_loop,
            args=(path, stop_evt),
            name="G3ClassicHidListener",
            daemon=True,
        )
        with self._lock:
            self._stop_event = stop_evt
            self._listener_path = path
            self._thread = thread
        thread.start()

    def _stop_listener(self, reset_state: bool = False) -> None:
        with self._lock:
            stop_evt = self._stop_event
            thread = self._thread
            self._stop_event = None
            self._thread = None
            self._listener_path = None
            if reset_state:
                self._raw_percent = None
                self._charging = False
                self._sleeping = False
                self._last_frame_ts = None

        if stop_evt is not None:
            stop_evt.set()
        if thread is not None and thread.is_alive() and threading.current_thread() is not thread:
            thread.join(timeout=1.0)

    def _reader_loop(self, path: Any, stop_evt: threading.Event) -> None:
        while not stop_evt.is_set():
            try:
                with self._open_device_fn(path) as dev:
                    while not stop_evt.is_set():
                        try:
                            data = hid_transport.read_with_timeout(dev, 8, 500)
                        except OSError:
                            break
                        if data:
                            self.process_frame(data)
            except Exception:
                pass
            if not stop_evt.is_set():
                stop_evt.wait(1.0)

    def poll(self, hid_devices: List[Dict[str, Any]]) -> Optional[DevicePollResult]:
        """非阻塞读取当前缓存的 258A:0138 电量与充电/休眠状态。"""
        target = self.find_target(hid_devices)
        if target is None:
            self._stop_listener(reset_state=True)
            return None

        self._ensure_listener(target["path"])
        now = self._clock_fn()

        with self._lock:
            raw_pct = self._raw_percent
            charging = self._charging
            sleeping = self._sleeping
            last_ts = self._last_frame_ts

        # 刚启动或刚插上接收器、尚未等来第一帧 48s 自发电池帧
        if last_ts is None:
            return DevicePollResult(
                hardware_connected=True,
                reading=BatteryReading(
                    percent=None,
                    charging=False,
                    online=True,
                    model_name=self.display_name,
                    connection_mode=ConnectionMode.WIRELESS_24G,
                    vid=VID_SINO_WEALTH,
                    pid=PID_G3_CLASSIC,
                ),
            )

        stale = (now - last_ts) > self._stale_after_sec
        if sleeping or stale:
            return DevicePollResult(
                hardware_connected=True,
                reading=BatteryReading(
                    percent=None,
                    charging=False,
                    online=False,
                    model_name=self.display_name,
                    connection_mode=ConnectionMode.WIRELESS_24G,
                    vid=VID_SINO_WEALTH,
                    pid=PID_G3_CLASSIC,
                ),
            )

        if charging:
            return DevicePollResult(
                hardware_connected=True,
                reading=BatteryReading(
                    percent=None,
                    charging=True,
                    online=True,
                    model_name=self.display_name,
                    connection_mode=ConnectionMode.WIRELESS_24G,
                    vid=VID_SINO_WEALTH,
                    pid=PID_G3_CLASSIC,
                    raw_charge_code=1,
                    raw_percent=None,
                ),
            )

        if raw_pct is None:
            return DevicePollResult(
                hardware_connected=True,
                reading=BatteryReading(
                    percent=None,
                    charging=False,
                    online=True,
                    model_name=self.display_name,
                    connection_mode=ConnectionMode.WIRELESS_24G,
                    vid=VID_SINO_WEALTH,
                    pid=PID_G3_CLASSIC,
                ),
            )

        return DevicePollResult(
            hardware_connected=True,
            reading=BatteryReading(
                percent=quantize_battery_step10(raw_pct),
                charging=False,
                online=True,
                model_name=self.display_name,
                connection_mode=ConnectionMode.WIRELESS_24G,
                vid=VID_SINO_WEALTH,
                pid=PID_G3_CLASSIC,
                raw_charge_code=0,
                raw_percent=raw_pct,
            ),
        )
