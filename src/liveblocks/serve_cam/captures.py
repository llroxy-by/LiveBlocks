"""采集落盘：``fixtures/live/<时间戳>/`` 里的一份「实拍场景」+ 人工标注。

一个采集样本 = 一个**和 ``fixtures/scenes/<name>/`` 同构的目录**：

```
fixtures/live/20261124T045500Z-3f2a1c/          # outcome=accepted
├── frame.png        # 采集到的那一帧（8bit 灰度，仓库自带编码器）
├── scene.json       # kind=LiveCaptureScene：recognize report 读的就是它
├── capture.json     # kind=LiveCaptureRecord：人工标注 + 当时的 §3 识别结果 + 警告
└── index.json       # kind=LiveCaptureIndex：这份样本的清单行（图像尺寸/像素摘要/张数）
```

`recognize report --dir fixtures/live/<id>` 直接能跑，是因为 `scene.json` 提供的是
**和合成真值同一套字段**（``image.file`` / ``card_layout`` / ``cards[].marker_id`` /
``marker_corners_px`` / ``center_px`` / ``status``），只是 ``kind`` 换成
``LiveCaptureScene``（见 ``docs/DECISIONS.md`` D-021：不把实拍数据冒充成 ``SyntheticCardScene``）。

「这帧识别错了」的样本单独归档到 ``fixtures/live/refused/<id>/``（同构目录）：这样
``recognize report --dir fixtures/live/<id>`` 看的是人认为对的那批，坏样本单独可跑，
归档目录本身不会混进正常样本的统计。
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .. import __version__
from ..synth.geometry import apply_homography
from ..synth.png import encode_gray
from ..vision.cards import CardRegistry, card_geometry
from ..vision.pose import homography_from_marker_frame
from ..vision.protocol import CARD_STATUSES
from .frames import RequestError, StoredFrame
from .protocol import (
    CAPTURE_VERSION,
    CORNER_SOURCE_DETECTED,
    CORNER_SOURCE_MANUAL,
    CORNER_SOURCE_SINGLE_MARKER,
    FILE_FRAME,
    FILE_INDEX,
    FILE_RECORD,
    FILE_SCENE,
    KIND_CAPTURE,
    KIND_CAPTURE_INDEX,
    KIND_CAPTURE_SCENE,
    OUTCOMES,
    OUTCOME_ACCEPTED,
    OUTCOME_MISRECOGNISED,
    REFUSED_DIRNAME,
    SERVICE_NAME,
)

#: 采集 id 的形状：``20261124T045500Z-3f2a1c``（UTC 紧凑时间戳 + 6 位随机十六进制）。
CAPTURE_ID_PATTERN = re.compile(r"^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{6}$")
#: 采集 id 的宽松校验（读回已有样本时用于防目录穿越）。
SAFE_ID_PATTERN = re.compile(r"^[0-9A-Za-z._-]{1,64}$")

#: 采集格式版本（与 ``CAPTURE_VERSION`` 一起写进每个文件）。
SCHEMA_VERSION = "0.0.1"


@dataclass(frozen=True)
class Annotation:
    """人工标注：卡片 id、角色、可选角点（+ 可选备注与状态）。"""

    card_id: str
    role: str | None = None
    corners_px: tuple[tuple[float, float], ...] | None = None
    status: str | None = None
    notes: str | None = None

    def echo(self) -> dict[str, Any]:
        return {
            "card_id": self.card_id,
            "role": self.role,
            "corners_px": None if self.corners_px is None else [list(p) for p in self.corners_px],
            "status": self.status,
            "notes": self.notes,
        }


def _as_optional_text(raw: object, name: str) -> str | None:
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise RequestError("LB-C006", f"{name} 必须是字符串：{raw!r}")
    text = raw.strip()
    return text or None


def _parse_corners(raw: object, name: str) -> tuple[tuple[float, float], ...] | None:
    if raw is None or raw == "" or raw == []:
        return None
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RequestError("LB-C006", f"{name} 不是合法 JSON：{exc}") from None
    if not isinstance(raw, list) or len(raw) != 4:
        raise RequestError("LB-C006", f"{name} 必须是 4 个点（左上、右上、右下、左下）")
    points: list[tuple[float, float]] = []
    for index, point in enumerate(raw):
        if not isinstance(point, (list, tuple)) or len(point) != 2:
            raise RequestError("LB-C006", f"{name}[{index}] 必须是 [x, y]")
        try:
            x = float(point[0])
            y = float(point[1])
        except (TypeError, ValueError):
            raise RequestError("LB-C006", f"{name}[{index}] 的 x/y 必须是数字") from None
        points.append((x, y))
    return tuple(points)


def parse_annotations(payload: dict[str, Any]) -> tuple[list[Annotation], list[str]]:
    """从请求 JSON 里取人工标注：支持 ``annotations`` 列表，也支持 ``card_id``/``role``/``corners`` 简写。"""
    warnings: list[str] = []
    entries: list[Any] = []
    raw_list = payload.get("annotations")
    if raw_list is not None:
        if not isinstance(raw_list, list):
            raise RequestError("LB-C006", "annotations 必须是数组")
        entries.extend(raw_list)
    if payload.get("card_id") is not None:
        entries.append(
            {
                "card_id": payload.get("card_id"),
                "role": payload.get("role"),
                "corners_px": payload.get("corners_px", payload.get("corners")),
                "status": payload.get("status"),
                "notes": payload.get("annotation_notes"),
            }
        )

    annotations: list[Annotation] = []
    seen: set[str] = set()
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise RequestError("LB-C006", f"annotations[{index}] 必须是对象")
        card_id = _as_optional_text(entry.get("card_id"), f"annotations[{index}].card_id")
        if card_id is None:
            raise RequestError("LB-C006", f"annotations[{index}].card_id 不能为空")
        if card_id in seen:
            warnings.append(f"标注里 {card_id} 出现了两次：按多次标注保留，但统计会当两张卡")
        seen.add(card_id)
        role = _as_optional_text(entry.get("role"), f"annotations[{index}].role")
        status = _as_optional_text(entry.get("status"), f"annotations[{index}].status")
        if status is not None and status not in CARD_STATUSES:
            raise RequestError(
                "LB-C006",
                f"annotations[{index}].status={status!r} 不在 §3.2 的枚举里（{', '.join(CARD_STATUSES)}）",
            )
        annotations.append(
            Annotation(
                card_id=card_id,
                role=role,
                corners_px=_parse_corners(
                    entry.get("corners_px", entry.get("corners")), f"annotations[{index}].corners_px"
                ),
                status=status,
                notes=_as_optional_text(entry.get("notes"), f"annotations[{index}].notes"),
            )
        )
    if not annotations:
        raise RequestError(
            "LB-C006",
            "至少要给一条人工标注（card_id）；不想标注就别落盘，用 /api/frame 看识别结果",
        )
    return annotations, warnings


@dataclass(frozen=True)
class WrittenCard:
    """写进 ``scene.json`` 的一张卡（人工标注 + 从识别结果借来的位置）。"""

    card_id: str
    marker_id: int
    role: str | None
    card_type: str
    card_face: str
    status: str
    status_assumed: bool
    center_px: tuple[int, int]
    center_source: str
    marker_corners_px: tuple[tuple[float, float], ...]
    corners_px: tuple[tuple[float, float], ...] | None
    corner_source: str
    recognized_card_id: str | None
    recognized_status: str | None
    match: bool
    notes: str | None = None

    def to_truth(self) -> dict[str, Any]:
        return {
            "candidate_id": f"a-{self.card_id}",
            "card_id": self.card_id,
            "marker_id": self.marker_id,
            "card_type": self.card_type,
            "card_face": self.card_face,
            "role": self.role,
            "status": self.status,
            "status_assumed": self.status_assumed,
            "center_px": [int(self.center_px[0]), int(self.center_px[1])],
            "center_source": self.center_source,
            "marker_corners_px": [list(point) for point in self.marker_corners_px],
            "corners_px": None if self.corners_px is None else [list(p) for p in self.corners_px],
            "corner_source": self.corner_source,
        }

    def to_record(self) -> dict[str, Any]:
        entry = self.to_truth()
        entry.pop("candidate_id")
        entry["recognized_card_id"] = self.recognized_card_id
        entry["recognized_status"] = self.recognized_status
        entry["match"] = self.match
        entry["notes"] = self.notes
        entry["report_usable"] = True
        return entry


@dataclass(frozen=True)
class CaptureResult:
    """一次落盘的结果（``/api/capture`` 的响应体里的 ``capture``）。"""

    capture_id: str
    directory: str
    outcome: str
    created_at: str
    scene_id: str
    cards: tuple[WrittenCard, ...]
    rejected: tuple[dict[str, Any], ...]
    warnings: tuple[str, ...]
    image: dict[str, Any]
    files: dict[str, str] = field(default_factory=dict)

    @property
    def report_usable(self) -> bool:
        return bool(self.cards)

    def to_dict(self) -> dict[str, Any]:
        return {
            "capture_id": self.capture_id,
            "directory": self.directory,
            "outcome": self.outcome,
            "created_at": self.created_at,
            "scene_id": self.scene_id,
            "outcome_dir": "refused" if self.outcome == OUTCOME_MISRECOGNISED else "",
            "image": dict(self.image),
            "cards": [card.to_record() for card in self.cards],
            "rejected_annotations": [dict(entry) for entry in self.rejected],
            "warnings": list(self.warnings),
            "report_usable": self.report_usable,
            "recognize_report_command": (
                f"liveblocks recognize report --dir {self.directory}" if self.report_usable else None
            ),
            "files": dict(self.files),
        }


@dataclass(frozen=True)
class CaptureSummary:
    """``GET /api/captures`` 里的一行（读自已落盘的 ``capture.json``）。"""

    capture_id: str
    outcome: str
    created_at: str
    scene_id: str
    directory: str
    width: int
    height: int
    card_ids: tuple[str, ...]
    roles: tuple[str, ...]
    matches: tuple[bool, ...]
    recognized_card_ids: tuple[str | None, ...]
    report_usable: bool
    notes: str | None

    def to_dict(self) -> dict[str, Any]:
        base = f"/api/captures/{self.capture_id}"
        return {
            "capture_id": self.capture_id,
            "outcome": self.outcome,
            "created_at": self.created_at,
            "scene_id": self.scene_id,
            "directory": self.directory,
            "width": self.width,
            "height": self.height,
            "cards": len(self.card_ids),
            "card_ids": list(self.card_ids),
            "roles": list(self.roles),
            "matches": list(self.matches),
            "recognized_card_ids": list(self.recognized_card_ids),
            "report_usable": self.report_usable,
            "notes": self.notes,
            "frame_url": f"{base}/{FILE_FRAME}",
            "truth_url": f"{base}/{FILE_SCENE}",
            "record_url": f"{base}/{FILE_RECORD}",
        }


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _default_capture_id(moment: datetime) -> str:
    stamp = moment.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{secrets.token_hex(3)}"


class CaptureStore:
    """``fixtures/live`` 的读写（一个样本一个目录，refused 单独一个子目录）。"""

    def __init__(
        self,
        root: Path,
        *,
        registry: CardRegistry,
        id_factory: Callable[[datetime], str] | None = None,
    ) -> None:
        self.root = Path(root)
        self.registry = registry
        self._id_factory = id_factory or _default_capture_id

    # --- 写 -------------------------------------------------------------------

    def write(
        self,
        stored: StoredFrame,
        *,
        annotations: list[Annotation],
        outcome: str,
        notes: str | None,
        created_at: datetime,
        extra_warnings: tuple[str, ...] | list[str] = (),
    ) -> CaptureResult:
        """把一个采集样本落盘（图像 + 场景真值 + 采集记录 + 清单行）。"""
        if outcome not in OUTCOMES:
            raise RequestError("LB-C006", f"outcome={outcome!r} 不在 {', '.join(OUTCOMES)} 里")
        warnings = list(extra_warnings)
        cards, rejected = self._build_cards(stored, annotations, warnings, outcome)
        capture_id = self._id_factory(created_at)
        scene_id = capture_id
        parent = self.root / (REFUSED_DIRNAME if outcome == OUTCOME_MISRECOGNISED else "")
        directory = parent / capture_id
        frame = stored.frame
        png = encode_gray(frame.width, frame.height, frame.image.pixels)
        pixel_sha256 = hashlib.sha256(frame.image.pixels).hexdigest()
        image = {
            "file": FILE_FRAME,
            "mode": "L",
            "width": frame.width,
            "height": frame.height,
            "pixel_sha256": pixel_sha256,
        }
        record = {
            "schema_version": SCHEMA_VERSION,
            "kind": KIND_CAPTURE,
            "capture_version": CAPTURE_VERSION,
            "service": {"name": SERVICE_NAME, "package_version": __version__},
            "capture_id": capture_id,
            "created_at": _iso(created_at),
            "outcome": outcome,
            "notes": notes,
            "scene_id": scene_id,
            "frame": frame.to_dict(),
            "card_layout": stored.card_layout or self.registry.default_layout,
            "image": dict(image),
            "annotations": [card.to_record() for card in cards]
            + [dict(entry, report_usable=False) for entry in rejected],
            "card_candidate_list": stored.recognition.cards.to_dict(),
            "marker_candidate_list": stored.recognition.markers.to_dict(),
            "warnings": warnings,
        }
        truth = {
            "schema_version": SCHEMA_VERSION,
            "kind": KIND_CAPTURE_SCENE,
            "capture_version": CAPTURE_VERSION,
            "scene_id": scene_id,
            "capture_id": capture_id,
            "created_at": record["created_at"],
            "outcome": outcome,
            "description": self._description(cards, rejected, outcome),
            "generator": {
                "name": "liveblocks.serve_cam",
                "package_version": __version__,
                "capture_version": CAPTURE_VERSION,
            },
            "image": dict(image),
            "card_layout": record["card_layout"],
            "cards": [card.to_truth() for card in cards],
        }
        index = {
            "schema_version": SCHEMA_VERSION,
            "kind": KIND_CAPTURE_INDEX,
            "capture_version": CAPTURE_VERSION,
            "capture_id": capture_id,
            "scene_id": scene_id,
            "created_at": record["created_at"],
            "outcome": outcome,
            "scene": {
                "scene_id": scene_id,
                "image": FILE_FRAME,
                "ground_truth": FILE_SCENE,
                "width": frame.width,
                "height": frame.height,
                "cards": len(cards),
                "pixel_sha256": pixel_sha256,
            },
            "recognize_report_command": (
                f"liveblocks recognize report --dir {self._display_dir(directory)}" if cards else None
            ),
        }
        try:
            directory.mkdir(parents=True, exist_ok=False)
            (directory / FILE_FRAME).write_bytes(png)
            (directory / FILE_SCENE).write_text(
                json.dumps(truth, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            (directory / FILE_RECORD).write_text(
                json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            (directory / FILE_INDEX).write_text(
                json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
        except OSError as exc:
            raise RequestError("LB-C009", f"写采集目录失败（{directory}）：{exc}") from None
        return CaptureResult(
            capture_id=capture_id,
            directory=self._display_dir(directory),
            outcome=outcome,
            created_at=str(record["created_at"]),
            scene_id=scene_id,
            cards=tuple(cards),
            rejected=tuple(rejected),
            warnings=tuple(warnings),
            image=image,
            files={
                "frame.png": self._display_dir(directory / FILE_FRAME),
                "scene.json": self._display_dir(directory / FILE_SCENE),
                "capture.json": self._display_dir(directory / FILE_RECORD),
                "index.json": self._display_dir(directory / FILE_INDEX),
            },
        )

    def _display_dir(self, path: Path) -> str:
        """尽量给出相对仓库根的可读路径（真正下载/落盘的仍是绝对路径）。"""
        try:
            return str(Path(path).resolve().relative_to(Path.cwd().resolve()))
        except ValueError:
            return str(path)

    @staticmethod
    def _description(
        cards: list[WrittenCard], rejected: list[dict[str, Any]], outcome: str
    ) -> str:
        matched = sum(1 for card in cards if card.match)
        label = "人判识别错误" if outcome == OUTCOME_MISRECOGNISED else "人判可用"
        text = (
            f"实拍采集（{outcome}，{label}）：人工标注 {len(cards) + len(rejected)} 张，"
            f"写进真值 {len(cards)} 张，与识别一致 {matched} 张"
        )
        if rejected:
            text += f"，无法定位 {len(rejected)} 张（未写进真值）"
        return text

    def _build_cards(
        self,
        stored: StoredFrame,
        annotations: list[Annotation],
        warnings: list[str],
        outcome: str,
    ) -> tuple[list[WrittenCard], list[dict[str, Any]]]:
        """把人工标注 + 识别结果合成真值卡片；定位不到的四角如实拒收（不猜坐标）。"""
        marker_positions = {
            marker.marker_id: marker
            for marker in stored.recognition.markers.markers
            if marker.marker_id is not None
        }
        candidate_by_marker: dict[str, Any] = {}
        for candidate in stored.recognition.cards.cards:
            for marker_id in candidate.marker_ids:
                candidate_by_marker.setdefault(marker_id, candidate)

        cards: list[WrittenCard] = []
        rejected: list[dict[str, Any]] = []
        for annotation in annotations:
            entry = self.registry.card_dictionary.by_id.get(annotation.card_id)
            if entry is None:
                rejected.append(
                    {
                        **annotation.echo(),
                        "reason": "卡片 id 不在 data/card_dictionary.json 里：没有 marker_id 就无法写进 §3 真值",
                    }
                )
                warnings.append(
                    f"{annotation.card_id}：不在卡片字典里，样本已存档但没写进 scene.json（recognize report 读不到）"
                )
                continue
            marker = marker_positions.get(entry.marker_id)
            corners = annotation.corners_px
            corner_source: str | None = None
            if corners is not None:
                corner_source = CORNER_SOURCE_MANUAL
            elif marker is not None:
                corners = tuple((float(x), float(y)) for x, y in marker.corners_px)
                corner_source = CORNER_SOURCE_DETECTED
            elif len(annotations) == 1 and len(marker_positions) == 1:
                only = next(iter(marker_positions.values()))
                corners = tuple((float(x), float(y)) for x, y in only.corners_px)
                corner_source = CORNER_SOURCE_SINGLE_MARKER
                warnings.append(
                    f"{annotation.card_id}：标记 {entry.marker_id} 没被检出，"
                    f"借用了画面里唯一一个标记候选（检测读成 {only.marker_id}）的角点；"
                    "位置仅供参考，四角请人工复核"
                )
            if corners is None:
                rejected.append(
                    {
                        **annotation.echo(),
                        "reason": (
                            f"标记 {entry.marker_id} 没被检出，也没给 corners_px："
                            "没有四角就无法写进 §3 真值（不猜坐标）"
                        ),
                    }
                )
                warnings.append(
                    f"{annotation.card_id}：缺四角，样本已存档但没写进 scene.json；"
                    "要把它纳入 recognize report，请在该卡可见时补拍或手填 corners_px"
                )
                continue

            center, center_source = self._center(annotation.card_id, corners)
            if center[0] < 0 or center[1] < 0 or center[0] >= stored.frame.width or center[1] >= stored.frame.height:
                warnings.append(
                    f"{annotation.card_id}：标注的中心点 {center} 落在画面之外，请确认角点是否填错"
                )
            candidate = candidate_by_marker.get(str(entry.marker_id))
            recognized_card_id = None if candidate is None else str(candidate.card_id)
            recognized_status = None if candidate is None else str(candidate.status)
            match = recognized_card_id == annotation.card_id
            status_assumed = annotation.status is None
            status = annotation.status or "ok"
            if status_assumed and outcome == OUTCOME_MISRECOGNISED:
                warnings.append(
                    f"{annotation.card_id}：真值 status 按 §3.2 记 {status!r}（未由人确认，"
                    "status_assumed=true），因为缺省值不随识别结果变化"
                )
            role_mismatch = (
                annotation.role is not None
                and annotation.role not in {"unknown"}
                and annotation.role != entry.card_type
            )
            if role_mismatch:
                warnings.append(
                    f"{annotation.card_id}：人工标注的角色 {annotation.role!r} 与字典里的 "
                    f"card_type={entry.card_type!r} 不一致；真值按字典记 card_type"
                )
            cards.append(
                WrittenCard(
                    card_id=annotation.card_id,
                    marker_id=entry.marker_id,
                    role=annotation.role,
                    card_type=entry.card_type,
                    card_face=entry.card_face,
                    status=status,
                    status_assumed=status_assumed,
                    center_px=center,
                    center_source=center_source,
                    marker_corners_px=corners,
                    corners_px=annotation.corners_px,
                    corner_source=corner_source or CORNER_SOURCE_MANUAL,
                    recognized_card_id=recognized_card_id,
                    recognized_status=recognized_status,
                    match=match,
                    notes=annotation.notes,
                )
            )

        matched = sum(1 for card in cards if card.match)
        if cards and matched < len(cards):
            warnings.append(
                f"{len(cards) - matched} 张标注与识别结果不一致："
                "样本按 outcome 归档，统计以 recognize report 的漏检/误检为准"
            )
        return cards, rejected

    #: 真值中心的两条来源（写进 ``scene.json`` 的 ``center_source``）。
    CENTER_FROM_LAYOUT = "card-geometry-from-marker-quad"
    CENTER_FROM_QUAD = "marker-quad-centroid"

    def _center(
        self, card_id: str, corners: tuple[tuple[float, float], ...]
    ) -> tuple[tuple[int, int], str]:
        """实拍样本没有独立测量的卡片中心：按「标记四边形 + 卡面版式」外推卡片中心。

        这不是独立测量（同一份几何也喂给了识别层），所以 ``scene.json`` 里记
        ``center_source``；拿它算出来的「卡片中心误差」只说明几何自洽，不说明拍摄精度。
        外推失败（退化的四边形）时退回标记四边形质心，并如实换一个 ``center_source``。
        """
        layout = self.registry.layout_for(card_id)
        try:
            matrix = homography_from_marker_frame([tuple(p) for p in corners], layout.marker_size_mm)
            _mm_corners, mm_center = card_geometry(layout, self.registry.quadrant_for(card_id))
            center = apply_homography(matrix, mm_center[0], mm_center[1])
            return (int(round(center[0])), int(round(center[1]))), self.CENTER_FROM_LAYOUT
        except (ValueError, ZeroDivisionError):
            cx = sum(point[0] for point in corners) / 4.0
            cy = sum(point[1] for point in corners) / 4.0
            return (int(round(cx)), int(round(cy))), self.CENTER_FROM_QUAD

    # --- 读 -------------------------------------------------------------------

    def resolve_directory(self, capture_id: str) -> Path:
        """按 id 找样本目录（accepted 优先，其次 refused）；非法 id 直接 404。"""
        if not SAFE_ID_PATTERN.match(capture_id or ""):
            raise RequestError("LB-C008", f"采集 id 不合法：{capture_id!r}")
        for candidate in (self.root / capture_id, self.root / REFUSED_DIRNAME / capture_id):
            if candidate.is_dir():
                return candidate
        raise RequestError("LB-C008", f"没有这个采集样本：{capture_id}")

    def list(self) -> tuple[CaptureSummary, ...]:
        """列出全部已采样本（accepted + refused），按创建时间倒序。"""
        summaries: list[CaptureSummary] = []
        for directory in self._iter_directories():
            summary = self._summarize(directory)
            if summary is not None:
                summaries.append(summary)
        summaries.sort(key=lambda item: (item.created_at, item.capture_id), reverse=True)
        return tuple(summaries)

    def _iter_directories(self) -> list[Path]:
        if not self.root.is_dir():
            return []
        directories = [path for path in sorted(self.root.iterdir()) if path.is_dir()]
        refused = self.root / REFUSED_DIRNAME
        if refused.is_dir():
            directories.extend(path for path in sorted(refused.iterdir()) if path.is_dir())
        return directories

    def _summarize(self, directory: Path) -> CaptureSummary | None:
        record_path = directory / FILE_RECORD
        truth_path = directory / FILE_SCENE
        if not record_path.is_file():
            return None
        try:
            record = json.loads(record_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        scene_id = str(record.get("scene_id") or record.get("capture_id") or directory.name)
        cards = [entry for entry in record.get("annotations", []) if entry.get("report_usable", True)]
        image = record.get("image") or {}
        report_usable = truth_path.is_file() and bool(cards)
        return CaptureSummary(
            capture_id=str(record.get("capture_id") or directory.name),
            outcome=str(record.get("outcome") or OUTCOME_ACCEPTED),
            created_at=str(record.get("created_at") or ""),
            scene_id=scene_id,
            directory=self._display_dir(directory),
            width=int(image.get("width") or 0),
            height=int(image.get("height") or 0),
            card_ids=tuple(str(entry.get("card_id")) for entry in cards),
            roles=tuple(str(entry.get("role")) for entry in cards),
            matches=tuple(bool(entry.get("match")) for entry in cards),
            recognized_card_ids=tuple(
                None if entry.get("recognized_card_id") is None else str(entry.get("recognized_card_id"))
                for entry in cards
            ),
            report_usable=report_usable,
            notes=record.get("notes"),
        )


__all__ = [
    "CAPTURE_ID_PATTERN",
    "SAFE_ID_PATTERN",
    "Annotation",
    "CaptureResult",
    "CaptureStore",
    "CaptureSummary",
    "SCHEMA_VERSION",
    "WrittenCard",
    "parse_annotations",
]
