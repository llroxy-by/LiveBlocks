"""标定文件（JSON）的格式、校验与读写。

一份标定文件描述「某个镜头在某个图像尺寸下的几何模型」，字段分三组：

1. **模型**：``intrinsics``（``fx, fy, cx, cy``）+ ``distortion``（``plumb_bob`` 的
   ``k1, k2, p1, p2``），与 ``docs/PROTOCOL.md`` §2 的 ``Frame.intrinsics/distortion`` 同形，
   因此可以直接填进帧对象（见 :func:`liveblocks.calib.apply_to_frame`）。
2. **标定过程的元数据**：``image``（图像尺寸）、``image_count``（用了多少张标定图）、
   ``point_count``、``created_at``（时间戳）、``target``（板定义）、``reprojection_error``
   （重投影误差统计）、``views``（每个视图的角点与残差）。
3. **来源**：``source`` 说明这是合成场景还是真实采集，``notes`` 里放已知限制。

``created_at`` 由调用方显式给出（CLI 默认取当前 UTC 时间），这样「同一份数据 + 同一时间戳」
必然产出字节一致的 JSON，可以当 golden 快照入库。

校验（:func:`validate_calibration`）只做形状与范围检查，不判断标定质量；质量问题由
``liveblocks calibrate report`` 的验收行负责。
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..synth.calibtarget import CalibTarget
from ..synth.lens import LensModel
from .codes import diagnostic

SCHEMA_VERSION = "0.0.1"
KIND = "Calibration"

#: 允许的 ``source.kind``。
SOURCE_KINDS = ("synthetic", "capture")

#: 畸变模型名（当前只支持 ``plumb_bob``）。
DISTORTION_MODELS = ("plumb_bob",)


class CalibrationFileError(ValueError):
    """标定文件不合格（字段缺失、类型不符、数值越界）。"""

    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__("标定文件不合格：" + "；".join(problems))


@dataclass(frozen=True)
class ReprojectionError:
    """重投影误差（像素）：整体 RMS / 最大值 + 每个视图的 RMS。"""

    rms_px: float
    max_px: float
    per_view_rms_px: tuple[float, ...] = ()
    point_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "rms_px": round(self.rms_px, 6),
            "max_px": round(self.max_px, 6),
            "point_count": self.point_count,
            "per_view_rms_px": [round(value, 6) for value in self.per_view_rms_px],
        }


@dataclass(frozen=True)
class ViewRecord:
    """一个标定视图的记录。"""

    view_id: str
    corners: int
    rms_px: float
    markers: int = 0
    marker_matches: int = 0
    method: str = ""

    def to_dict(self) -> dict[str, Any]:
        entry: dict[str, Any] = {
            "view_id": self.view_id,
            "corners": self.corners,
            "rms_px": round(self.rms_px, 6),
        }
        if self.method:
            entry["method"] = self.method
        if self.markers:
            entry["markers"] = self.markers
            entry["marker_matches"] = self.marker_matches
        return entry


@dataclass(frozen=True)
class CalibrationFile:
    """一份标定文件。"""

    calibration_id: str
    created_at: str
    target: CalibTarget
    lens: LensModel
    image_width: int
    image_height: int
    image_count: int
    reprojection_error: ReprojectionError
    views: tuple[ViewRecord, ...] = ()
    source: dict[str, Any] = field(default_factory=dict)
    notes: tuple[str, ...] = ()
    schema_version: str = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "kind": KIND,
            "calibration_id": self.calibration_id,
            "created_at": self.created_at,
            "status": "synthetic" if self.source.get("kind") == "synthetic" else "capture",
            "target": self.target.to_dict(),
            "image": {"width": self.image_width, "height": self.image_height},
            "image_count": self.image_count,
            "point_count": self.reprojection_error.point_count,
            "intrinsics": {
                "fx": round(self.lens.fx, 6),
                "fy": round(self.lens.fy, 6),
                "cx": round(self.lens.cx, 6),
                "cy": round(self.lens.cy, 6),
            },
            "distortion": {
                "model": self.lens.model,
                "k1": round(self.lens.k1, 9),
                "k2": round(self.lens.k2, 9),
                "p1": round(self.lens.p1, 9),
                "p2": round(self.lens.p2, 9),
            },
            "reprojection_error": self.reprojection_error.to_dict(),
            "views": [view.to_dict() for view in self.views],
            "source": dict(self.source),
            "notes": list(self.notes),
        }

    def to_json(self) -> str:
        """规范文本形式：缩进 2、键序按插入顺序、行尾 ``\\n``（可作为 golden 快照）。"""
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2) + "\n"

    def write(self, path: Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.to_json(), encoding="utf-8")
        return path

    @classmethod
    def from_dict(cls, document: dict[str, Any]) -> "CalibrationFile":
        problems = validate_calibration(document)
        if problems:
            raise CalibrationFileError(problems)
        intrinsics = document["intrinsics"]
        distortion = document["distortion"]
        errors = document["reprojection_error"]
        return cls(
            calibration_id=str(document["calibration_id"]),
            created_at=str(document["created_at"]),
            target=CalibTarget.from_dict(document["target"]),
            lens=LensModel(
                fx=float(intrinsics["fx"]),
                fy=float(intrinsics["fy"]),
                cx=float(intrinsics["cx"]),
                cy=float(intrinsics["cy"]),
                k1=float(distortion["k1"]),
                k2=float(distortion["k2"]),
                p1=float(distortion["p1"]),
                p2=float(distortion["p2"]),
                model=str(distortion["model"]),
            ),
            image_width=int(document["image"]["width"]),
            image_height=int(document["image"]["height"]),
            image_count=int(document["image_count"]),
            reprojection_error=ReprojectionError(
                rms_px=float(errors["rms_px"]),
                max_px=float(errors["max_px"]),
                per_view_rms_px=tuple(float(value) for value in errors.get("per_view_rms_px", ())),
                point_count=int(errors.get("point_count", document.get("point_count", 0))),
            ),
            views=tuple(
                ViewRecord(
                    view_id=str(item["view_id"]),
                    corners=int(item["corners"]),
                    rms_px=float(item["rms_px"]),
                    markers=int(item.get("markers", 0)),
                    marker_matches=int(item.get("marker_matches", 0)),
                    method=str(item.get("method", "")),
                )
                for item in document.get("views", ())
            ),
            source=dict(document.get("source", {})),
            notes=tuple(str(note) for note in document.get("notes", ())),
            schema_version=str(document.get("schema_version", SCHEMA_VERSION)),
        )

    @classmethod
    def load(cls, path: Path) -> "CalibrationFile":
        path = Path(path)
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise CalibrationFileError([f"{path}: 不是合法 JSON（{error}）"]) from None
        if not isinstance(document, dict):
            raise CalibrationFileError([f"{path}: 顶层必须是对象"])
        return cls.from_dict(document)

    def describe(self) -> str:
        return (
            f"{self.calibration_id}：{self.image_width}×{self.image_height}，"
            f"{self.image_count} 张标定图 / {self.reprojection_error.point_count} 个角点，"
            f"重投影 RMS {self.reprojection_error.rms_px:.3f} px（最大 "
            f"{self.reprojection_error.max_px:.3f} px）；{self.lens.describe()}"
        )


# --- 校验 -----------------------------------------------------------------------------

_REQUIRED: tuple[tuple[str, type], ...] = (
    ("schema_version", str),
    ("kind", str),
    ("calibration_id", str),
    ("created_at", str),
    ("intrinsics", dict),
    ("distortion", dict),
    ("image", dict),
    ("reprojection_error", dict),
    ("target", dict),
)


def validate_calibration(document: dict[str, Any]) -> list[str]:
    """检查标定文件形状与数值范围，返回问题清单（空 = 合格）。"""
    problems: list[str] = []
    if not isinstance(document, dict):
        return ["顶层必须是对象"]
    for key, expected in _REQUIRED:
        if key not in document:
            problems.append(f"缺少字段 {key}")
        elif not isinstance(document[key], expected):
            problems.append(f"{key} 类型应为 {expected.__name__}，实际 {type(document[key]).__name__}")
    if problems:
        return problems
    if document["kind"] != KIND:
        problems.append(f"kind 应为 {KIND!r}，实际 {document['kind']!r}")
    if not document["calibration_id"]:
        problems.append("calibration_id 不能为空")
    if not _is_iso8601(document["created_at"]):
        problems.append(f"created_at 不是 ISO8601 时间（形如 2026-11-24T00:00:00Z）：{document['created_at']!r}")

    intrinsics = document["intrinsics"]
    for key in ("fx", "fy", "cx", "cy"):
        if key not in intrinsics:
            problems.append(f"intrinsics 缺少 {key}")
        elif not _is_number(intrinsics[key]):
            problems.append(f"intrinsics.{key} 必须是数字")
    for key in ("fx", "fy"):
        if key in intrinsics and _is_number(intrinsics[key]) and float(intrinsics[key]) <= 0.0:
            problems.append(f"intrinsics.{key} 必须为正")

    distortion = document["distortion"]
    model = distortion.get("model")
    if model not in DISTORTION_MODELS:
        problems.append(f"distortion.model 必须是 {DISTORTION_MODELS} 之一，实际 {model!r}")
    for key in ("k1", "k2", "p1", "p2"):
        if key not in distortion:
            problems.append(f"distortion 缺少 {key}")
        elif not _is_number(distortion[key]):
            problems.append(f"distortion.{key} 必须是数字")

    image = document["image"]
    for key in ("width", "height"):
        if key not in image:
            problems.append(f"image 缺少 {key}")
        elif not isinstance(image[key], int) or image[key] <= 0:
            problems.append(f"image.{key} 必须是正整数")
    count = document.get("image_count")
    if not isinstance(count, int) or count <= 0:
        problems.append("image_count 必须是正整数")
    elif count < 3:
        problems.append(f"image_count 至少为 3（实际 {count}），少于 3 张无法解出内参")
    if isinstance(image.get("width"), int) and isinstance(intrinsics.get("cx"), (int, float)):
        if not 0.0 <= float(intrinsics["cx"]) <= float(image["width"]) * 2:
            problems.append("intrinsics.cx 明显越界（应在画面附近）")
    if isinstance(image.get("height"), int) and isinstance(intrinsics.get("cy"), (int, float)):
        if not 0.0 <= float(intrinsics["cy"]) <= float(image["height"]) * 2:
            problems.append("intrinsics.cy 明显越界（应在画面附近）")

    errors = document["reprojection_error"]
    for key in ("rms_px", "max_px"):
        if key not in errors:
            problems.append(f"reprojection_error 缺少 {key}")
        elif not _is_number(errors[key]) or float(errors[key]) < 0.0:
            problems.append(f"reprojection_error.{key} 必须是非负数")
    if _is_number(errors.get("rms_px")) and _is_number(errors.get("max_px")):
        if float(errors["max_px"]) + 1e-9 < float(errors["rms_px"]):
            problems.append("reprojection_error.max_px 不能小于 rms_px（最大值不小于均方根）")

    target = document["target"]
    try:
        CalibTarget.from_dict(target).inner_corners()
    except (KeyError, ValueError, TypeError) as error:
        problems.append(f"target 定义不合法：{error}")

    status = document.get("status")
    if status is not None and status not in ("synthetic", "capture"):
        problems.append(f"status 只能是 synthetic / capture，实际 {status!r}")
    return problems


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _is_iso8601(text: str) -> bool:
    """只做形状检查（``YYYY-MM-DDTHH:MM:SS`` 后跟 ``Z`` 或 ``+hh:mm``），不引入时区库。"""
    if not isinstance(text, str) or len(text) < 20 or text[4] != "-" or text[7] != "-":
        return False
    if text[10] != "T" or text[13] != ":" or text[16] != ":":
        return False
    tail = text[19:]
    return tail in ("Z",) or (len(tail) == 6 and tail[0] in "+-" and tail[3] == ":")


def validation_diagnostics(document: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    """把校验问题包成诊断（``LB-B008``），供 CLI 打印。"""
    return tuple(
        diagnostic("LB-B008", "error", problem) for problem in validate_calibration(document)
    )
