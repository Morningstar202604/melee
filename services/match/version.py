"""单一版本源：API、CLI、A2A 卡与两个打包脚本都从这里取版本号。

以前四处各写一份 `APP_VERSION`，A2A 卡停在 1.8.0、安装器停在 1.9.2，
用户看到的版本号取决于他从哪个入口打开产品。版本号只允许有一个字面量。
"""

from __future__ import annotations

APP_VERSION = "1.10.1"
