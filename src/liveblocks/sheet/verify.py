"""标记页验收：把生成的页面**重新识别**一遍，逐卡核对 id、朝向与位置。

一道验收做四件事（全部是「真跑」的结果，不写「应该能过」）：

1. **重识别**：把渲染出来的像素当成一帧（``Frame``，``image_ref = card-sheet.png``）交给 LB-003
   的识别层（默认自适应窗口 ``block=21``，与其它层一致），得到 ``MarkerCandidateList`` +
   ``CardCandidateList``；
2. **逐卡核对**：清单里每张卡都要被读成同一个 ``card_id``、``status == "ok"``；标记的面内角
   （``angle_deg``，逆时针为正）要接近 0°；识别出的卡片中心要与清单里的卡槽中心吻合
   （这条只说明**几何自洽**，不是识别精度结论）；
3. **朝向可读性**：把同一页**顺时针转 90° / 180° / 270°** 再识别一遍，每张卡的角读数必须跟着
   转过去（-90° × 圈数）。这一条是为了排除「朝向永远读成 0°」的假通过；
4. **确定性**：同参数再渲染一遍，PNG / PDF / 清单三种产物必须**逐字节相同**。

合成管线能读出全部 id 与朝向，**不等于**实体卡能；真实打印机、纸张与拍摄距离见
``docs/CARD-SPEC.md`` §11 的待人工验证步骤。
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ..vision import CardRegistry, recognize
from ..vision.frame import Frame
from ..vision.image import GrayImage
from .codes import diagnostic
from .render import SheetImage, render_sheet, rotate_quarter_turns
from .spec import HUMAN_VERIFICATION, CardSheetSpec

#: 朝向读数的容差（度）。合成图没有畸变，标记又正对"相机"，实测 0.0°；留 1.5° 给数值噪声。
ORIENTATION_TOLERANCE_DEG = 1.5
#: 卡片中心与清单卡槽中心的容许偏差（像素）。1 px = 0.25 mm（4 px/mm）。
CENTER_TOLERANCE_PX = 3.0
#: 重识别默认用的自适应窗口（与 LB-003 的其它入口一致）。
DEFAULT_BLOCK = 21


@dataclass(frozen=True)
class CardRead:
    """一张卡的识别结果与清单的对照。"""

    card_id: str
    marker_id: int
    slot: int
    row: int
    col: int
    found: bool
    status: str
    angle_deg: float
    center_px: tuple[float, float] | None
    expected_center_px: tuple[float, float]
    center_error_px: float
    orientation_ok: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "card_id": self.card_id,
            "marker_id": self.marker_id,
            "slot": self.slot,
            "row": self.row,
            "col": self.col,
            "found": self.found,
            "status": self.status,
            "angle_deg": round(self.angle_deg, 3),
            "center_px": [round(value, 3) for value in self.center_px] if self.center_px else None,
            "expected_center_px": [round(value, 3) for value in self.expected_center_px],
            "center_error_px": round(self.center_error_px, 3),
            "orientation_ok": self.orientation_ok,
        }


@dataclass(frozen=True)
class RotationRead:
    """「把页面转 90°×``turns`` 再识别」的结果。"""

    turns: int
    expected_angle_deg: float
    cards: int
    ids_match: bool
    max_angle_error_deg: float

    @property
    def ok(self) -> bool:
        return self.ids_match and self.cards > 0 and self.max_angle_error_deg <= ORIENTATION_TOLERANCE_DEG

    def to_dict(self) -> dict[str, Any]:
        return {
            "turns": self.turns,
            "expected_angle_deg": round(self.expected_angle_deg, 3),
            "cards": self.cards,
            "ids_match": self.ids_match,
            "max_angle_error_deg": round(self.max_angle_error_deg, 3),
            "ok": self.ok,
        }


@dataclass(frozen=True)
class SheetVerification:
    """一次标记页验收的全部结果。"""

    spec: CardSheetSpec
    image: SheetImage
    reads: tuple[CardRead, ...]
    rotations: tuple[RotationRead, ...]
    unidentified_candidates: int
    repeated_png_identical: bool
    repeated_pdf_identical: bool
    repeated_manifest_identical: bool
    elapsed_ms: float
    diagnostics: tuple[dict[str, Any], ...] = field(default_factory=tuple)

    @property
    def expected_cards(self) -> int:
        return len(self.spec.cards)

    @property
    def read_cards(self) -> int:
        return sum(1 for read in self.reads if read.found and read.status == "ok")

    def acceptance(self) -> list[tuple[bool, str, str]]:
        """五条自动化验收；返回 ``(是否通过, 条目, 依据)``。"""
        missing = [read.card_id for read in self.reads if not read.found]
        bad_status = [f"{read.card_id}({read.status})" for read in self.reads if read.found and read.status != "ok"]
        all_read = (
            self.expected_cards > 0
            and not missing
            and not bad_status
            and self.read_cards == self.expected_cards
        )
        orientation_bad = [read.card_id for read in self.reads if not read.orientation_ok]
        center_max = max((read.center_error_px for read in self.reads if read.found), default=0.0)
        rotations_ok = len(self.rotations) == 3 and all(
            rotation.ok for rotation in self.rotations
        )
        deterministic = (
            self.repeated_png_identical
            and self.repeated_pdf_identical
            and self.repeated_manifest_identical
        )
        return [
            (
                all_read,
                f"标记页重新识别读出全部 {self.expected_cards} 张卡的 id",
                f"读出 {self.read_cards}/{self.expected_cards}"
                + (f"；缺 {'、'.join(missing)}" if missing else "")
                + (f"；状态异常 {'、'.join(bad_status)}" if bad_status else "")
                + f"；未登记候选 {self.unidentified_candidates} 个",
            ),
            (
                not orientation_bad,
                "每张卡的朝向读数与清单一致（angle ≈ 0°）",
                f"{self.expected_cards - len(orientation_bad)}/{self.expected_cards} 张卡在 "
                f"±{ORIENTATION_TOLERANCE_DEG:g}° 内"
                + (f"；偏差超限 {'、'.join(orientation_bad)}" if orientation_bad else ""),
            ),
            (
                rotations_ok,
                "把页面转 90°/180°/270° 后朝向跟着转（排除「朝向恒为 0°」的假通过）",
                (
                    "；".join(
                        f"转 {rotation.turns * 90}°：卡 {rotation.cards} 张、"
                        f"期望 {rotation.expected_angle_deg:g}°、"
                        f"最大偏差 {rotation.max_angle_error_deg:.2f}°"
                        for rotation in self.rotations
                    )
                    if self.rotations
                    else "未跑旋转核对（rotations=False）：没有这条证据就不能声称朝向可读"
                ),
            ),
            (
                center_max <= CENTER_TOLERANCE_PX,
                f"识别出的卡片中心与清单卡槽中心吻合（≤ {CENTER_TOLERANCE_PX:g} px）",
                f"最大偏差 {center_max:.3f} px（1 px = {1.0 / self.spec.pixels_per_mm:.3f} mm）；"
                "只说明版面几何自洽，不是识别精度结论",
            ),
            (
                deterministic,
                "同参数两次渲染 PNG / PDF / 清单逐字节一致",
                f"PNG {self.image.png_sha256[:16]}（{len(self.image.png)} 字节）、"
                f"PDF {self.image.pdf_sha256[:16]}（{len(self.image.pdf)} 字节）、"
                f"清单 {hashlib.sha256(self.image.manifest_json.encode('utf-8')).hexdigest()[:16]}",
            ),
        ]

    def passes(self) -> bool:
        return all(ok for ok, _criterion, _evidence in self.acceptance())

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": "CardSheetVerification",
            "schema_version": "0.0.1",
            "layout": self.spec.layout_name,
            "batch_id": self.spec.batch_id,
            "image": {
                "width_px": self.image.width,
                "height_px": self.image.height,
                "pixel_sha256": self.image.pixel_sha256,
                "png_sha256": self.image.png_sha256,
                "pdf_sha256": self.image.pdf_sha256,
            },
            "cards": [read.to_dict() for read in self.reads],
            "rotations": [rotation.to_dict() for rotation in self.rotations],
            "unidentified_candidates": self.unidentified_candidates,
            "deterministic": {
                "png": self.repeated_png_identical,
                "pdf": self.repeated_pdf_identical,
                "manifest": self.repeated_manifest_identical,
            },
            "acceptance": [
                {"ok": ok, "criterion": criterion, "evidence": evidence}
                for ok, criterion, evidence in self.acceptance()
            ],
            "elapsed_ms": round(self.elapsed_ms, 3),
            "diagnostics": [dict(entry) for entry in self.diagnostics],
            "human_verification": [
                {"item": item, "steps": steps} for item, steps in HUMAN_VERIFICATION
            ],
        }


def _recognize(pixels: bytes, width: int, height: int, spec: CardSheetSpec, *, block: int):
    frame = Frame(
        frame_id=f"sheet-{spec.layout_name}",
        image=GrayImage(width, height, pixels),
        image_ref="card-sheet.png",
    )
    registry = CardRegistry.load(capture_layout=spec.layout_name)
    return recognize(frame, registry=registry, block=block)


def _wrap(angle: float) -> float:
    """把角度折算到 ``(-180, 180]``。"""
    value = (angle + 180.0) % 360.0 - 180.0
    return 180.0 if value == -180.0 else value


def verify_sheet(
    spec: CardSheetSpec,
    *,
    block: int = DEFAULT_BLOCK,
    rotations: bool = True,
    image: SheetImage | None = None,
    recognize_fn: Callable[..., object] | None = None,
    render_fn: Callable[..., SheetImage] | None = None,
) -> SheetVerification:
    """跑一遍完整验收：渲染 → 重识别 → 逐卡核对 → 旋转核对 → 确定性核对。

    ``recognize_fn`` / ``render_fn`` 只用于测试注入故障（丢掉一张卡、给出错的角度、两次渲染
    不一致），默认就是本层的真实现：``_recognize`` 与 :func:`render_sheet`。
    """
    started = time.perf_counter()
    renderer = render_fn or render_sheet
    recognizer = recognize_fn or _recognize
    if image is None:
        image = renderer(spec)
    ppm = spec.pixels_per_mm
    result = recognizer(image.pixels, image.width, image.height, spec, block=block)

    angles: dict[int, float] = {}
    for marker in result.markers.markers:
        if marker.marker_id is None:
            continue
        angles[int(marker.marker_id)] = float(marker.angle_deg)
    by_card = {card.card_id: card for card in result.cards.cards if card.status == "ok"}
    unidentified = sum(1 for card in result.cards.cards if card.status != "ok")

    diagnostics: list[dict[str, Any]] = []
    reads: list[CardRead] = []
    for slot in spec.slots():
        card = slot.card
        x0, y0, x1, y1 = spec.slot_rect_mm(slot.index)
        expected = ((x0 + x1) / 2.0 * ppm, (y0 + y1) / 2.0 * ppm)
        candidate = by_card.get(card.card_id)
        angle = angles.get(card.marker_id, 0.0)
        if candidate is None:
            diagnostics.append(
                diagnostic(
                    "LB-S004",
                    "error",
                    f"{card.card_id}（标记 {card.marker_id}）没有在重识别结果里读出",
                    card_id=card.card_id,
                )
            )
            reads.append(
                CardRead(
                    card_id=card.card_id,
                    marker_id=card.marker_id,
                    slot=slot.index,
                    row=slot.row,
                    col=slot.col,
                    found=False,
                    status="missing",
                    angle_deg=angle,
                    center_px=None,
                    expected_center_px=expected,
                    center_error_px=float("nan"),
                    orientation_ok=False,
                )
            )
            continue
        if card.marker_id not in angles:
            diagnostics.append(
                diagnostic(
                    "LB-S005",
                    "error",
                    f"{card.card_id}（标记 {card.marker_id}）读到了身份但没有角度读数",
                    card_id=card.card_id,
                )
            )
        angle_ok = abs(_wrap(angle)) <= ORIENTATION_TOLERANCE_DEG
        if not angle_ok:
            diagnostics.append(
                diagnostic(
                    "LB-S005",
                    "error",
                    f"{card.card_id} 的朝向读数 {angle:.2f}° 超出 ±{ORIENTATION_TOLERANCE_DEG:g}°",
                    card_id=card.card_id,
                )
            )
        center = (float(candidate.center_px[0]), float(candidate.center_px[1]))
        error = ((center[0] - expected[0]) ** 2 + (center[1] - expected[1]) ** 2) ** 0.5
        reads.append(
            CardRead(
                card_id=card.card_id,
                marker_id=card.marker_id,
                slot=slot.index,
                row=slot.row,
                col=slot.col,
                found=True,
                status=candidate.status,
                angle_deg=angle,
                center_px=center,
                expected_center_px=expected,
                center_error_px=error,
                orientation_ok=angle_ok,
            )
        )

    if unidentified:
        diagnostics.append(
            diagnostic(
                "LB-S004",
                "warning",
                f"重识别结果里有 {unidentified} 个候选没有卡片身份（卡面文字也可能被当成标记候选）",
            )
        )

    rotation_reads: list[RotationRead] = []
    if rotations:
        expected_ids = {card.card_id for card in spec.cards}
        if len({read.card_id for read in reads if read.found}) != len(expected_ids):
            expected_ids = {read.card_id for read in reads if read.found}
        for turns in (1, 2, 3):
            width, height, pixels = rotate_quarter_turns(image.width, image.height, image.pixels, turns)
            turned = recognizer(pixels, width, height, spec, block=block)
            turned_ids = {card.card_id for card in turned.cards.cards if card.status == "ok"}
            turned_angles: dict[int, float] = {}
            for marker in turned.markers.markers:
                if marker.marker_id is not None:
                    turned_angles[int(marker.marker_id)] = float(marker.angle_deg)
            expected_angle = -90.0 * turns
            errors: list[float] = []
            for card in spec.cards:
                if card.card_id not in turned_ids or card.marker_id not in turned_angles:
                    continue
                errors.append(abs(_wrap(turned_angles[card.marker_id] - expected_angle)))
            rotation_reads.append(
                RotationRead(
                    turns=turns,
                    expected_angle_deg=expected_angle,
                    cards=len(errors),
                    ids_match=turned_ids == expected_ids,
                    max_angle_error_deg=max(errors) if errors else float("inf"),
                )
            )

    # 确定性：同参数再渲染一遍，三种产物都要逐字节相同
    again = renderer(spec)
    png_same = again.png == image.png
    pdf_same = again.pdf == image.pdf
    manifest_same = again.manifest_json == image.manifest_json
    if not (png_same and pdf_same and manifest_same):
        diagnostics.append(
            diagnostic(
                "LB-S006",
                "error",
                f"同参数两次渲染不一致：PNG={png_same}，PDF={pdf_same}，清单={manifest_same}",
            )
        )

    elapsed_ms = (time.perf_counter() - started) * 1000.0
    return SheetVerification(
        spec=spec,
        image=image,
        reads=tuple(reads),
        rotations=tuple(rotation_reads),
        unidentified_candidates=unidentified,
        repeated_png_identical=png_same,
        repeated_pdf_identical=pdf_same,
        repeated_manifest_identical=manifest_same,
        elapsed_ms=elapsed_ms,
        diagnostics=tuple(diagnostics),
    )


def format_report(verification: SheetVerification) -> str:
    """把验收结果渲染成给人看的文本（数字全部是本机真跑出来的）。"""
    spec = verification.spec
    lines: list[str] = []
    lines.append(
        f"标记页验收：{spec.layout_name} 版式、{len(spec.cards)} 张卡、{spec.paper} "
        f"{spec.paper_width_mm:g}×{spec.paper_height_mm:g} mm、"
        f"{spec.columns} 行位 × {spec.rows} 列位（容量 {spec.capacity}，{spec.page_count} 页）、"
        f"{spec.width_px}×{spec.height_px} px @ {spec.pixels_per_mm:g} px/mm"
    )
    lines.append(
        f"静区：卡面声明 {spec.quiet_zone_face_mm:g} mm，静区规则 {spec.quiet_zone_rule_mm:g} mm"
        f"（1 个模块 {spec.module_mm:g} mm），卡间距 {spec.gutter_mm:g} mm"
    )
    lines.append(f"批次号 {spec.batch_id}；耗时 {verification.elapsed_ms:.0f} ms（实测）")
    lines.append("")
    lines.append("逐卡：")
    for read in verification.reads:
        if not read.found:
            lines.append(f"  {read.card_id}(标记 {read.marker_id:>2}) 未读出 —— 见诊断 LB-S004")
            continue
        lines.append(
            f"  {read.card_id}(标记 {read.marker_id:>2}) 行 {read.row} 列 {read.col} "
            f"状态 {read.status:<6} 朝向 {read.angle_deg:+.2f}° "
            f"中心偏差 {read.center_error_px:.3f} px"
        )
    if verification.rotations:
        lines.append("")
        lines.append("旋转核对：")
    for rotation in verification.rotations:
        lines.append(
            f"  顺时针 {rotation.turns * 90:>3}°：卡 {rotation.cards:>2} 张，"
            f"期望 {rotation.expected_angle_deg:+.0f}°，最大偏差 {rotation.max_angle_error_deg:.2f}°"
            f" → {'通过' if rotation.ok else '未通过'}"
        )
    if verification.diagnostics:
        lines.append("")
        lines.append("诊断：")
        for entry in verification.diagnostics:
            lines.append(f"  {entry['code']} {entry['severity']} {entry['message']}")
    lines.append("")
    lines.append("验收对照：")
    for ok, criterion, evidence in verification.acceptance():
        lines.append(f"  [{'通过' if ok else '未通过'}] {criterion} —— {evidence}")
    lines.append("")
    lines.append("待人工验证（实体打印 / 基材 / 识别距离相关，代理不得代填）：")
    for index, (item, steps) in enumerate(HUMAN_VERIFICATION, start=1):
        lines.append(f"  {index}. {item}")
        lines.append(f"     人工步骤：{steps}")
    lines.append("")
    lines.append(
        "结论：" + ("全部自动化验收通过（仅限合成管线）" if verification.passes() else "有验收条目未通过")
    )
    return "\n".join(lines) + "\n"


__all__ = [
    "CENTER_TOLERANCE_PX",
    "DEFAULT_BLOCK",
    "ORIENTATION_TOLERANCE_DEG",
    "CardRead",
    "RotationRead",
    "SheetVerification",
    "format_report",
    "verify_sheet",
]
