"""准确率 / 漏检统计：把识别结果与 LB-002 的真值逐场景对齐，输出可读报表。

口径（写进 ``docs/PROGRESS.md`` 与 PR 描述，避免「准确率」含糊）：

- **预期（expected）**：真值里的卡片数（每张卡一个标记）。
- **识别（identified）**：输出了该 ``marker_id`` 且没被判 ``unknown_card`` 的候选。
- **未识别（unidentified）**：形状检出但码字不可靠（``occluded`` / ``ambiguous``），且位置落在某张
  真值卡的标记区域附近——「看见了但读不出来」。
- **漏检（missed）**：真值里有、输出里既没有识别也没有未识别候选的卡。
- **误检（extra）**：输出了真值里不存在的 ``marker_id``。
- **召回率** = 识别 / 预期；**精确率** = 识别 / (识别 + 误检)。
- **状态一致**：识别出的卡其 ``status`` 与真值 ``status`` 一致的数量（真值只有
  ``ok|clipped|occluded``；``unknown_card``/``ambiguous`` 属识别层结论，真值不会有）。
- **标记角点误差 / 卡片中心误差**：检测角点与真值 ``marker_corners_px`` 的距离，以及外推的卡片
  中心与真值 ``center_px`` 的距离（均值 / 最大值，单位像素）。

已知偏差不做粉饰：例如 LB-002 的 ``overlap`` 场景有一张卡被纸片压住，标记码字不可靠，本实现会
把它算作「未识别」而不是「识别成功」；被别的卡压住但标记完好的卡，识别层只看得到标记，因此
会报 ``ok`` 而真值是 ``occluded``（卡片级遮挡需要 LB-004 之后的分簇信息，见 PROGRESS 已知限制）。
"""

from __future__ import annotations

import json
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .cards import CardRegistry
from .frame import Intrinsics, load_frame
from .image import Point, point_in_quad
from .recognize import RecognitionResult, recognize

#: 判定「未识别候选落在哪张真值卡上」时，标记四边形的放大比例。
ATTRIBUTION_MARGIN = 0.35

#: 能进 :func:`evaluate_directory` 的真值 ``kind``。
#:
#: ``SyntheticCardScene`` 是 LB-002 的合成场景；``LiveCaptureScene`` 是 LB-030 采集台写下的
#: **实拍**场景（``fixtures/live/<时间戳>/scene.json``）——两者字段同构（``image.file`` /
#: ``card_layout`` / ``cards[].marker_id`` / ``marker_corners_px`` / ``center_px`` / ``status``），
#: 所以同一份统计代码可以直接吃实拍样本。之所以不用同一个 ``kind``：合成图与实拍数据的可信度
#: 完全不同，文件名/字段里必须能分清（见 ``docs/DECISIONS.md`` D-021）。
SCENE_KINDS: tuple[str, ...] = ("SyntheticCardScene", "LiveCaptureScene")


@dataclass(frozen=True)
class SceneEvaluation:
    """一个场景的识别统计。"""

    scene_id: str
    image: str
    cluster: str
    expected: int
    identified: int
    unidentified: int
    missed: tuple[str, ...]
    extra: tuple[str, ...]
    status_ok: int
    status_mismatch: tuple[str, ...]
    marker_corner_error_mean: float
    marker_corner_error_max: float
    card_center_error_mean: float
    card_center_error_max: float
    status_by_card: dict[str, str] = field(default_factory=dict)
    notes: tuple[str, ...] = ()

    @property
    def recall(self) -> float:
        return 0.0 if self.expected == 0 else self.identified / self.expected

    @property
    def precision(self) -> float:
        denominator = self.identified + len(self.extra)
        return 0.0 if denominator == 0 else self.identified / denominator

    def to_dict(self) -> dict[str, Any]:
        return {
            "scene_id": self.scene_id,
            "image": self.image,
            "cluster": self.cluster,
            "expected": self.expected,
            "identified": self.identified,
            "unidentified": self.unidentified,
            "missed": list(self.missed),
            "extra": list(self.extra),
            "status_ok": self.status_ok,
            "status_mismatch": list(self.status_mismatch),
            "recall": round(self.recall, 4),
            "precision": round(self.precision, 4),
            "marker_corner_error_mean": round(self.marker_corner_error_mean, 3),
            "marker_corner_error_max": round(self.marker_corner_error_max, 3),
            "card_center_error_mean": round(self.card_center_error_mean, 3),
            "card_center_error_max": round(self.card_center_error_max, 3),
            "status_by_card": dict(self.status_by_card),
            "notes": list(self.notes),
        }


