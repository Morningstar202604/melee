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
from dataclasses import dataclass, field
from functools import lru_cache
from io import BytesIO
from pathlib import Path
from typing import Any, Iterable, Iterator

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


# 高光模板：同一份事件、不同的信息主次。全部确定性，不含随机与时钟。
#   broadcast —— 现在这套：左标题 + 左账本 + 右局面面板，信息最全
#   spotlight —— 大字居中：一句话 + 一个巨大数字，适合竖屏与社交传播
#   scorecard —— 双方记分卡并排：突出"谁领先"，适合连播多手
TEMPLATES = ("broadcast", "spotlight", "scorecard")


def find_ffmpeg() -> str | None:
    return shutil.which("ffmpeg")


def normalize_template(name: str | None) -> str:
    """未知模板退回默认，不报错——渲染是异步长任务，不该因参数拼错整段失败。"""
    text = (name or "broadcast").strip().lower()
    return text if text in TEMPLATES else "broadcast"


@lru_cache(maxsize=64)
def _font(size: int):
    """字号 → 字体对象，进程内缓存。

    之前每画一行文字都重新 truetype 载入一次（一帧约 20 次），
    高光渲染 10 帧/秒、每段几十帧，载入字体成了最贵的操作。
    """
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
    template: str = "broadcast",
) -> RenderResult:
    """把一段剪辑渲染成 mp4。`template` 决定版式（见 TEMPLATES）。"""
    # 只有真的没传（None）才去 PATH 里找；显式传空串表示"就是没有"，
    # 否则调用方想禁用渲染时会被系统 ffmpeg 静默接管。
    binary = find_ffmpeg() if ffmpeg is None else ffmpeg
    if not binary:
        raise RuntimeError("找不到 ffmpeg，无法渲染视频")

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    repeat = max(1, int(per_event_ms / 1000 * FPS))
    total_frames = len(clip.events) * repeat

    process = subprocess.Popen(_ffmpeg_cmd(binary, out_path), stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    assert process.stdin is not None

    # 局面必须用全场事件算：剪辑窗口是从中间截的，
    # 只数窗口内的动作会把"已问 3 次"算成 1 次。
    context = build_context(all_events if all_events is not None else clip.events)
    context["total"] = len(clip.events)
    context["template"] = normalize_template(template)
    _feed_frames(process.stdin, clip, context, repeat)

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


def _ffmpeg_cmd(binary: str, out_path: Path) -> list[str]:
    return [
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


def _feed_frames(stdin: Any, clip: Clip, context: dict[str, Any], repeat: int) -> None:
    """把每帧重复 repeat 次推进编码器；编码器提前退出就停止。"""
    try:
        for index, event in enumerate(clip.events):
            frame = draw_frame(event, clip=clip, context={**context, "index": index})
            buffer = BytesIO()
            frame.save(buffer, format="PNG")
            payload = buffer.getvalue()
            for _ in range(repeat):
                stdin.write(payload)
        stdin.close()
    except BrokenPipeError:
        pass


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
    template = normalize_template(context.get("template"))
    if template != "broadcast":
        return _draw_templated(event, context, template)

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
        from .engine.codenames import team_label as _team_label

        label = _team_label(winner) if isinstance(winner, str) and winner.startswith("team") else {"red": "红队", "blue": "蓝队"}.get(winner, winner)
        draw.text((60, 392), f"胜者 {label}" if label else "无人获胜",
                  font=_font(34), fill=OK if winner else BAD)

    # 左下：双方账本，让左栏不至于空着
    _draw_ledger(draw, context, left=60, top=500, width=880)

    # 右侧：局面面板
    _draw_panel(draw, context)

    # 底部：进度条 + 事件戳
    _draw_progress(draw, context, event)

    return image


@dataclass
class _ContextState:
    game_kind: str = ""
    players: list[str] = field(default_factory=list)
    remaining: int | None = None
    team_remaining: dict[str, int] | None = None
    asks: list[dict[str, Any]] = field(default_factory=list)
    asks_by: dict[str, int] = field(default_factory=dict)
    plays: list[dict[str, Any]] = field(default_factory=list)
    winner: str | None = None

    def feed(self, event: dict[str, Any]) -> None:
        payload = event.get("payload") or {}
        handler = {
            "match.created": self._on_created,
            "state.changed": self._on_state,
            "action.applied": self._on_action,
            "match.ended": self._on_ended,
        }.get(event["type"])
        if handler is not None:
            handler(event, payload)

    def _on_created(self, event: dict[str, Any], payload: dict[str, Any]) -> None:
        self.game_kind = payload.get("game") or self.game_kind
        self.players = list(payload.get("players") or self.players)

    def _on_state(self, event: dict[str, Any], payload: dict[str, Any]) -> None:
        pub = payload.get("publicState") or {}
        self.remaining = pub.get("remainingPublic", self.remaining)
        self.asks = pub.get("history") or self.asks
        if isinstance(pub.get("remaining"), dict):
            self.team_remaining = pub["remaining"]
        history = pub.get("history") or []
        if history:
            self.plays = history

    def _on_action(self, event: dict[str, Any], payload: dict[str, Any]) -> None:
        player = event.get("player")
        if not player:
            return
        self.asks_by[player] = self.asks_by.get(player, 0) + 1
        if self.game_kind and self.game_kind not in _BOARD_GAMES:
            return
        action = payload.get("action") or {}
        self.plays = self.plays + [
            {"by": player, "kind": action.get("kind", "move"), "value": action.get("value", "")}
        ]

    def _on_ended(self, event: dict[str, Any], payload: dict[str, Any]) -> None:
        self.winner = payload.get("winner")

    def as_context(self, total: int) -> dict[str, Any]:
        return {
            "gameKind": self.game_kind or "twenty-questions",
            "players": self.players or sorted(self.asks_by),
            "remaining": self.remaining,
            "teamRemaining": self.team_remaining,
            "asks": self.asks,
            "asksBy": self.asks_by,
            "plays": self.plays,
            "winner": self.winner,
            "total": total,
            "known": total,
        }


_BOARD_GAMES = {"tic-tac-toe", "connect-four", "othello", "nim"}


def build_context(events: list[dict[str, Any]]) -> dict[str, Any]:
    """把剪辑窗口折叠成面板需要的几个数。

    三种赛制的"局面"长得不一样：二十问看候选数，代号看两队剩余与翻牌，
    棋类看步数与最后几手。这里一次算全，画的时候按 gameKind 取用。
    """
    state = _ContextState()
    for event in events:
        state.feed(event)
    return state.as_context(len(events))


def _row_ys(top: int, bottom: int, step: int = 44) -> Iterator[int]:
    """面板里一行行的 y 坐标，到底部留 60px 边距就停。"""
    y = top
    while y <= bottom - 60:
        yield y
        y += step


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

    _draw_candidates_panel(draw, context, left=left, top=top, right=right, bottom=bottom)


def _draw_candidates_panel(draw, context: dict[str, Any], *, left: int, top: int, right: int, bottom: int) -> None:
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

    for y, item in zip(_row_ys(top + 168, bottom), (context.get("asks") or [])[-6:], strict=False):
        who = item.get("by", "")
        mark = "是" if item.get("answer") == "yes" else "否"
        color = OK if item.get("answer") == "yes" else BAD
        draw.text((left + 28, y), f"{who}  {item.get('value', '')}", font=_font(20), fill=TEXT)
        draw.text((right - 70, y), mark, font=_font(20), fill=color)


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

    for y, item in zip(_row_ys(top + 168, bottom), (context.get("plays") or [])[-6:], strict=False):
        kind = item.get("kind")
        color = OK if kind == "hint" else TEXT
        label = "线索" if kind == "hint" else "翻牌"
        draw.text((left + 28, y), f"{item.get('by', '')}  {label} {item.get('value', '')}",
                  font=_font(20), fill=color)


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

    for y, item in zip(_row_ys(top + 168, bottom), plays[-6:], strict=False):
        draw.text((left + 28, y), f"{item.get('by', '')}  {item.get('value', '')}", font=_font(20), fill=TEXT)


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
        _draw_recent_plays(draw, [i for i in plays if i.get("by") == player][-2:], x + 22, top + 40)


def _draw_recent_plays(draw, plays: list[dict[str, Any]], x: int, y0: int) -> None:
    for offset, item in enumerate(plays):
        text = str(item.get("value", ""))
        if len(text) > 12:
            text = text[:12] + "…"
        draw.text((x, y0 + offset * 28), text, font=_font(17), fill=DIM)


def _draw_templated(event: dict[str, Any], context: dict[str, Any], template: str):
    """spotlight / scorecard 两套版式。

    与 broadcast 版共用同一份 context 与同一套取词逻辑，只改排版：
    - spotlight：一句话打满屏，数字做成大号强调，适合分享与竖屏裁切；
    - scorecard：双方各项指标并排，突出"谁领先"，适合连播多手。
    """
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (WIDTH, HEIGHT), BG)
    draw = ImageDraw.Draw(image)
    payload = event.get("payload") or {}
    kind = event["type"]
    headline = _headline(kind, payload, event.get("player"))

    if template == "spotlight":
        draw.text((80, 70), "MELEE / 高光", font=_font(22), fill=DIM)
        draw.line([(80, 110), (WIDTH - 80, 110)], fill=LINE, width=2)
        lines = _wrap_text(headline, 16)[:4]
        y = 250 - max(0, len(lines) - 2) * 40
        for line in lines:
            draw.text((80, y), line, font=_font(72), fill=TEXT)
            y += 96
        metric, label = _spotlight_metric(context, event)
        if metric:
            draw.text((WIDTH - 80, 560), metric, font=_font(120), fill=ACCENT_A, anchor="ra")
            draw.text((WIDTH - 80, 700), label, font=_font(24), fill=FAINT, anchor="ra")
        _draw_progress(draw, context, event)
        return image

    # scorecard：左右两栏各画一方，中间一条分隔线
    draw.text((80, 70), "MELEE / 高光", font=_font(22), fill=DIM)
    players = context.get("players") or ["A", "B"]
    half = max(1, len(players) // 2)
    columns = [(players[:half], 80, ACCENT_A), (players[half:], WIDTH // 2 + 40, ACCENT_B)]
    draw.text((80, 150), headline, font=_font(34), fill=TEXT)
    draw.line([(WIDTH // 2, 220), (WIDTH // 2, 750)], fill=LINE, width=2)
    for names, x, color in columns:
        draw.rectangle([(x, 240), (x + 8, 750)], fill=color)
        for index, name in enumerate(names[:2]):
            y = 270 + index * 230
            draw.text((x + 30, y), name, font=_font(40), fill=TEXT)
            stat = _player_stat(context, name)
            draw.text((x + 30, y + 62), stat[0], font=_font(64), fill=color)
            draw.text((x + 30, y + 140), stat[1], font=_font(22), fill=FAINT)
    _draw_progress(draw, context, event)
    return image


def _wrap_text(text: str, width: int) -> list[str]:
    """按显示宽度折行：中文字符算 2 列。"""
    lines: list[str] = []
    current = ""
    flat = str(text).replace(chr(10), " ")
    for char in flat:
        current += char
        if sum(2 if ord(c) > 0x2E80 else 1 for c in current) >= width * 2:
            lines.append(current)
            current = ""
    if current:
        lines.append(current)
    return lines or [""]


def _spotlight_metric(context: dict[str, Any], event: dict[str, Any]) -> tuple[str, str]:
    """挑一个能代表"此刻"的数字。没有就不画，不编数。"""
    remaining = context.get("remaining")
    if remaining is not None:
        return str(remaining), "剩余候选"
    teams = context.get("teamRemaining") or {}
    if teams.get("red") is not None and teams.get("blue") is not None:
        lead = "红队" if teams["red"] < teams["blue"] else "蓝队" if teams["blue"] < teams["red"] else "平"
        return f"{teams['red']}:{teams['blue']}", f"剩余词数 · {lead}"
    plays = context.get("plays") or []
    if plays:
        return str(len(plays)), "已落子"
    return "", ""


def _player_stat(context: dict[str, Any], name: str) -> tuple[str, str]:
    """单个选手的两个数：出手数与当前状态。全部来自事件统计。"""
    asks_by = context.get("asksBy") or {}
    count = asks_by.get(name, 0)
    winner = context.get("winner")
    state = "领先" if winner == name else ("已结束" if winner else "进行中")
    return str(count), f"出手数 · {state}"


def _draw_progress(draw, context: dict[str, Any], event: dict[str, Any]) -> None:
    draw.line([(60, 800), (WIDTH - 60, 800)], fill=LINE, width=2)
    total = max(1, context.get("total", 1))
    index = context.get("index", 0)
    width = (WIDTH - 120) * min(1.0, (index + 1) / total)
    draw.line([(60, 800), (60 + width, 800)], fill=ACCENT_A, width=3)
    draw.text((60, 820), f"{event['type']}  ·  seq {event['seq']}", font=_font(18), fill=FAINT)


_EFFECT_HEADLINES = {
    "answer": lambda e: f"问 {e.get('value')}\n答 {'是' if e.get('answer') == 'yes' else '否'}",
    "guess": lambda e: f"报出 {e.get('value')}",
    "hint": lambda e: f"给线索 {e.get('word')} {e.get('count')}",
    "reveal": lambda e: (
        f"翻到刺客 {e.get('word')}" if e.get("color") == "assassin"
        else f"翻 {e.get('value') or e.get('word')}（{e.get('color')}）"
    ),
    "action": lambda e: f"落子 {e.get('action')}",
}


def _effect_headline(effects: list[dict[str, Any]]) -> str | None:
    for effect in effects:
        formatter = _EFFECT_HEADLINES.get(str(effect.get("kind")))
        if formatter is not None:
            return formatter(effect)
    return None


def _state_headline(pub: dict[str, Any]) -> str:
    if "remainingPublic" in pub:
        return f"候选剩 {pub.get('remainingPublic')}"
    teams = pub.get("remaining")
    if isinstance(teams, dict):
        return f"红队剩 {teams.get('red', '—')} · 蓝队剩 {teams.get('blue', '—')}"
    return f"第 {pub.get('turnIndex', '—')} 步"


_SIMPLE_HEADLINES = {
    "action.proposed": lambda p, who: f"{who} {p.get('action', {}).get('kind')} {p.get('action', {}).get('value')}",
    "action.rejected": lambda p, who: f"{who} 犯规：{p.get('reason')}",
    "director.cue": lambda p, who: f"镜头 {p.get('shot')}",
    "match.ended": lambda p, who: f"比赛结束\n{p.get('reason', '')}",
    "state.changed": lambda p, who: _state_headline(p.get("publicState") or {}),
}


def _headline(kind: str, payload: dict[str, Any], player: str | None) -> str:
    if kind in ("narration.line", "thought.delta"):
        return str(payload.get("text", ""))
    if kind == "action.applied":
        return _applied_headline(payload, player)
    formatter = _SIMPLE_HEADLINES.get(kind)
    if formatter is not None:
        return formatter(payload, player)
    return kind


def _applied_headline(payload: dict[str, Any], player: str | None) -> str:
    action = payload.get("action", {})
    effect_text = _effect_headline(payload.get("effects") or [])
    if effect_text:
        return f"{player} {effect_text}"
    return f"{player} {action.get('kind')} {action.get('value')}"


def verify_renderable(clip: Clip) -> list[str]:
    """渲染前自检：没有事件就不该出片。"""
    problems = []
    if not clip.events:
        problems.append("剪辑没有事件")
    if clip.durationMs <= 0:
        problems.append("剪辑时长必须为正")
    return problems
