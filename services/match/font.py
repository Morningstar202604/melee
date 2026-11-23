"""字体加载与进程内缓存：视频渲染与分享卡共用同一套候选路径。

之前分享卡从 `render` 导入私有 `_font`，两个渲染入口被一个私有名绑在一起；
字体是媒体层的公共资源，不该藏在某一个渲染器里。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

FONT_CANDIDATES = (
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/msyhbd.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "/System/Library/Fonts/PingFang.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
)


@lru_cache(maxsize=64)
def font(size: int):
    """字号 → 字体对象，进程内缓存；一帧要画二十来行，重复载入很贵。"""
    from PIL import ImageFont

    for path in FONT_CANDIDATES:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return ImageFont.load_default()