@dataclass(frozen=True)
class EvaluationReport:
    """一次目录级评估的总表。"""

    directory: str
    scenes: tuple[SceneEvaluation, ...] = ()

    @property
    def expected(self) -> int:
        return sum(scene.expected for scene in self.scenes)

    @property
    def identified(self) -> int:
        return sum(scene.identified for scene in self.scenes)

    @property
    def unidentified(self) -> int:
        return sum(scene.unidentified for scene in self.scenes)

    @property
    def missed(self) -> int:
        return sum(len(scene.missed) for scene in self.scenes)

    @property
    def extra(self) -> int:
        return sum(len(scene.extra) for scene in self.scenes)

    @property
    def recall(self) -> float:
        return 0.0 if self.expected == 0 else self.identified / self.expected

    @property
    def precision(self) -> float:
        denominator = self.identified + self.extra
        return 0.0 if denominator == 0 else self.identified / denominator

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": "RecognitionAccuracyReport",
            "directory": self.directory,
            "totals": {
                "expected": self.expected,
                "identified": self.identified,
                "unidentified": self.unidentified,
                "missed": self.missed,
                "extra": self.extra,
                "recall": round(self.recall, 4),
                "precision": round(self.precision, 4),
            },
            "scenes": [scene.to_dict() for scene in self.scenes],
        }


def _distance(a: Point, b: Point) -> float:
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5


def _expanded(quad: list[Point], margin: float) -> list[Point]:
    cx = sum(point[0] for point in quad) / 4.0
    cy = sum(point[1] for point in quad) / 4.0
    return [
        (cx + (point[0] - cx) * (1.0 + margin), cy + (point[1] - cy) * (1.0 + margin))
        for point in quad
    ]


def _truth_marker_quads(truth: dict[str, Any]) -> dict[int, list[Point]]:
    quads: dict[int, list[Point]] = {}
    for card in truth.get("cards", []):
        quads[int(card["marker_id"])] = [tuple(point) for point in card["marker_corners_px"]]
    return quads


def evaluate_scene(
    image_path: Path,
    truth: dict[str, Any],
    *,
    registry: CardRegistry | None = None,
    intrinsics: Intrinsics | None = None,
) -> tuple[SceneEvaluation, RecognitionResult]:
    """评估单个场景，返回（统计行, 原始识别结果）。"""
    frame = load_frame(
        image_path,
        frame_id=f"f-{truth.get('scene_id', image_path.stem)}",
        intrinsics=intrinsics,
    )
    result = recognize(frame, registry=registry)
    evaluation = _evaluate_result(truth, image_path.name, result)
    return evaluation, result


def _evaluate_result(
    truth: dict[str, Any], image_name: str, result: RecognitionResult
) -> SceneEvaluation:
    cards = truth.get("cards", [])
    expected_markers = [int(card["marker_id"]) for card in cards]
    quads = _truth_marker_quads(truth)

    identified_markers: list[str] = []
    unidentified_candidates = [
        candidate
        for candidate in result.cards.cards
        if candidate.status in {"occluded", "ambiguous"} and not candidate.marker_ids
    ]
    used_unidentified: set[str] = set()

    missed: list[str] = []
    status_ok = 0
    status_mismatch: list[str] = []
    status_by_card: dict[str, str] = {}
    marker_errors: list[float] = []
    center_errors: list[float] = []
    notes: list[str] = []

    marker_by_id = {
        marker.marker_id: marker
        for marker in result.markers.markers
        if marker.marker_id is not None
    }
    card_by_marker: dict[str, Any] = {}
    for candidate in result.cards.cards:
        for marker_id in candidate.marker_ids:
            card_by_marker.setdefault(marker_id, candidate)

    for truth_card in cards:
        marker_id = int(truth_card["marker_id"])
        key = str(marker_id)
        candidate = card_by_marker.get(key)
        if candidate is not None and candidate.status != "unknown_card":
            identified_markers.append(key)
            marker = marker_by_id.get(marker_id)
            if marker is not None:
                truth_corners = [tuple(point) for point in truth_card["marker_corners_px"]]
                marker_errors.append(
                    max(_distance(a, b) for a, b in zip(marker.corners_px, truth_corners))
                )
            center_errors.append(
                _distance(candidate.center_px, tuple(truth_card["center_px"]))
            )
            status_by_card[str(truth_card["card_id"])] = candidate.status
            if candidate.status == truth_card["status"]:
                status_ok += 1
            else:
                status_mismatch.append(
                    f"{truth_card['card_id']}: 识别={candidate.status} 真值={truth_card['status']}"
                )
            continue
        attributed = _attribute_unidentified(
            quads.get(marker_id), unidentified_candidates, used_unidentified
        )
        if attributed is not None:
            used_unidentified.add(attributed.candidate_id)
            status_by_card[str(truth_card["card_id"])] = attributed.status
            notes.append(
                f"{truth_card['card_id']}（标记 {marker_id}）检出但不可识别 → 候选 "
                f"{attributed.candidate_id} 报 {attributed.status}"
            )
            continue
        status_by_card[str(truth_card["card_id"])] = "missing"
        missed.append(key)

    extra = [
        marker_id
        for marker_id in identified_markers
        if int(marker_id) not in expected_markers
    ]

    return SceneEvaluation(
        scene_id=str(truth.get("scene_id", "")),
        image=image_name,
        cluster=str(truth.get("description", "")),
        expected=len(cards),
        identified=len(identified_markers),
        unidentified=len(used_unidentified),
        missed=tuple(missed),
        extra=tuple(extra),
        status_ok=status_ok,
        status_mismatch=tuple(status_mismatch),
        marker_corner_error_mean=_mean(marker_errors),
        marker_corner_error_max=max(marker_errors) if marker_errors else 0.0,
        card_center_error_mean=_mean(center_errors),
        card_center_error_max=max(center_errors) if center_errors else 0.0,
        status_by_card=status_by_card,
        notes=tuple(notes),
    )


