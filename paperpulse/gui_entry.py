"""GUI 入口(PyInstaller --windowed 目标)。"""

import sys

from paperpulse.gui.app import main

if __name__ == "__main__":
    sys.exit(main())
