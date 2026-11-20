# -*- coding: utf-8 -*-
"""内圈默认虚拟键盘驱动。

当前无真实键盘驱动接入时，默认返回未连接状态（内圈线条变暗且文字显示 `--`）；
后续如需接入真实键盘（如 G87），只需在 drivers/registry.py 中于更高优先级注册真实驱动即可。
"""

from typing import Any, Dict, List, Optional

from config import DEFAULT_KEYBOARD_PERCENT
from core.interfaces import BaseDeviceDriver
from core.models import (
    BatteryReading,
    ConnectionMode,
    DevicePollResult,
    DeviceSlot,
)


class VirtualKeyboardDriver(BaseDeviceDriver):
    """兜底虚拟键盘槽位驱动（默认未接入硬件，可按需指定测试默认电量）。"""

    slot = DeviceSlot.KEYBOARD
    driver_id = "virtual_keyboard_default"
    display_name = "MCHOSE"
    priority = 999  # 最低优先级兜底：一旦注册了真实键盘驱动，将优先使用真实驱动
    is_virtual = True

    def __init__(self, default_percent: Optional[int] = DEFAULT_KEYBOARD_PERCENT) -> None:
        self._default_percent: Optional[int] = (
            max(0, min(100, int(default_percent)))
            if default_percent is not None
            else None
        )

    def poll(self, hid_devices: List[Dict[str, Any]]) -> Optional[DevicePollResult]:
        if self._default_percent is None:
            return None
        return DevicePollResult(
            hardware_connected=True,
            reading=BatteryReading(
                percent=self._default_percent,
                charging=False,
                online=True,
                model_name="默认",
                connection_mode=ConnectionMode.VIRTUAL,
            ),
        )
