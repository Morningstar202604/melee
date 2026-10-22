"""高光导出：把事件流切成一段可播放的剪辑清单。

先做"剪什么"，不做"渲染成什么"——剪辑清单是纯数据，
前端或后续的 ffmpeg / Remotion 都能直接吃。这比先绑定渲染器更稳。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .recap import find_highlights


@dataclass
class Clip:
    """一段高光：以某个事件为中心，向两侧各扩若干事件。"""

    kind: str
    title: str
    detail: str
    startSeq: int
    endSeq: int
    events: list[dict[str, Any]] = field(default_factory=list)
    durationMs: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "title": self.title,
            "detail": self.detail,
            "startSeq": self.startSeq,
            "endSeq": self.endSeq,
            "eventCount": len(self.events),
            "durationMs": self.durationMs,
        }


def build_clips(
    events: list[dict[str, Any]],
    *,
    limit: int = 3,
    before: int = 2,
    after: int = 3,
    per_event_ms: int = 900,
) -> list[Clip]:
    """挑转折点，各取前后若干条事件，拼成剪辑清单。"""
    highlights = find_highlights(events, limit=limit)
    by_seq = {event["seq"]: index for index, event in enumerate(events)}
    clips: list[Clip] = []
    used: set[int] = set()

    for highlight in highlights:
        anchor = by_seq.get(highlight.seq)
        if anchor is None:
            continue
        start = max(0, anchor - before)
        end = min(len(events), anchor + after + 1)
        if any(index in used for index in range(start, end)):
            continue
        used.update(range(start, end))
        window = events[start:end]
        clips.append(
            Clip(
                kind=highlight.kind,
                title=highlight.title,
                detail=highlight.detail,
                startSeq=window[0]["seq"],
                endSeq=window[-1]["seq"],
                events=window,
                durationMs=len(window) * per_event_ms,
            )
        )

    return clips


def export_manifest(
    match_id: str,
    events: list[dict[str, Any]],
    *,
    limit: int = 3,
    per_event_ms: int = 900,
) -> dict[str, Any]:
    """给前端/渲染器的完整剪辑清单。"""
    clips = build_clips(events, limit=limit, per_event_ms=per_event_ms)
    return {
        "matchId": match_id,
        "clipCount": len(clips),
        "totalMs": sum(clip.durationMs for clip in clips),
        "clips": [clip.as_dict() for clip in clips],
    }


def verify_clip(clip: Clip, events: list[dict[str, Any]]) -> list[str]:
    """剪辑自检：窗口内的事件必须与源事件逐条相同，且序号连续。"""
    problems: list[str] = []
    source = {event["seq"]: event for event in events}
    for event in clip.events:
        original = source.get(event["seq"])
        if original is None:
            problems.append(f"第 {event['seq']} 条事件在源流里不存在")
        elif original["type"] != event["type"]:
            problems.append(f"第 {event['seq']} 条事件类型不一致")
    seqs = [event["seq"] for event in clip.events]
    if seqs and seqs != list(range(seqs[0], seqs[0] + len(seqs))):
        problems.append("剪辑窗口内的事件序号不连续")
    return problems