def _attribute_unidentified(quad: list[Point] | None, candidates: list[Any], used: set[str]) -> Any:
    if quad is None:
        return None
    expanded = _expanded(quad, ATTRIBUTION_MARGIN)
    for candidate in candidates:
        if candidate.candidate_id in used:
            continue
        if point_in_quad(tuple(candidate.center_px), expanded):
            return candidate
    return None


def _mean(values: list[float]) -> float:
    return 0.0 if not values else sum(values) / len(values)


def _registry_for_scene(
    truth: dict[str, Any], base: CardRegistry | None, layout_override: str | None
) -> CardRegistry:
    """按场景的卡面版式（采集配置）建注册表；``layout_override`` 优先。"""
    layout = layout_override or truth.get("card_layout")
    if base is None:
        return CardRegistry.load(capture_layout=layout)
    return base.with_capture_layout(layout_override or layout)


def evaluate_directory(
    directory: Path,
    *,
    registry: CardRegistry | None = None,
    layout_override: str | None = None,
    intrinsics_from_truth: bool = True,
) -> EvaluationReport:
    """对目录里的全部场景跑识别并与真值比对。

    ``intrinsics_from_truth``：真值里的 ``camera`` 块是**采集配置**（相当于标定文件），默认拿它
    当内参以顺便评估位姿；置 ``False`` 则完全不给内参（输出里就没有 ``pose``）。
    """
    directory = Path(directory)
    scenes: list[SceneEvaluation] = []
    for truth_path in sorted(directory.glob("*.json")):
        if truth_path.name == "index.json":
            continue
        truth = json.loads(truth_path.read_text(encoding="utf-8"))
        if truth.get("kind") not in SCENE_KINDS:
            continue
        image_path = directory / str(truth["image"]["file"])
        scene_registry = _registry_for_scene(truth, registry, layout_override)
        intrinsics = None
        if intrinsics_from_truth and "camera" in truth:
            camera = truth["camera"]
            intrinsics = Intrinsics(
                fx=float(camera["fx"]),
                fy=float(camera["fy"]),
                cx=float(camera["cx"]),
                cy=float(camera["cy"]),
            )
        evaluation, _result = evaluate_scene(
            image_path, truth, registry=scene_registry, intrinsics=intrinsics
        )
        scenes.append(evaluation)
    return EvaluationReport(directory=str(directory), scenes=tuple(scenes))


def _display_width(text: str) -> int:
    """终端显示宽度：CJK 全角字符按 2 列计（报表里有中文表头）。"""
    width = 0
    for char in text:
        width += 2 if unicodedata.east_asian_width(char) in {"W", "F", "A"} else 1
    return width


def _pad(text: str, width: int, *, align: str = "left") -> str:
    padding = max(0, width - _display_width(text))
    if align == "right":
        return " " * padding + text
    return text + " " * padding


