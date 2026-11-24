"""S1 ``Frame``：把一张灰度图包装成 ``docs/PROTOCOL.md`` §2 的帧对象。

识别层只吃 ``Frame``：像素 + 尺寸 + 帧标识（+ 可选内参）。LB-003 阶段没有摄像头，帧来自
LB-002 的合成图或回放文件，所以 ``source`` 默认 ``synthetic``，并且 ``captured_at_ms`` 固定为
``0.0``——合成帧没有墙钟时间，取固定值才能让识别输出**字节可复现**（协议只要求「单调递增」
而不是墙钟时间）。

``frame_id`` 默认由文件名推得（``f-<文件名主干>``），保证同一份 fixtures 每次跑出同样的 id。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from ..synth.png import decode_gray
from .image import GrayImage

#: 协议文档里 S2 对象的 ``schema_version``（与 `docs/PROTOCOL.md` 的草案版本一致）。
SCHEMA_VERSION = "0.0.1"

_FRAME_ID_SAFE = re.compile(r"[^0-9A-Za-z_.-]+")


@dataclass(frozen=True)
class Intrinsics:
    """针孔相机内参（``distortion`` 单独给出，见 :class:`Distortion` 与 LB-031）。"""

    fx: float
    fy: float
    cx: float
    cy: float

    def __post_init__(self) -> None:
        if self.fx <= 0.0 or self.fy <= 0.0:
            raise ValueError(f"焦距必须为正：fx={self.fx}, fy={self.fy}")

    def to_dict(self) -> dict[str, float]:
        return {"fx": self.fx, "fy": self.fy, "cx": self.cx, "cy": self.cy}


@dataclass(frozen=True)
class Distortion:
    """镜头畸变（``docs/PROTOCOL.md`` §2 的 ``plumb_bob``：径向 + 切向）。

    LB-031 的标定文件里就是这五个字段；全 0 表示「按无畸变处理」，与只给内参的旧行为一致。
    """

    k1: float = 0.0
    k2: float = 0.0
    p1: float = 0.0
    p2: float = 0.0
    model: str = "plumb_bob"

    def to_dict(self) -> dict[str, float | str]:
        return {
            "model": self.model,
            "k1": self.k1,
            "k2": self.k2,
            "p1": self.p1,
            "p2": self.p2,
        }

    @classmethod
    def from_dict(cls, document: dict[str, object]) -> "Distortion":
        return cls(
            k1=float(document.get("k1", 0.0)),  # type: ignore[arg-type]
            k2=float(document.get("k2", 0.0)),  # type: ignore[arg-type]
            p1=float(document.get("p1", 0.0)),  # type: ignore[arg-type]
            p2=float(document.get("p2", 0.0)),  # type: ignore[arg-type]
            model=str(document.get("model", "plumb_bob")),
        )


@dataclass(frozen=True)
class Frame:
    """一帧输入图像。"""

    frame_id: str
    image: GrayImage
    image_ref: str
    source: str = "synthetic"
    intrinsics: Intrinsics | None = None
    distortion: "Distortion | None" = None
    captured_at_ms: float = 0.0
    calibration_id: str = "calib-default"

    @property
    def width(self) -> int:
        return self.image.width

    @property
    def height(self) -> int:
        return self.image.height

    def to_dict(self) -> dict[str, object]:
        """按协议 §2 输出 ``Frame``（不含内联图像，只用 ``image_ref`` 引用）。"""
        document: dict[str, object] = {
            "schema_version": SCHEMA_VERSION,
            "kind": "Frame",
            "frame_id": self.frame_id,
            "captured_at_ms": self.captured_at_ms,
            "source": self.source,
            "width": self.width,
            "height": self.height,
            "calibration_id": self.calibration_id,
            "image_ref": self.image_ref,
        }
        if self.intrinsics is not None:
            document["intrinsics"] = self.intrinsics.to_dict()
            distortion = self.distortion or Distortion()
            document["distortion"] = distortion.to_dict()
        return document


def frame_id_for(path: Path) -> str:
    """由文件名推导稳定的 ``frame_id``（``fixtures/scenes/normal.png`` → ``f-normal``）。"""
    stem = _FRAME_ID_SAFE.sub("-", Path(path).stem).strip("-") or "image"
    return f"f-{stem}"


def load_frame(
    path: Path,
    *,
    frame_id: str | None = None,
    source: str = "synthetic",
    intrinsics: Intrinsics | None = None,
    distortion: "Distortion | None" = None,
    image_ref: str | None = None,
) -> Frame:
    """读一张 PNG 灰度图并包装成 ``Frame``。"""
    path = Path(path)
    width, height, pixels = decode_gray(path.read_bytes())
    return Frame(
        frame_id=frame_id or frame_id_for(path),
        image=GrayImage(width, height, pixels),
        image_ref=image_ref or path.name,
        source=source,
        intrinsics=intrinsics,
        distortion=distortion,
    )
