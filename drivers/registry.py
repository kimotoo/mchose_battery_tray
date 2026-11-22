# -*- coding: utf-8 -*-
"""驱动注册中心：集中管理已启用及后续预留扩展的设备驱动。"""

from core.manager import DeviceManager

from .a5_compx import MchoseA5CompxDriver
from .a5_legacy import MchoseA5LegacyDriver
from .atk_x1 import AtkX1Driver
from .g3_classic import G3ClassicDriver
from .g3_v2 import G3V2Driver
from .mchose_family import MchoseClassicDriver, MchoseModernDriver
from .virtual_keyboard import VirtualKeyboardDriver


def create_default_manager(enumerate_fn=None) -> DeviceManager:
    """构建默认设备管理器并注册各槽位驱动。

    鼠标槽位支持全家族识别与自动仲裁：
      1. G3 V2 / G3 V2 Pro / A7e / A7e Pro (0x55/0xAA 配置协议)
      2. G3 原始款 (258A:0138 SINO WEALTH FF02 被动输入流协议)
      3. 迈从经典全系 A7 / A7 Pro / A7 Ultra / A7 V2 / A5 Ultra / A5 V2 Ultra / M7 / L7 / K7 Ultra (0x11/0x06)
      4. 迈从新一代 G3 V3 / A7 V3 / A5 V3 / K7 V2 / K5 / R7 / V7 (0x4D/0x0900)
      5. 迈从早期 A5 Pro / A5 Pro Max / AX5 系 (0x2023, 0xFFFF Cmd 0x83)
      6. 迈从 A5 / A5 Pro Compx 方案 (0x3554:0xF550/0xF551, Report ID 0x08 / Cmd 0x04)
      7. ATK X1 Ultimate 8K Dongle (0x373B:0x11D9, Compx Report ID 0x08 / Cmd 0x04)
    """
    from . import hid_transport

    manager = DeviceManager(enumerate_fn=enumerate_fn or hid_transport.enumerate_devices)
    manager.register_driver(G3V2Driver())
    manager.register_driver(G3ClassicDriver())
    manager.register_driver(MchoseClassicDriver())
    manager.register_driver(MchoseModernDriver())
    manager.register_driver(MchoseA5LegacyDriver())
    manager.register_driver(MchoseA5CompxDriver())
    manager.register_driver(AtkX1Driver())
    manager.register_driver(VirtualKeyboardDriver())
    return manager
