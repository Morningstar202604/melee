"""PyInstaller 入口：exe 的 `main()` 只做一件事——把 CLI 跑起来。

保持极薄：任何逻辑都该在 services/match/cli.py 里，这里只负责接线，
否则打包后的栈回溯会多一层噪音。
"""

import sys

from services.match.cli import main

if __name__ == "__main__":
    sys.exit(main())
