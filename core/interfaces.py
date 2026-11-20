# -*- coding: utf-8 -*-
"""设备驱动抽象基类与可扩展功能接口定义。"""

from abc import ABC, abstractmethod
from typing import Any, Callable, Dict, List, Optional

from .models import (
    DevicePollResult,
    DeviceSlot,
    MouseConfig,
    PollingRate,
    TrayMenuAction,
)


class BaseDeviceDriver(ABC):
    """外设电量监控驱动抽象基类。

    后续接入新系列型号（如 A7、K7 V2、G3 V3、G87 键盘等）时，
    只需继承此类并注册到 DeviceManager 即可自动对接双环渲染与通知状态机。
    """

    slot: DeviceSlot
    driver_id: str
    display_name: str
    priority: int = 100
    is_virtual: bool = False

    @abstractmethod
    def poll(self, hid_devices: List[Dict[str, Any]]) -> Optional[DevicePollResult]:
        """基于当次枚举出的 HID 设备列表读取设备状态。

        返回 None 表示当前系统中未检测到该驱动负责的物理硬件。
        """

    def set_update_callback(self, callback: Optional[Callable[[], None]]) -> None:
        """注册异步事件驱动设备的即时刷新回调（默认忽略）。"""

    def stop(self) -> None:
        """停止驱动内部后台监听资源（默认忽略）。"""


class IConfigurableMouse(ABC):
    """鼠标参数配置扩展能力接口（预留：DPI、回报率、休眠时间等）。"""

    @abstractmethod
    def get_config(self) -> Optional[MouseConfig]:
        """读取鼠标当前板载配置。"""

    @abstractmethod
    def set_config(self, config: MouseConfig) -> bool:
        """写入完整硬件配置到鼠标板载内存。"""

    @abstractmethod
    def set_active_dpi_stage(self, stage: int) -> bool:
        """切换当前激活的 DPI 档位（1..=6）。"""

    @abstractmethod
    def set_polling_rate(self, rate: PollingRate) -> bool:
        """设置无线回报率（125/250/500/1000 Hz 等）。"""

    @abstractmethod
    def set_sleep_timeout(self, minutes: int) -> bool:
        """设置无线休眠时间（0..=100 分钟，0 表示永不休眠）。"""


class IMenuContributor(ABC):
    """托盘动态菜单贡献接口（预留：驱动可向右键菜单注入设备快捷配置项）。"""

    @abstractmethod
    def get_menu_actions(self) -> List[TrayMenuAction]:
        """返回该驱动希望挂载到托盘右键菜单的功能项列表。"""
