"""识别叠加图：在一帧灰度图上画出标记框、卡片范围与卡片 id/状态。

「零新增依赖 + 字节可复现」：复用 LB-008 的确定性光栅（``liveblocks.ui.raster.Canvas``：
整数 Bresenham + 仓库自带点阵字体 + 仓库自带 PNG 编码器），不引入任何绘图库。同样的输入
必然得到同样的 PNG 字节，所以「叠加图能解码、尺寸与输入一致」可以写成断言而不是靠肉眼。

叠加图的标签只有 ASCII（点阵字形集是 ASCII）：卡片 id、卡面、状态、置信度都是 ASCII，
所以不需要中文字形，也不会出现占位下划线。
"""

from __future__ import annotations

from collections.abc import Sequence

from ..ui.raster import INK, INK_SOFT, Canvas, load_overlay_font
from ..vision.frame import Frame
from ..vision.recognize import RecognitionResult
from .protocol import CAPTURE_VERSION

#: 卡片框比标记框粗一档（一眼分清「标记」与「卡片范围」）。
CARD_THICKNESS = 2
#: 图例底色（深色，保证白字可读）。
LEGEND_BG = 16


def _round_points(points: Sequence[Sequence[float]]) -> list[tuple[int, int]]:
    return [(int(round(float(x))), int(round(float(y)))) for x, y in points]


def render_overlay(
    frame: Frame,
    result: RecognitionResult,
    *,
    extra_lines: Sequence[str] = (),
) -> bytes:
    """把识别结果画到原帧上，返回 PNG 字节（尺寸与输入帧一致）。"""
    font = load_overlay_font()
    canvas = Canvas(width=frame.width, height=frame.height, pixels=bytearray(frame.image.pixels))

    # 标记层（§3.1）：四边形 + 中心点 + m<id>
    for marker in result.markers.markers:
        corners = _round_points(marker.corners_px)
        if len(corners) == 4:
            canvas.draw_polyline(corners, closed=True, shade=INK, thickness=1)
            anchor = corners[0]
            label = f"m{marker.marker_id}" if marker.marker_id is not None else "m?"
            canvas.draw_label(label, (anchor[0] + 2, max(0, anchor[1] - 12)), font=font)
        center = _round_points([marker.center_px])[0]
        canvas.set(center[0], center[1], INK)

    # 卡片层（§3.2）：卡片范围（能外推时）+ 中心十字 + id/卡面/状态/置信度
    for card in result.cards.cards:
        if card.card_corners_px:
            canvas.draw_polyline(
                _round_points(card.card_corners_px), closed=True, shade=INK_SOFT, thickness=CARD_THICKNESS
            )
        cx, cy = _round_points([card.center_px])[0]
        canvas.draw_cross(cx, cy, size=6, shade=INK)
        canvas.draw_label(
            f"{card.candidate_id} {card.card_id} {card.card_face} {card.status} {card.confidence:.2f}",
            (cx + 8, max(0, cy - 20)),
            font=font,
        )

    # 图例：一眼看出这张叠加图是哪一帧、哪个版本画出来的
    legend = [
        f"LB-030 overlay {CAPTURE_VERSION}",
        f"frame={frame.frame_id} {frame.width}x{frame.height}",
        f"markers={len(result.markers.markers)} cards={len(result.cards.cards)}",
        "quad=marker box=card cross=center",
    ]
    legend.extend(str(line) for line in extra_lines)
    width = 4 + 8 * max(len(line) for line in legend) + 8
    canvas.fill_rect(4, 4, width, 4 + 14 * len(legend) + 4, LEGEND_BG)
    for index, line in enumerate(legend):
        canvas.draw_label(line, (8, 8 + index * 14), font=font, background=None)

    return canvas.to_png()


__all__ = ["CARD_THICKNESS", "LEGEND_BG", "render_overlay"]
