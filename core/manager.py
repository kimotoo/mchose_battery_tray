# -*- coding: utf-8 -*-
"""多槽位设备管理器，协调设备枚举、驱动优先级匹配、状态缓存与扩展菜单聚合。"""

from typing import Any, Callable, Dict, List, Optional

from .interfaces import BaseDeviceDriver, IMenuContributor
from .models import BatteryReading, ConnectionMode, DeviceSlot, SlotState, TrayMenuAction


class DeviceManager:
    """管理外圈鼠标与内圈键盘槽位的状态机与驱动列表。"""

    def __init__(
        self,
        enumerate_fn: Callable[[], List[Dict[str, Any]]],
        upward_hysteresis_percent: int = 3,
    ) -> None:
        self._enumerate_fn = enumerate_fn
        self._upward_hysteresis_percent = max(0, int(upward_hysteresis_percent))
        self._drivers: Dict[DeviceSlot, List[BaseDeviceDriver]] = {
            DeviceSlot.MOUSE: [],
            DeviceSlot.KEYBOARD: [],
        }
        self._states: Dict[DeviceSlot, SlotState] = {
            DeviceSlot.MOUSE: SlotState(
                slot=DeviceSlot.MOUSE,
                display_label="鼠标",
                model_name="MCHOSE",
            ),
            DeviceSlot.KEYBOARD: SlotState(
                slot=DeviceSlot.KEYBOARD,
                display_label="键盘",
                model_name="MCHOSE",
            ),
        }

    def register_driver(self, driver: BaseDeviceDriver) -> None:
        """注册设备驱动并按优先级升序排序（数值越小优先级越高）。"""
        slot_drivers = self._drivers[driver.slot]
        slot_drivers.append(driver)
        slot_drivers.sort(key=lambda item: item.priority)

    def get_state(self, slot: DeviceSlot) -> SlotState:
        """获取指定槽位当前聚合状态。"""
        return self._states[slot]

    def get_all_states(self) -> Dict[DeviceSlot, SlotState]:
        """获取所有槽位当前状态快照。"""
        return dict(self._states)

    def refresh_all(self) -> Dict[DeviceSlot, SlotState]:
        """执行一次全槽位设备扫描与电量更新。"""
        try:
            hid_devices = self._enumerate_fn()
        except Exception:
            hid_devices = []

        for slot, drivers in self._drivers.items():
            self._refresh_slot(slot, drivers, hid_devices)

        return self.get_all_states()

    def _apply_battery_reading(self, state: SlotState, reading: BatteryReading) -> None:
        """应用单次电量读数，并在未充电状态下抑制原始电量小幅回升引起的向上跳档。"""
        new_percent = reading.percent
        new_raw = reading.raw_percent

        if reading.charging:
            state.last_raw_percent = new_raw
            if new_percent is not None:
                state.percent = new_percent
            return

        if new_percent is None:
            return

        if (
            state.is_virtual
            or state.percent is None
            or new_raw is None
            or state.last_raw_percent is None
            or self._upward_hysteresis_percent <= 0
        ):
            state.percent = new_percent
            state.last_raw_percent = new_raw
            return

        if new_percent <= state.percent:
            state.percent = new_percent
            state.last_raw_percent = new_raw
            return

        if (new_raw - state.last_raw_percent) > self._upward_hysteresis_percent:
            state.percent = new_percent
            state.last_raw_percent = new_raw

    def _refresh_slot(
        self,
        slot: DeviceSlot,
        drivers: List[BaseDeviceDriver],
        hid_devices: List[Dict[str, Any]],
    ) -> None:
        state = self._states[slot]

        for driver in drivers:
            try:
                result = driver.poll(hid_devices)
            except Exception:
                result = None

            if result is None or not result.hardware_connected:
                continue

            state.connected = True
            state.is_virtual = driver.is_virtual
            reading = result.reading

            if reading is not None:
                if reading.model_name:
                    state.model_name = reading.model_name
                if reading.connection_mode != ConnectionMode.UNKNOWN:
                    state.connection_mode = reading.connection_mode

                if reading.online:
                    state.online = True
                    state.charging = reading.charging
                    self._apply_battery_reading(state, reading)
                else:
                    # 硬件接收器仍在，但外设处于休眠或短暂通信失败：保留上次有效电量缓存
                    state.online = False
                    state.charging = False
            else:
                state.online = False
                state.charging = False
            return

        # 没有任何驱动匹配到该槽位的在位硬件：重置为未连接状态
        state.connected = False
        state.online = False
        state.charging = False
        state.percent = None
        state.last_raw_percent = None
        state.connection_mode = ConnectionMode.UNKNOWN
        state.is_virtual = False

    def collect_menu_actions(self) -> List[TrayMenuAction]:
        """收集所有实现 IMenuContributor 的驱动所贡献的扩展托盘菜单项。"""
        actions: List[TrayMenuAction] = []
        for slot in (DeviceSlot.MOUSE, DeviceSlot.KEYBOARD):
            for driver in self._drivers[slot]:
                if isinstance(driver, IMenuContributor):
                    actions.extend(driver.get_menu_actions())
        return actions

    def set_async_update_callback(self, callback: Optional[Callable[[], None]]) -> None:
        """为全部已注册驱动注入异步状态变更唤醒回调。"""
        for drivers in self._drivers.values():
            for driver in drivers:
                driver.set_update_callback(callback)

    def stop_drivers(self) -> None:
        """停止全部驱动的后台监听线程及资源。"""
        for drivers in self._drivers.values():
            for driver in drivers:
                try:
                    driver.stop()
                except Exception:
                    pass
