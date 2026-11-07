"""CLI 打包入口(顶层模块,避免包内 __main__.py 的相对导入问题)。"""

import sys

from paperpulse.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
