"""HTTP 路由按业务域拆分：比赛、赛季、媒体、导播、分享、系统。"""

from ..a2a import router as a2a_router
from . import director, matches, media, season_routes, share, system

ROUTERS = (
    a2a_router,
    system.router,
    matches.router,
    season_routes.router,
    media.router,
    director.router,
    share.router,
)

__all__ = ["ROUTERS"]
