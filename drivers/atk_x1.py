# -*- coding: utf-8 -*-
"""ATK X1 Ultimate (VID=0x373B, PID=0x11D9) HID 电量驱动实现。

协议参考 MyATxMouseControl (src/atk-x1.ts)：
- 通信接口：usage_page = 0xFF02, usage = 0x0002, 17 字节 Report ID 0x08
- 查询帧（17 字节）：08 04 00 00 00 00 00 00 00 00 00 00 00 00 00 00 49
  （尾字节校验和满足 sum(bytes[0..16]) & 0xFF == 0x55）
- 应答帧（17 字节）：[0]=0x08, [1]=0x04, [2]=status, [6]=电量(0..100), [7]=有线/充电标志(>0), [16]=校验和
- 鼠标休眠时 8K 接收器静默不响应，由 DeviceManager 保留上次有效电量并标记休眠状态
"""

from typing import Any, Dict

from .a5_compx import CompxBatteryDriverBase, MchoseA5CompxProtocol


VID_ATK = 0x373B
PID_ATK_X1_ULTIMATE_8K = 0x11D9
PID_ATK_X1_ULTIMATE_8K_ALT = 0x101B

ATK_X1_WIRELESS_PIDS: Dict[int, str] = {
    PID_ATK_X1_ULTIMATE_8K: "ATK X1 Ultimate",
    PID_ATK_X1_ULTIMATE_8K_ALT: "ATK X1 Ultimate",
}

_WIRELESS_PRODUCT_HINTS = ("DONGLE", "RECEIVER", "2.4G", "8K", "WIRELESS")

AtkX1Protocol = MchoseA5CompxProtocol


class AtkX1Driver(CompxBatteryDriverBase):
    """ATK X1 Ultimate (8K 接收器 / 同系 Compx 协议) 鼠标驱动。"""

    driver_id = "atk_x1"
    display_name = "ATK X1 Ultimate"
    priority = 20

    @classmethod
    def is_supported_device(cls, vid: int, pid: int, product_string: str = "") -> bool:
        if vid != VID_ATK:
            return False
        if pid in ATK_X1_WIRELESS_PIDS:
            return True
        upper = (product_string or "").upper()
        if "ATK" not in upper:
            return False
        return "X1" in upper or ("MOUSE" in upper and "8K DONGLE" in upper)

    @classmethod
    def _is_wired_target(cls, target: Dict[str, Any]) -> bool:
        pid = int(target.get("product_id") or 0)
        if pid in ATK_X1_WIRELESS_PIDS:
            return False
        upper = str(target.get("product_string") or "").upper()
        return not any(hint in upper for hint in _WIRELESS_PRODUCT_HINTS)

    @classmethod
    def _resolve_model_name(cls, target: Dict[str, Any]) -> str:
        upper = str(target.get("product_string") or "").strip().upper()
        if "X1 ULTIMATE" in upper:
            return "ATK X1 Ultimate"
        if "X1 ULTRA" in upper:
            return "ATK X1 Ultra"
        if "X1 PRO MAX" in upper:
            return "ATK X1 Pro Max"
        if "X1 PRO" in upper:
            return "ATK X1 Pro"
        if "ATK" in upper and "X1" in upper:
            return "ATK X1"
        pid = int(target.get("product_id") or 0)
        return ATK_X1_WIRELESS_PIDS.get(pid, cls.display_name)
