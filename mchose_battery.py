# -*- coding: utf-8 -*-
"""迈从 G3 V2 Windows 电量托盘监控主入口。"""

from pathlib import Path
import sys

from drivers import create_default_manager
from ui import TrayApplication, acquire_single_instance


def main() -> int:
    ok, _mutex_handle = acquire_single_instance()
    if not ok:
        return 0

    manager = create_default_manager()
    app = TrayApplication(
        manager=manager,
        entry_script=Path(__file__),
    )
    app.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
