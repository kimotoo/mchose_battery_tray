# -*- coding: utf-8 -*-
"""迈从 G3 V2 Windows 电量托盘监控主入口。"""

from pathlib import Path
import sys

from drivers import create_default_manager
from ui import (
    TrayApplication,
    acquire_single_instance,
    load_user_settings,
    settings_path,
)


def main() -> int:
    ok, _mutex_handle = acquire_single_instance()
    if not ok:
        return 0

    cfg_path = settings_path()
    saved = load_user_settings(cfg_path)
    manager = create_default_manager()
    app = TrayApplication(
        manager=manager,
        refresh_sec=saved.refresh_sec,
        entry_script=Path(__file__),
        display_mode=saved.display_mode,
        widget_style=saved.widget_style,
        taskbar_placement=saved.taskbar_placement,
        settings_path=cfg_path,
    )
    app.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
