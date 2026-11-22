# -*- coding: utf-8 -*-
"""设备驱动包导出。"""

from .a5_compx import MchoseA5CompxDriver, MchoseA5CompxProtocol
from .a5_legacy import MchoseA5LegacyDriver, MchoseA5LegacyProtocol
from .atk_x1 import AtkX1Driver, AtkX1Protocol
from .crush80 import BleBatteryReading, Crush80Driver, Crush80Protocol
from .g3_classic import G3ClassicDriver, G3ClassicProtocol
from .g3_v2 import G3V2Driver, G3V2Protocol
from .mchose_family import (
    MchoseCatalog,
    MchoseClassicDriver,
    MchoseClassicProtocol,
    MchoseModernDriver,
    MchoseModernProtocol,
)
from .registry import create_default_manager
from .virtual_keyboard import VirtualKeyboardDriver

__all__ = [
    "AtkX1Driver",
    "AtkX1Protocol",
    "BleBatteryReading",
    "Crush80Driver",
    "Crush80Protocol",
    "G3ClassicDriver",
    "G3ClassicProtocol",
    "G3V2Driver",
    "G3V2Protocol",
    "MchoseA5CompxDriver",
    "MchoseA5CompxProtocol",
    "MchoseA5LegacyDriver",
    "MchoseA5LegacyProtocol",
    "MchoseCatalog",
    "MchoseClassicDriver",
    "MchoseClassicProtocol",
    "MchoseModernDriver",
    "MchoseModernProtocol",
    "VirtualKeyboardDriver",
    "create_default_manager",
]
