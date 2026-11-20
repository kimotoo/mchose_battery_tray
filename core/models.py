# -*- coding: utf-8 -*-
"""核心数据结构与领域模型。"""

from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import Callable, List, Optional


class DeviceSlot(str, Enum):
    """托盘双环设备槽位。"""

    MOUSE = "mouse"        # 外圈：鼠标
    KEYBOARD = "keyboard"  # 内圈：键盘


class DisplayMode(str, Enum):
    """界面展示形态。"""

    TASKBAR_BAR = "taskbar_bar"      # 嵌入 Win10 任务栏的常驻信息条
    TRAY_ICON_ONLY = "tray_icon"     # 仅在系统托盘区显示图标


class WidgetStyle(str, Enum):
    """可视化样式选项。"""

    DUAL_RING = "dual_ring"          # 左双环图标 + 右双行文字（鼠标 + 键盘）
    MOUSE_CAPSULE = "mouse_capsule"  # 左竖向鼠标电量胶囊 + 右单行百分比（仅鼠标）


class TaskbarPlacement(str, Enum):
    """任务栏常驻条相对 TrafficMonitor 等第三方组件的停靠位置。"""

    LEFT_OF_SIBLINGS = "left_of_siblings"    # 位于 TrafficMonitor 等组件左侧（默认推荐：自动让位）
    RIGHT_OF_SIBLINGS = "right_of_siblings"  # 位于 TrafficMonitor 等组件右侧（贴近托盘 ^）


class ConnectionMode(str, Enum):
    """设备连接方式。"""

    UNKNOWN = "未知"
    USB = "有线"
    WIRELESS_24G = "2.4G"
    BLUETOOTH = "蓝牙"
    VIRTUAL = "默认"


class PollingRate(IntEnum):
    """鼠标回报率枚举（对应 G3 V2 协议索引）。"""

    HZ_125 = 0
    HZ_250 = 1
    HZ_500 = 2
    HZ_1000 = 3
    HZ_2000 = 4
    HZ_4000 = 5
    HZ_8000 = 6

    @property
    def hz(self) -> int:
        return (125, 250, 500, 1000, 2000, 4000, 8000)[int(self)]

    @classmethod
    def from_protocol_byte(cls, value: int) -> "PollingRate":
        idx = max(0, min(6, value - 1))
        return cls(idx)

    def to_protocol_byte(self) -> int:
        return int(self) + 1


class LodLevel(IntEnum):
    """鼠标静默高度 (LOD)。"""

    LOW_1MM = 0
    HIGH_2MM = 1


@dataclass(frozen=True)
class BatteryReading:
    """单次设备电量与状态读取结果。"""

    percent: Optional[int]
    charging: bool = False
    online: bool = True
    model_name: str = ""
    connection_mode: ConnectionMode = ConnectionMode.UNKNOWN
    vid: int = 0
    pid: int = 0
    raw_charge_code: int = 0
    raw_percent: Optional[int] = None


@dataclass
class DevicePollResult:
    """驱动轮询返回结果，区分硬件是否接入及在线状态。"""

    hardware_connected: bool
    reading: Optional[BatteryReading] = None


@dataclass
class SlotState:
    """槽位聚合状态（含休眠缓存保留逻辑）。"""

    slot: DeviceSlot
    display_label: str
    model_name: str
    connected: bool = False
    online: bool = False
    charging: bool = False
    percent: Optional[int] = None
    connection_mode: ConnectionMode = ConnectionMode.UNKNOWN
    is_virtual: bool = False


@dataclass
class MouseConfig:
    """迈从 G3 V2 鼠标硬件配置模型（预留用于后续 DPI/回报率等拓展）。"""

    report_rate: PollingRate = PollingRate.HZ_250
    active_dpi_stage: int = 3
    dpi_stages_count: int = 6
    dpi_stages: List[int] = field(default_factory=lambda: [400, 800, 1600, 3200, 6400, 12000])
    lod: LodLevel = LodLevel.HIGH_2MM
    debounce_ms: int = 8
    sleep_minutes: int = 3
    sensor_flags: int = 0
    scroll_flag: int = 0
    highspeed_mode: int = 0
    sensor_angle: int = 0


@dataclass
class TrayMenuAction:
    """与具体托盘 UI 库解耦的扩展菜单项定义。"""

    label: str
    callback: Optional[Callable[[], None]] = None
    checked: Optional[Callable[[], bool]] = None
    enabled: bool = True
    radio: bool = False
    children: List["TrayMenuAction"] = field(default_factory=list)


def quantize_battery_step10(raw_percent: int) -> int:
    """按迈从官方 M HUB 规则将 1% 原始电量映射为整 10 挡位。

    区间映射：0..14->10, 15..24->20, 25..34->30, ..., 85..94->90, 95..100->100。
    """
    clamped = max(0, min(100, int(raw_percent)))
    if clamped <= 14:
        return 10
    return min(100, ((clamped + 5) // 10) * 10)