def _acceptance_lines(report: EvaluationReport) -> list[str]:
    """按 Issue #9 的验收条款逐条对照（口径写死，避免「看起来还行」）。"""
    by_id = {scene.scene_id: scene for scene in report.scenes}
    lines = ["验收对照（Issue #9）："]
    normal = by_id.get("normal")
    if normal is None:
        lines.append("  - 「正常」样例：目录里没有 normal 场景，未核对")
    else:
        ok = not normal.missed and normal.identified == normal.expected
        lines.append(
            f"  - 「正常」样例无漏检：{'通过' if ok else '未通过'}"
            f"（预期 {normal.expected}，识别 {normal.identified}，漏检 {len(normal.missed)}）"
        )
    clipped = by_id.get("clipped")
    if clipped is None:
        lines.append("  - 「裁切」样例标 clipped：目录里没有 clipped 场景，未核对")
    else:
        statuses = [clipped.status_by_card.get(card_id, "missing") for card_id in clipped.status_by_card]
        ok = bool(statuses) and all(status == "clipped" for status in statuses)
        lines.append(
            f"  - 「裁切」样例标 clipped：{'通过' if ok else '未通过'}（实际 status={statuses}）"
        )
    lines.append(
        "  - 未登记标记标 unknown_card：由 recognize detect --card-dictionary 与 "
        "tests/test_vision_cards.py 覆盖（本报表的卡片字典登记了全部 fixtures 标记）"
    )
    return lines


def format_report(report: EvaluationReport) -> str:
    """人读报表（按显示宽度对齐，含验收对照与漏检明细）。"""
    name_width = 16
    headers = (
        ("场景", name_width, "left"),
        ("预期", 4, "right"),
        ("识别", 5, "right"),
        ("未识别", 7, "right"),
        ("漏检", 5, "right"),
        ("误检", 5, "right"),
        ("状态一致", 9, "right"),
        ("标记角点误差(px)", 18, "right"),
        ("卡片中心误差(px)", 18, "right"),
    )
    lines = [
        f"识别统计（LB-003）：{len(report.scenes)} 个场景，预期 {report.expected} 张卡",
        "",
        "".join(_pad(text, width, align=align) for text, width, align in headers),
        "-" * 96,
    ]
    for scene in report.scenes:
        row = (
            _pad(scene.scene_id, name_width)
            + _pad(str(scene.expected), 4, align="right")
            + _pad(str(scene.identified), 5, align="right")
            + _pad(str(scene.unidentified), 7, align="right")
            + _pad(str(len(scene.missed)), 5, align="right")
            + _pad(str(len(scene.extra)), 5, align="right")
            + _pad(f"{scene.status_ok}/{scene.identified}", 9, align="right")
            + _pad(
                f"{scene.marker_corner_error_mean:.2f}/{scene.marker_corner_error_max:.2f}",
                18,
                align="right",
            )
            + _pad(
                f"{scene.card_center_error_mean:.2f}/{scene.card_center_error_max:.2f}",
                18,
                align="right",
            )
        )
        lines.append(row)
    lines.append("-" * 96)
    lines.append(
        _pad("合计", name_width)
        + _pad(str(report.expected), 4, align="right")
        + _pad(str(report.identified), 5, align="right")
        + _pad(str(report.unidentified), 7, align="right")
        + _pad(str(report.missed), 5, align="right")
        + _pad(str(report.extra), 5, align="right")
    )
    lines.append("")
    lines.append(
        f"召回率（识别/预期）：{report.recall * 100:.1f}%    "
        f"精确率（识别/(识别+误检)）：{report.precision * 100:.1f}%"
    )
    lines.append("")
    lines.extend(_acceptance_lines(report))
    detail_lines: list[str] = []
    for scene in report.scenes:
        if scene.missed:
            detail_lines.append(f"  漏检 {scene.scene_id}: 标记 {', '.join(scene.missed)}")
        if scene.extra:
            detail_lines.append(f"  误检 {scene.scene_id}: 标记 {', '.join(scene.extra)}")
        for note in scene.notes:
            detail_lines.append(f"  {scene.scene_id}: {note}")
        for mismatch in scene.status_mismatch:
            detail_lines.append(f"  状态差异 {scene.scene_id}: {mismatch}")
    if detail_lines:
        lines.append("")
        lines.append("明细：")
        lines.extend(detail_lines)
    return "\n".join(lines) + "\n"


__all__ = [
    "SCENE_KINDS",
    "EvaluationReport",
    "SceneEvaluation",
    "evaluate_directory",
    "evaluate_scene",
    "format_report",
]
