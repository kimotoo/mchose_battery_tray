# -*- coding: utf-8 -*-
"""全局配置常量。"""

from typing import Optional

APP_NAME = "迈从电量托盘"
APP_TITLE = "迈从 G3 V2 电量托盘"
MUTEX_NAME = "Global\\MCHOSE_G3V2_BATTERY_TRAY"
SHORTCUT_NAME = "迈从电量托盘.lnk"

# 托盘图标与超采样参数
ICON_SIZE = 64
SUPERSAMPLE_FACTOR = 4

# 电量阈值配置（百分比）
MID_COLOR_THRESHOLD = 30     # <= 30% 变黄
LOW_COLOR_THRESHOLD = 15     # <= 15% 变红
LOW_NOTIFY_THRESHOLD = 10    # <= 10% 弹一次低电量通知
NOTIFY_RESET_THRESHOLD = 15  # 未插线充电时，电量回升至 >= 15% 重置提醒锁（防临界抖动）

# 轮询间隔配置（秒）
DEFAULT_REFRESH_SEC = 60
REFRESH_INTERVAL_OPTIONS = (
    ("30 秒", 30),
    ("1 分钟", 60),
    ("5 分钟", 300),
    ("1 小时", 3600),
)

# 内圈虚拟键盘默认电量（None 表示未接入真实键盘硬件：内圈线条变暗且显示 --）
DEFAULT_KEYBOARD_PERCENT: Optional[int] = None
