# -*- coding: utf-8 -*-
"""低电量通知状态机（<= 10% 触发一次，回充后重置）。"""

from typing import Dict, Optional, Tuple

from config import APP_NAME, LOW_NOTIFY_THRESHOLD, NOTIFY_RESET_THRESHOLD
from core.models import DeviceSlot, SlotState


class LowBatteryNotifier:
    """按设备槽位独立管理低电量通知锁，防止休眠唤醒或临界波动重复弹窗。"""

    def __init__(
        self,
        notify_threshold: int = LOW_NOTIFY_THRESHOLD,
        reset_threshold: int = NOTIFY_RESET_THRESHOLD,
    ) -> None:
        self._notify_threshold = notify_threshold
        self._reset_threshold = max(notify_threshold + 1, reset_threshold)
        self._notified: Dict[DeviceSlot, bool] = {}

    def evaluate(self, state: SlotState) -> Optional[Tuple[str, str]]:
        """评估单个槽位状态。若应弹出通知则返回 (title, message)，否则返回 None。"""
        if state.is_virtual or state.percent is None:
            return None

        # 正在充电时立即解锁，确保下次耗尽至阈值时可再次提醒
        if state.charging:
            self._notified[state.slot] = False
            return None

        # 离线或休眠时不触发新通知，也保留现有锁状态，防止唤醒时误重弹
        if not state.connected or not state.online:
            return None

        if state.percent >= self._reset_threshold:
            self._notified[state.slot] = False
            return None

        if state.percent <= self._notify_threshold:
            if not self._notified.get(state.slot, False):
                self._notified[state.slot] = True
                msg = f"{state.display_label} {state.model_name} 电量仅剩 {state.percent}%，请及时充电"
                return APP_NAME, msg

        return None

    def is_notified(self, slot: DeviceSlot) -> bool:
        """查询某槽位当前是否处于已通知锁定状态（便于测试与状态检查）。"""
        return self._notified.get(slot, False)
