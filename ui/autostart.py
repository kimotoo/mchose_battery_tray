# -*- coding: utf-8 -*-
"""绿色免注册表开机自启管理（基于用户 Startup 目录快捷方式）。"""

import os
from pathlib import Path
import subprocess
import sys
from typing import Optional

from config import SHORTCUT_NAME


def startup_dir() -> Path:
    """获取当前用户的 Windows 启动目录。"""
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
    return Path.home() / "AppData" / "Roaming" / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def shortcut_path() -> Path:
    """返回开机自启快捷方式完整路径。"""
    return startup_dir() / SHORTCUT_NAME


def is_autostart_enabled() -> bool:
    """检查启动目录快捷方式是否存在。"""
    return shortcut_path().exists()


def _resolve_launch_target(entry_script: Optional[Path] = None) -> tuple[Path, str, Path]:
    """区分单文件 exe 打包运行与源码脚本运行，返回 (TargetPath, Arguments, WorkingDirectory)。"""
    if getattr(sys, "frozen", False):
        exe_path = Path(sys.executable).resolve()
        return exe_path, "", exe_path.parent

    script_path = (entry_script or Path(sys.argv[0])).resolve()
    pyw_path = Path(sys.executable).with_name("pythonw.exe")
    target = pyw_path if pyw_path.exists() else Path(sys.executable)
    return target, f'"{script_path}"', script_path.parent


def set_autostart(enable: bool, entry_script: Optional[Path] = None) -> bool:
    """开启或关闭开机自启（仅操作 Startup 文件夹 .lnk，不触碰注册表）。"""
    lnk = shortcut_path()
    try:
        if not enable:
            if lnk.exists():
                lnk.unlink()
            return True

        lnk.parent.mkdir(parents=True, exist_ok=True)
        target, arguments, working_dir = _resolve_launch_target(entry_script)

        # 通过环境变量向 PowerShell 安全传参，避免路径含引号或空格导致解析异常
        env = os.environ.copy()
        env["MCHOSE_LNK_PATH"] = str(lnk)
        env["MCHOSE_TARGET_PATH"] = str(target)
        env["MCHOSE_ARGS"] = arguments
        env["MCHOSE_WORKDIR"] = str(working_dir)

        ps_script = (
            "$ws = New-Object -ComObject WScript.Shell;"
            "$sc = $ws.CreateShortcut($env:MCHOSE_LNK_PATH);"
            "$sc.TargetPath = $env:MCHOSE_TARGET_PATH;"
            "$sc.Arguments = $env:MCHOSE_ARGS;"
            "$sc.WorkingDirectory = $env:MCHOSE_WORKDIR;"
            "$sc.Save()"
        )
        subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_script],
            env=env,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000),
            check=True,
            timeout=20,
        )
        return True
    except Exception:
        return False
