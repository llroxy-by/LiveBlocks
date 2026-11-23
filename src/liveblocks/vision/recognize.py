"""识别层编排：``Frame`` → ``MarkerCandidateList`` → ``CardCandidateList``。

这是 LB-003 对外的唯一入口。两层各自可以单独调用（``detect_markers`` /
``build_card_candidates``），合起来是协议 §1 里 ``vision.detector`` → ``vision.card`` 的那一步。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .cards import CardCandidateList, CardRegistry, build_card_candidates
from .frame import Frame, Intrinsics, load_frame
from .markers import MarkerCandidateList, detect_markers


@dataclass(frozen=True)
class RecognitionResult:
    """一帧的识别结果（两层对象都保留，便于调试与回归）。"""

    frame: Frame
    markers: MarkerCandidateList
    cards: CardCandidateList

    def to_dict(self) -> dict[str, Any]:
        """两层对象打包成一个审计对象（不属于协议层间对象，仅用于自检与报告）。"""
        return {
            "frame_id": self.frame.frame_id,
            "marker_candidate_list": self.markers.to_dict(),
            "card_candidate_list": self.cards.to_dict(),
        }


def recognize(
    frame: Frame,
    *,
    registry: CardRegistry | None = None,
    block: int = 21,
    offset: int = 8,
    max_bit_errors: int = 1,
) -> RecognitionResult:
    """对一帧做「标记检测 + 卡片映射」，返回两层候选列表。"""
    effective = registry or CardRegistry.load()
    markers = detect_markers(
        frame,
        aruco=effective.aruco,
        marker_size_mm=effective.marker_size_mm,
        block=block,
        offset=offset,
        max_bit_errors=max_bit_errors,
    )
    cards = build_card_candidates(frame, markers, effective)
    return RecognitionResult(frame=frame, markers=markers, cards=cards)


def recognize_path(
    path: Path,
    *,
    registry: CardRegistry | None = None,
    frame_id: str | None = None,
    source: str = "synthetic",
    intrinsics: Intrinsics | None = None,
    block: int = 21,
    offset: int = 8,
    max_bit_errors: int = 1,
) -> RecognitionResult:
    """读一张 PNG 灰度图并识别。"""
    frame = load_frame(path, frame_id=frame_id, source=source, intrinsics=intrinsics)
    return recognize(
        frame,
        registry=registry,
        block=block,
        offset=offset,
        max_bit_errors=max_bit_errors,
    )


__all__ = ["RecognitionResult", "recognize", "recognize_path"]
