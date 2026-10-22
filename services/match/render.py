"""把高光剪辑清单渲染成视频。

只依赖 ffmpeg 与 PIL：前者系统里常有，后者是本项目已用的图像库。
不引入 Remotion——它的许可对组织规模有限制，而这里只需要把
"一张图 + 一行字"按时长推给编码器。

渲染思路刻意保持笨拙：
1. 每个事件画一帧（1600×900），帧内容只由事件决定，不含随机与时钟。
2. 按 per_event_ms 决定每帧重复多少帧，喂给 ffmpeg 的 image2pipe。
3. 同一份剪辑清单渲染两次必须得到同一段视频（除编码器元数据）。

这样"回放 = 直播"这条不变量就从事件流一路延伸到了成品视频。
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any, Iterable

from .clips import Clip

WIDTH = 1600
HEIGHT = 900
FPS = 10

# 与前端舞台同一套配色，保证视频和网页看起来是同一个产品。
BG = (7, 9, 13)
PANEL = (17, 20, 27)
LINE = (35, 40, 56)
TEXT = (232, 234, 242)
DIM = (154, 163, 181)
FAINT = (92, 100, 120)
ACCENT_A = (34, 211, 238)
ACCENT_B = (245, 165, 36)
OK = (52, 211, 153)
BAD = (248, 113, 113)

FONT_CANDIDATES = (
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/msyhbd.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "/System/Library/Fonts/PingFang.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
)


def find_ffmpeg() -> str | None:
    return shutil.which("ffmpeg")


def _font(size: int):
    from PIL import ImageFont

    for path in FONT_CANDIDATES:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return ImageFont.load_default()


@dataclass
class RenderResult:
    path: Path
    frames: int
    duration_ms: int
    bytes: int


def render_clip(
    clip: Clip,
    out_path: str | Path,
    *,
    ffmpeg: str | None = None,
    per_event_ms: int = 900,
    all_events: list[dict[str, Any]] | None = None,
) -> RenderResult:
    """把一段剪辑渲染成 mp4。"""
    binary = ffmpeg or find_ffmpeg()
    if not binary:
        raise RuntimeError("找不到 ffmpeg，无法渲染视频")

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    repeat = max(1, int(per_event_ms / 1000 * FPS))
    total_frames = len(clip.events) * repeat

    cmd = [
        binary,
        "-y",
        "-loglevel",
        "error",
        "-f",
        "image2pipe",
        "-vcodec",
        "png",
        "-r",
        str(FPS),
        "-i",
        "-",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-r",
        str(FPS),
        str(out_path),
    ]
    process = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    assert process.stdin is not None

    # 局面必须用全场事件算：剪辑窗口是从中间截的，
    # 只数窗口内的动作会把"已问 3 次"算成 1 次。
    context = build_context(all_events if all_events is not None else clip.events)
    context["total"] = len(clip.events)
    try:
        for index, event in enumerate(clip.events):
            frame = draw_frame(event, clip=clip, context={**context, "index": index})
            buffer = BytesIO()
            frame.save(buffer, format="PNG")
            payload = buffer.getvalue()
            for _ in range(repeat):
                process.stdin.write(payload)
        process.stdin.close()
    except BrokenPipeError:
        pass

    stderr = process.stderr.read().decode("utf-8", "ignore") if process.stderr else ""
    code = process.wait()
    if code != 0:
        raise RuntimeError(f"ffmpeg 失败（{code}）：{stderr[:500]}")

    return RenderResult(
        path=out_path,
        frames=total_frames,
        duration_ms=int(total_frames / FPS * 1000),
        bytes=out_path.stat().st_size if out_path.exists() else 0,
    )


def render_clips(
    clips: Iterable[Clip],
    out_dir: str | Path,
    *,
    prefix: str = "clip",
    ffmpeg: str | None = None,
    per_event_ms: int = 900,
    all_events: list[dict[str, Any]] | None = None,
) -> list[RenderResult]:
    out_dir = Path(out_dir)
    results = []
    for index, clip in enumerate(clips, start=1):
        results.append(
            render_clip(
                clip,
                out_dir / f"{prefix}-{index:02d}-{clip.kind}.mp4",
                ffmpeg=ffmpeg,
                per_event_ms=per_event_ms,
                all_events=all_events,
            )
        )
    return results


# ---------- 单帧绘制 ----------

def draw_frame(event: dict[str, Any], *, clip: Clip | None = None, context: dict[str, Any] | None = None):
    """一个事件一帧。纯函数：同样的输入永远画出同样的像素。

    context 由调用方按剪辑累积（候选数、提问链、双方进度），
    这样每一帧都能显示"此刻的局面"，而不是孤立的一句话。
    """
    from PIL import Image, ImageDraw

    context = context or {}
    image = Image.new("RGB", (WIDTH, HEIGHT), BG)
    draw = ImageDraw.Draw(image)
    payload = event.get("payload") or {}
    kind = event["type"]

    # 顶部：标识 + 剪辑标题
    draw.text((60, 44), "MELEE / 高光", font=_font(22), fill=DIM)
    if clip is not None:
        draw.text((60, 88), clip.title, font=_font(36), fill=TEXT)
        draw.text((60, 138), clip.detail, font=_font(21), fill=FAINT)

    # 左侧：主画面
    body = _headline(kind, payload, event.get("player"))
    draw.multiline_text((60, 260), body, font=_font(46), fill=TEXT, spacing=12)

    if kind == "action.applied":
        action = payload.get("action", {})
        color = ACCENT_A if event.get("player") == "A" else ACCENT_B
        draw.rectangle([(60, 392), (240, 448)], outline=color, width=2)
        draw.text((84, 406), str(action.get("kind", "")), font=_font(24), fill=color)

    if kind == "match.ended":
        winner = payload.get("winner")
        label = {"red": "红队", "blue": "蓝队"}.get(winner, winner)
        draw.text((60, 392), f"胜者 {label}" if label else "无人获胜",
                  font=_font(34), fill=OK if winner else BAD)

    # 左下：双方账本，让左栏不至于空着
    _draw_ledger(draw, context, left=60, top=500, width=880)

    # 右侧：局面面板
    _draw_panel(draw, context)

    # 底部：进度条 + 事件戳
    _draw_progress(draw, context, event)

    return image


def build_context(events: list[dict[str, Any]]) -> dict[str, Any]:
    """把剪辑窗口折叠成面板需要的几个数。

    三种赛制的"局面"长得不一样：二十问看候选数，代号看两队剩余与翻牌，
    棋类看步数与最后几手。这里一次算全，画的时候按 gameKind 取用。
    """
    remaining = None
    team_remaining: dict[str, int] | None = None
    game_kind = ""
    players: list[str] = []
    asks: list[dict[str, Any]] = []
    asks_by: dict[str, int] = {}
    plays: list[dict[str, Any]] = []
    winner = None

    for event in events:
        payload = event.get("payload") or {}
        if event["type"] == "match.created":
            game_kind = payload.get("game") or game_kind
            players = list(payload.get("players") or players)
        elif event["type"] == "state.changed":
            pub = payload.get("publicState") or {}
            remaining = pub.get("remainingPublic", remaining)
            asks = pub.get("history") or asks
            if isinstance(pub.get("remaining"), dict):
                team_remaining = pub["remaining"]
            history = pub.get("history") or []
            if history:
                plays = history
        elif event["type"] == "action.applied":
            player = event.get("player")
            if player:
                asks_by[player] = asks_by.get(player, 0) + 1
                if not game_kind or game_kind in {"tic-tac-toe", "connect-four", "othello", "nim"}:
                    action = payload.get("action") or {}
                    plays = plays + [
                        {"by": player, "kind": action.get("kind", "move"), "value": action.get("value", "")}
                    ]
        elif event["type"] == "match.ended":
            winner = payload.get("winner")

    if not players:
        players = sorted(asks_by)

    return {
        "gameKind": game_kind or "twenty-questions",
        "players": players,
        "remaining": remaining,
        "teamRemaining": team_remaining,
        "asks": asks,
        "asksBy": asks_by,
        "plays": plays,
        "winner": winner,
        "total": len(events),
        "known": len(events),
    }


def _draw_panel(draw, context: dict[str, Any]) -> None:
    left, top, right, bottom = WIDTH - 560, 250, WIDTH - 60, 720
    draw.rectangle([(left, top), (right, bottom)], fill=PANEL, outline=LINE)

    game_kind = context.get("gameKind", "twenty-questions")
    if game_kind == "codenames":
        _draw_codenames_panel(draw, context, left=left, top=top, right=right, bottom=bottom)
        return
    if game_kind != "twenty-questions":
        _draw_board_panel(draw, context, left=left, top=top, right=right, bottom=bottom)
        return

    remaining = context.get("remaining")
    draw.text((left + 28, top + 22), "候选", font=_font(18), fill=FAINT)
    draw.text((left + 28, top + 48), str(remaining) if remaining is not None else "—",
              font=_font(46), fill=ACCENT_A if remaining and remaining <= 2 else TEXT)

    asks_by = context.get("asksBy") or {}
    draw.text((left + 200, top + 22), "出手", font=_font(18), fill=FAINT)
    draw.text((left + 200, top + 48), f"A {asks_by.get('A', 0)}  B {asks_by.get('B', 0)}",
              font=_font(30), fill=TEXT)

    draw.line([(left + 28, top + 118), (right - 28, top + 118)], fill=LINE, width=1)
    draw.text((left + 28, top + 132), "提问链", font=_font(18), fill=FAINT)

    y = top + 168
    for index, item in enumerate((context.get("asks") or [])[-6:], start=1):
        if y > bottom - 60:
            break
        who = item.get("by", "")
        mark = "是" if item.get("answer") == "yes" else "否"
        color = OK if item.get("answer") == "yes" else BAD
        draw.text((left + 28, y), f"{who}  {item.get('value', '')}", font=_font(20), fill=TEXT)
        draw.text((right - 70, y), mark, font=_font(20), fill=color)
        y += 44


def _draw_codenames_panel(draw, context: dict[str, Any], *, left: int, top: int, right: int, bottom: int) -> None:
    teams = context.get("teamRemaining") or {}
    red = teams.get("red")
    blue = teams.get("blue")
    draw.text((left + 28, top + 22), "红队剩余", font=_font(18), fill=FAINT)
    draw.text((left + 28, top + 48), str(red) if red is not None else "—", font=_font(42), fill=(248, 113, 113))
    draw.text((left + 200, top + 22), "蓝队剩余", font=_font(18), fill=FAINT)
    draw.text((left + 200, top + 48), str(blue) if blue is not None else "—", font=_font(42), fill=(96, 165, 250))

    draw.line([(left + 28, top + 118), (right - 28, top + 118)], fill=LINE, width=1)
    draw.text((left + 28, top + 132), "线索与翻牌", font=_font(18), fill=FAINT)

    y = top + 168
    for item in (context.get("plays") or [])[-6:]:
        if y > bottom - 60:
            break
        kind = item.get("kind")
        color = OK if kind == "hint" else TEXT
        label = "线索" if kind == "hint" else "翻牌"
        draw.text((left + 28, y), f"{item.get('by', '')}  {label} {item.get('value', '')}",
                  font=_font(20), fill=color)
        y += 44


def _draw_board_panel(draw, context: dict[str, Any], *, left: int, top: int, right: int, bottom: int) -> None:
    plays = context.get("plays") or []
    draw.text((left + 28, top + 22), "步数", font=_font(18), fill=FAINT)
    draw.text((left + 28, top + 48), str(len(plays)), font=_font(46), fill=ACCENT_A)

    asks_by = context.get("asksBy") or {}
    draw.text((left + 200, top + 22), "出手", font=_font(18), fill=FAINT)
    line = "  ".join(f"{p} {asks_by.get(p, 0)}" for p in (context.get("players") or [])[:4])
    draw.text((left + 200, top + 48), line or "—", font=_font(28), fill=TEXT)

    draw.line([(left + 28, top + 118), (right - 28, top + 118)], fill=LINE, width=1)
    draw.text((left + 28, top + 132), "最近落子", font=_font(18), fill=FAINT)

    y = top + 168
    for item in plays[-6:]:
        if y > bottom - 60:
            break
        draw.text((left + 28, y), f"{item.get('by', '')}  {item.get('value', '')}", font=_font(20), fill=TEXT)
        y += 44


def _draw_ledger(draw, context: dict[str, Any], *, left: int, top: int, width: int) -> None:
    """每位选手的出手次数 + 最近两问/两手，按人数分栏。"""
    asks_by = context.get("asksBy") or {}
    plays = context.get("plays") or []
    players = list(context.get("players") or [])[:4] or ["A", "B"]
    column_width = width // len(players)

    for column, player in enumerate(players):
        x = left + column * column_width
        color = ACCENT_A if column % 2 == 0 else ACCENT_B
        draw.rectangle([(x, top), (x + 6, top + 96)], fill=color)
        draw.text((x + 22, top - 4), player, font=_font(24), fill=color)
        draw.text((x + 22 + 40, top + 2), f"出手 {asks_by.get(player, 0)}", font=_font(16), fill=DIM)

        mine = [item for item in plays if item.get("by") == player][-2:]
        y = top + 40
        for item in mine:
            text = str(item.get("value", ""))
            if len(text) > 12:
                text = text[:12] + "…"
            draw.text((x + 22, y), text, font=_font(17), fill=DIM)
            y += 28


def _draw_progress(draw, context: dict[str, Any], event: dict[str, Any]) -> None:
    draw.line([(60, 800), (WIDTH - 60, 800)], fill=LINE, width=2)
    total = max(1, context.get("total", 1))
    index = context.get("index", 0)
    width = (WIDTH - 120) * min(1.0, (index + 1) / total)
    draw.line([(60, 800), (60 + width, 800)], fill=ACCENT_A, width=3)
    draw.text((60, 820), f"{event['type']}  ·  seq {event['seq']}", font=_font(18), fill=FAINT)


def _headline(kind: str, payload: dict[str, Any], player: str | None) -> str:
    if kind == "action.proposed":
        action = payload.get("action", {})
        return f"{player} {action.get('kind')} {action.get('value')}"
    if kind == "action.applied":
        action = payload.get("action", {})
        effects = payload.get("effects") or []
        for effect in effects:
            if effect.get("kind") == "answer":
                mark = "是" if effect.get("answer") == "yes" else "否"
                return f"{player} 问 {effect.get('value')}\n答 {mark}"
            if effect.get("kind") == "guess":
                return f"{player} 报出 {effect.get('value')}"
            if effect.get("kind") == "hint":
                return f"{player} 给线索 {effect.get('word')} {effect.get('count')}"
            if effect.get("kind") == "reveal":
                color = effect.get("color")
                if color == "assassin":
                    return f"{player} 翻到刺客 {effect.get('word')}"
                return f"{player} 翻 {effect.get('value') or effect.get('word')}（{color}）"
            if effect.get("kind") == "action":
                return f"{player} 落子 {effect.get('action')}"
        return f"{player} {action.get('kind')} {action.get('value')}"
    if kind == "action.rejected":
        return f"{player} 犯规：{payload.get('reason')}"
    if kind == "narration.line":
        return str(payload.get("text", ""))
    if kind == "thought.delta":
        return str(payload.get("text", ""))
    if kind == "director.cue":
        return f"镜头 {payload.get('shot')}"
    if kind == "match.ended":
        return f"比赛结束\n{payload.get('reason', '')}"
    if kind == "state.changed":
        pub = payload.get("publicState") or {}
        if "remainingPublic" in pub:
            return f"候选剩 {pub.get('remainingPublic')}"
        if isinstance(pub.get("remaining"), dict):
            teams = pub["remaining"]
            return f"红队剩 {teams.get('red', '—')} · 蓝队剩 {teams.get('blue', '—')}"
        return f"第 {pub.get('turnIndex', '—')} 步"
    return kind


def verify_renderable(clip: Clip) -> list[str]:
    """渲染前自检：没有事件就不该出片。"""
    problems = []
    if not clip.events:
        problems.append("剪辑没有事件")
    if clip.durationMs <= 0:
        problems.append("剪辑时长必须为正")
    return problems
