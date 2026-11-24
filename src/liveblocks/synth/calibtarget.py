"""标定板（棋盘格 / ChArUco）与「带畸变的合成拍摄图」生成器。

标定板在**平面坐标**里描述（单位毫米，``Z = 0``）：

- 原点在**整块板（含白色留边）的中心**，x 向右、y 向下——这样位姿 ``Pose(x, y, z)`` 就是
  「板心在相机坐标里的位置」，与 LB-002 的卡片一致；
- 方块边长 ``square_mm``，棋盘 ``squares_x × squares_y`` 个方块，(0,0) 格是**黑格**，
  黑白按 ``(col + row) % 2 == 0`` 交替；
- 「内角点」是四个方块交汇的 X 型角点，坐标 ``(i·square_mm, j·square_mm)``，
  ``i ∈ 1..squares_x-1``、``j ∈ 1..squares_y-1``，id 按行优先从 0 起：
  ``id = (j - 1)·(squares_x - 1) + (i - 1)``；
- ChArUco 板把**白格**换成 ArUco 标记（边长 ``marker_mm``，居中，四周留白），标记 id
  也按行优先从 0 起递增，只落在白格上。内角点定义与纯棋盘格完全相同。

:func:`render_capture` 是「合成器 + 镜头畸变模型」的接口：给定目标、位姿与**真值内参/畸变**，
渲染一张**带畸变的**拍摄图，并给出同一帧里每个内角点的**像素坐标真值**（已经过畸变映射）。
渲染是一遍完成的：对每个输出像素先按镜头畸变反解到理想针孔图上的位置，再用单应回到标定板
纹理采样——因此边缘依旧是双线性插值的平滑过渡，不会出现「先渲染再二次插值」的双重模糊。
"""

from __future__ import annotations

import hashlib
import json
import math
import random
from dataclasses import dataclass
from typing import Any

from . import geometry
from .dictionaries import ArucoDictionary, load_aruco_dictionaries
from .lens import LensModel, _grid_maps, map_grid
from .png import encode_gray
from .raster import CardTexture, Raster, apply_lighting, box_blur

Point = tuple[float, float]

SCHEMA_VERSION = "0.0.1"
GROUND_TRUTH_KIND = "SyntheticCalibrationCapture"

#: 标定板纹理的分辨率（像素/毫米）。8 px/mm 下一条 25 mm 的边有 200 像素，足够双线性平滑。
DEFAULT_PIXELS_PER_MM = 8

#: 黑格 / 白格 / 留边的灰度。
BLACK_SHADE = 24
WHITE_SHADE = 232
MARGIN_SHADE = 246

KINDS = ("checkerboard", "charuco")


@dataclass(frozen=True)
class CalibTarget:
    """一块标定板（棋盘格或 ChArUco）的几何定义。"""

    name: str
    kind: str
    squares_x: int
    squares_y: int
    square_mm: float
    margin_mm: float = 0.0
    marker_mm: float = 0.0
    dictionary: str = ""

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"{self.name}: 未知标定板类型 {self.kind!r}；可用：{', '.join(KINDS)}")
        if self.squares_x < 3 or self.squares_y < 3:
            raise ValueError(f"{self.name}: 棋盘至少 3×3 个方块：{self.squares_x}×{self.squares_y}")
        if self.square_mm <= 0.0:
            raise ValueError(f"{self.name}: square_mm 必须为正：{self.square_mm}")
        if self.margin_mm < 0.0:
            raise ValueError(f"{self.name}: margin_mm 不能为负：{self.margin_mm}")
        if self.kind == "charuco":
            if not (0.0 < self.marker_mm <= self.square_mm):
                raise ValueError(
                    f"{self.name}: 标记边长必须在 (0, square_mm]：{self.marker_mm} vs {self.square_mm}"
                )
            if not self.dictionary:
                raise ValueError(f"{self.name}: ChArUco 需要指定 ArUco 字典")
        elif self.marker_mm or self.dictionary:
            raise ValueError(f"{self.name}: 纯棋盘格不应带 marker_mm / dictionary")

    # --- 尺寸 -------------------------------------------------------------------------

    @property
    def pattern_width_mm(self) -> float:
        return self.squares_x * self.square_mm

    @property
    def pattern_height_mm(self) -> float:
        return self.squares_y * self.square_mm

    @property
    def board_width_mm(self) -> float:
        return self.pattern_width_mm + 2.0 * self.margin_mm

    @property
    def board_height_mm(self) -> float:
        return self.pattern_height_mm + 2.0 * self.margin_mm

    @property
    def inner_corner_grid(self) -> tuple[int, int]:
        return self.squares_x - 1, self.squares_y - 1

    # --- 角点与方块 -------------------------------------------------------------------

    def inner_corners(self) -> list[tuple[int, float, float]]:
        """``[(id, X_mm, Y_mm), …]``：全部内角点（相对板心），按行优先排序。"""
        width = self.squares_x - 1
        offset_x = self.margin_mm - self.board_width_mm / 2.0
        offset_y = self.margin_mm - self.board_height_mm / 2.0
        corners: list[tuple[int, float, float]] = []
        for j in range(1, self.squares_y):
            for i in range(1, self.squares_x):
                corner_id = (j - 1) * width + (i - 1)
                corners.append(
                    (corner_id, i * self.square_mm + offset_x, j * self.square_mm + offset_y)
                )
        return corners

    def object_points(self) -> dict[int, Point]:
        return {corner_id: (x, y) for corner_id, x, y in self.inner_corners()}

    def pattern_outline_mm(self) -> list[Point]:
        """棋盘（不含留边）四角的平面坐标，顺序：左上、右上、右下、左下。"""
        half_w = self.pattern_width_mm / 2.0
        half_h = self.pattern_height_mm / 2.0
        return [(-half_w, -half_h), (half_w, -half_h), (half_w, half_h), (-half_w, half_h)]

    def board_outline_mm(self) -> list[Point]:
        """整块板四角的平面坐标（相对板心），顺序：左上、右上、右下、左下。"""
        half_w = self.board_width_mm / 2.0
        half_h = self.board_height_mm / 2.0
        return [(-half_w, -half_h), (half_w, -half_h), (half_w, half_h), (-half_w, half_h)]

    def is_black_cell(self, col: int, row: int) -> bool:
        return (col + row) % 2 == 0

    def markers(self) -> list[tuple[int, int, int]]:
        """ChArUco 的标记：``[(marker_id, col, row), …]``，按行优先，id 从 0 递增。"""
        if self.kind != "charuco":
            return []
        markers: list[tuple[int, int, int]] = []
        for row in range(self.squares_y):
            for col in range(self.squares_x):
                if not self.is_black_cell(col, row):
                    markers.append((len(markers), col, row))
        return markers

    def cell_center_mm(self, col: int, row: int) -> Point:
        """方块中心的平面坐标（相对板心）。"""
        return (
            self.margin_mm + (col + 0.5) * self.square_mm - self.board_width_mm / 2.0,
            self.margin_mm + (row + 0.5) * self.square_mm - self.board_height_mm / 2.0,
        )

    def marker_layout(self) -> dict[tuple[int, int], int]:
        """格 → 标记 id（ChArUco 才有；纯棋盘格返回空字典）。"""
        return {(col, row): marker_id for marker_id, col, row in self.markers()}

    def marker_corners_mm(self, marker_id: int) -> list[Point]:
        """标记四角在平面坐标里的位置，顺序为标记自身的左上、右上、右下、左下（y 向下）。"""
        for mid, col, row in self.markers():
            if mid != marker_id:
                continue
            cx, cy = self.cell_center_mm(col, row)
            half = self.marker_mm / 2.0
            return [(cx - half, cy - half), (cx + half, cy - half), (cx + half, cy + half), (cx - half, cy + half)]
        raise KeyError(f"{self.name}: 没有标记 {marker_id}")

    # --- 纹理 -------------------------------------------------------------------------

    def render_texture(
        self, *, pixels_per_mm: float = DEFAULT_PIXELS_PER_MM, aruco: ArucoDictionary | None = None
    ) -> CardTexture:
        """把标定板渲染成一张毫米定标的纹理（含白色留边）。"""
        if pixels_per_mm <= 0.0:
            raise ValueError(f"pixels_per_mm 必须为正：{pixels_per_mm}")
        ppm = float(pixels_per_mm)
        width_px = int(round(self.board_width_mm * ppm))
        height_px = int(round(self.board_height_mm * ppm))
        raster = Raster.filled(width_px, height_px, MARGIN_SHADE)

        offset_x = self.margin_mm * ppm
        offset_y = self.margin_mm * ppm
        square_px = self.square_mm * ppm
        for row in range(self.squares_y):
            for col in range(self.squares_x):
                x0 = offset_x + col * square_px
                y0 = offset_y + row * square_px
                shade = BLACK_SHADE if self.is_black_cell(col, row) else WHITE_SHADE
                _fill_rect_px(raster, x0, y0, square_px, square_px, shade)

        if self.kind == "charuco":
            dictionary = aruco or self._dictionary()
            marker_px = self.marker_mm * ppm
            modules = dictionary.marker_modules + 2 * dictionary.border_bits
            module_px = marker_px / modules
            for marker_id, col, row in self.markers():
                cx, cy = self.cell_center_mm(col, row)
                left = offset_x + cx * ppm - marker_px / 2.0
                top = offset_y + cy * ppm - marker_px / 2.0
                bits = dictionary.matrix(marker_id)
                for my in range(modules):
                    for mx in range(modules):
                        shade = module_shade(bits, mx, my, dictionary.border_bits, modules)
                        _fill_rect_px(
                            raster,
                            left + mx * module_px,
                            top + my * module_px,
                            module_px,
                            module_px,
                            shade,
                        )

        mask = bytearray([255]) * (width_px * height_px)
        return CardTexture(
            raster=raster,
            mask=mask,
            width_mm=self.board_width_mm,
            height_mm=self.board_height_mm,
            pixels_per_mm=ppm,
        )

    def _dictionary(self) -> ArucoDictionary:
        dictionaries = load_aruco_dictionaries()
        try:
            return dictionaries[self.dictionary]
        except KeyError:
            raise KeyError(
                f"{self.name}: 没有 ArUco 字典 {self.dictionary!r}；"
                f"可用：{', '.join(sorted(dictionaries))}"
            ) from None

    # --- 序列化 -----------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        document: dict[str, Any] = {
            "name": self.name,
            "kind": self.kind,
            "squares_x": self.squares_x,
            "squares_y": self.squares_y,
            "square_mm": self.square_mm,
            "margin_mm": self.margin_mm,
            "inner_corners": len(self.inner_corners()),
        }
        if self.kind == "charuco":
            document["marker_mm"] = self.marker_mm
            document["dictionary"] = self.dictionary
            document["markers"] = len(self.markers())
        return document

    @classmethod
    def from_dict(cls, document: dict[str, Any]) -> "CalibTarget":
        return cls(
            name=str(document["name"]),
            kind=str(document["kind"]),
            squares_x=int(document["squares_x"]),
            squares_y=int(document["squares_y"]),
            square_mm=float(document["square_mm"]),
            margin_mm=float(document.get("margin_mm", 0.0)),
            marker_mm=float(document.get("marker_mm", 0.0)),
            dictionary=str(document.get("dictionary", "")),
        )

    def describe(self) -> str:
        base = (
            f"{self.name}（{self.kind} {self.squares_x}×{self.squares_y}，"
            f"方块 {self.square_mm:g} mm，内角点 {len(self.inner_corners())}）"
        )
        if self.kind == "charuco":
            base += f"，标记 {self.marker_mm:g} mm / {self.dictionary}"
        return base


def _fill_rect_px(raster: Raster, x0: float, y0: float, width: float, height: float, shade: int) -> None:
    """按像素中心判断是否落在矩形内，填一个固定灰度（边缘按中心点取舍，保持确定性）。"""
    left = int(math.floor(x0))
    top = int(math.floor(y0))
    right = int(math.ceil(x0 + width))
    bottom = int(math.ceil(y0 + height))
    pixels = raster.pixels
    w = raster.width
    h = raster.height
    for y in range(max(0, top), min(h, bottom)):
        cy = y + 0.5
        if cy < y0 or cy >= y0 + height:
            continue
        row = y * w
        for x in range(max(0, left), min(w, right)):
            cx = x + 0.5
            if cx < x0 or cx >= x0 + width:
                continue
            pixels[row + x] = shade


# --- 拍摄图 ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CalibrationCapture:
    """一张合成标定拍摄图 + 它的真值。"""

    capture_id: str
    width: int
    height: int
    pixels: bytes
    ground_truth: dict[str, Any]
    ground_truth_json: str

    @property
    def png(self) -> bytes:
        return encode_gray(self.width, self.height, self.pixels)

    @property
    def pixel_sha256(self) -> str:
        return hashlib.sha256(self.pixels).hexdigest()

    def truth_corners(self) -> dict[int, Point]:
        """真值里（可见的）内角点 id → 带畸变的像素坐标。"""
        return {
            int(entry["corner_id"]): (float(entry["px"][0]), float(entry["px"][1]))
            for entry in self.ground_truth["corners"]
        }


def _plane_scale(plane_to_image: geometry.Matrix3, lens: LensModel) -> tuple[float, float]:
    """板心处「每像素多少毫米」（x / y 各一个），用于确定边缘过渡的宽度。"""
    inverse = geometry.mat_inv(plane_to_image)
    center = geometry.apply_homography(plane_to_image, 0.0, 0.0)
    px = lens.undistort(*center)
    base_x, base_y = geometry.apply_homography(inverse, *px)
    step_x = geometry.apply_homography(inverse, px[0] + 1.0, px[1])
    step_y = geometry.apply_homography(inverse, px[0], px[1] + 1.0)
    scale_x = math.hypot(step_x[0] - base_x, step_x[1] - base_y)
    scale_y = math.hypot(step_y[0] - base_x, step_y[1] - base_y)
    return max(scale_x, 1e-6), max(scale_y, 1e-6)


def module_shade(
    bits: tuple[tuple[str, ...], ...], mx: int, my: int, border_bits: int, modules: int
) -> int:
    """一个 ArUco 模块的灰度：黑边一圈恒为黑，内部按位表（``"1"`` 为白模块）。"""
    if (
        mx < border_bits
        or my < border_bits
        or mx >= modules - border_bits
        or my >= modules - border_bits
    ):
        return BLACK_SHADE
    return WHITE_SHADE if bits[my - border_bits][mx - border_bits] == "1" else BLACK_SHADE


def _shade_at(
    target: CalibTarget,
    x_mm: float,
    y_mm: float,
    *,
    half_width_mm: float,
    half_height_mm: float,
    marker_layout: dict[tuple[int, int], int],
    module_bits: dict[int, tuple[tuple[str, ...], ...]],
    module_mm: float,
    module_count: int,
    border_bits: int,
) -> int:
    """板面上某一点（板心坐标，毫米）的灰度：黑白格 / 留边 / ChArUco 模块。"""
    square_mm = target.square_mm
    # 平板坐标 → 格编号：板心坐标先回到「含留边的板角」，再减掉留边，才是棋盘的起点。
    col = int(math.floor((x_mm + half_width_mm - target.margin_mm) / square_mm))
    row = int(math.floor((y_mm + half_height_mm - target.margin_mm) / square_mm))
    if not (0 <= col < target.squares_x and 0 <= row < target.squares_y):
        return MARGIN_SHADE
    if target.is_black_cell(col, row):
        return BLACK_SHADE
    marker_id = marker_layout.get((col, row))
    if marker_id is None:
        return WHITE_SHADE
    center_x, center_y = target.cell_center_mm(col, row)
    left = center_x - target.marker_mm / 2.0
    top = center_y - target.marker_mm / 2.0
    mx = int(math.floor((x_mm - left) / module_mm))
    my = int(math.floor((y_mm - top) / module_mm))
    if not (0 <= mx < module_count and 0 <= my < module_count):
        return WHITE_SHADE
    return module_shade(module_bits[marker_id], mx, my, border_bits, module_count)


def _split_axis(value: float, half: float, index: int) -> list[tuple[float, float]]:
    """把 ``[value-half, value+half]`` 按整格边界切成 1~2 段，返回 ``[(段权重, 段中点)]``。"""
    span = 2.0 * half
    parts: list[tuple[float, float]] = []
    left_spill = index - (value - half)
    if left_spill > 0.0:
        parts.append((left_spill / span, ((value - half) + index) / 2.0))
    middle_low = max(index, value - half)
    middle_high = min(index + 1, value + half)
    if middle_high > middle_low:
        parts.append(((middle_high - middle_low) / span, (middle_low + middle_high) / 2.0))
    right_spill = (value + half) - (index + 1)
    if right_spill > 0.0:
        parts.append((right_spill / span, ((index + 1) + (value + half)) / 2.0))
    return parts


def _pixel_shade(
    target: CalibTarget,
    x_mm: float,
    y_mm: float,
    *,
    half_width_mm: float,
    half_height_mm: float,
    half_pixel_x: float,
    half_pixel_y: float,
    marker_layout: dict[tuple[int, int], int],
    module_bits: dict[int, tuple[tuple[str, ...], ...]],
    module_mm: float,
    module_count: int,
    border_bits: int,
) -> float:
    """一个像素的灰度 = 该像素足迹内「黑格/白格/留边/模块」的**面积平均**。

    这是合成图能做亚像素角点定位的关键：足迹按格边界切成至多 4 段、每段取中点求值，就等价于
    对分段常数图案做精确的面积平均，于是边缘会呈现一段宽度正好是一个像素的线性过渡，
    亚像素边缘拟合（``refine_quad``）才能把角点定位到 0.1 px 以内。直接对高分辨率纹理做
    双线性采样是不行的：那会把 25 mm 的方块压缩到 ~30 px，边缘变成「阶跃」，亚像素信息全丢。
    """
    square_mm = target.square_mm
    u = (x_mm + half_width_mm - target.margin_mm) / square_mm
    v = (y_mm + half_height_mm - target.margin_mm) / square_mm
    hu = half_pixel_x / square_mm
    hv = half_pixel_y / square_mm
    columns = _split_axis(u, hu, int(math.floor(u)))
    rows = _split_axis(v, hv, int(math.floor(v)))
    total = 0.0
    for column_weight, column_center in columns:
        x = column_center * square_mm - half_width_mm + target.margin_mm
        for row_weight, row_center in rows:
            y = row_center * square_mm - half_height_mm + target.margin_mm
            total += column_weight * row_weight * _shade_at(
                target,
                x,
                y,
                half_width_mm=half_width_mm,
                half_height_mm=half_height_mm,
                marker_layout=marker_layout,
                module_bits=module_bits,
                module_mm=module_mm,
                module_count=module_count,
                border_bits=border_bits,
            )
    return total


def render_capture(
    target: CalibTarget,
    *,
    pose: geometry.Pose,
    lens: LensModel,
    width: int,
    height: int,
    capture_id: str = "capture",
    background_shade: int = 96,
    gain: float = 1.0,
    vignette: float = 0.0,
    noise_sigma: float = 0.0,
    blur_radius_px: int = 0,
    seed: int = 0,
    aruco: ArucoDictionary | None = None,
) -> CalibrationCapture:
    """渲染一张**带镜头畸变**的标定板拍摄图，并同时给出角点真值。

    ``lens`` 是**真值**：内参 + 畸变系数都由它给出，渲染与真值用的是同一套参数，因此
    「标定能不能把真值找回来」是这道题唯一的正确性判据。
    """
    if width <= 2 or height <= 2:
        raise ValueError(f"画面尺寸太小：{width}x{height}")
    dictionary = None
    marker_layout: dict[tuple[int, int], int] = {}
    module_bits: dict[int, tuple[tuple[str, ...], ...]] = {}
    module_mm = 0.0
    module_count = 0
    border_bits = 0
    if target.kind == "charuco":
        dictionary = aruco or target._dictionary()
        marker_layout = target.marker_layout()
        border_bits = dictionary.border_bits
        module_count = dictionary.marker_modules + 2 * border_bits
        module_mm = target.marker_mm / module_count
        module_bits = {marker_id: dictionary.matrix(marker_id) for marker_id in marker_layout.values()}

    camera = geometry.Camera(fx=lens.fx, fy=lens.fy, cx=lens.cx, cy=lens.cy)
    plane_to_image = geometry.plane_homography(camera, pose)
    image_to_plane = geometry.mat_inv(plane_to_image)
    half_width_mm = target.board_width_mm / 2.0
    half_height_mm = target.board_height_mm / 2.0
    scale_x, scale_y = _plane_scale(plane_to_image, lens)

    ideal_corners = [
        geometry.apply_homography(plane_to_image, x, y) for x, y in target.board_outline_mm()
    ]
    bbox = (
        min(point[0] for point in ideal_corners) - 2.0,
        min(point[1] for point in ideal_corners) - 2.0,
        max(point[0] for point in ideal_corners) + 2.0,
        max(point[1] for point in ideal_corners) + 2.0,
    )

    col_x, row_y, map_x, map_y = map_grid(width, height, lens.undistort)
    ideal_x, ideal_y = _grid_maps(width, height, col_x, row_y, map_x, map_y)

    canvas = Raster.filled(width, height, background_shade)
    pixels = canvas.pixels
    m = image_to_plane
    m00, m01, m02 = m[0][0], m[0][1], m[0][2]
    m10, m11, m12 = m[1][0], m[1][1], m[1][2]
    m20, m21, m22 = m[2][0], m[2][1], m[2][2]
    bx0, by0, bx1, by1 = bbox
    half_pixel_x = 0.5 * scale_x
    half_pixel_y = 0.5 * scale_y
    for y in range(height):
        base = y * width
        for x in range(width):
            qx = ideal_x[base + x]
            qy = ideal_y[base + x]
            if qx < bx0 or qx > bx1 or qy < by0 or qy > by1:
                continue
            w = m20 * qx + m21 * qy + m22
            if -1e-12 < w < 1e-12:
                continue
            plane_x = (m00 * qx + m01 * qy + m02) / w
            plane_y = (m10 * qx + m11 * qy + m12) / w
            if not (-half_width_mm <= plane_x <= half_width_mm):
                continue
            if not (-half_height_mm <= plane_y <= half_height_mm):
                continue
            value = _pixel_shade(
                target,
                plane_x,
                plane_y,
                half_width_mm=half_width_mm,
                half_height_mm=half_height_mm,
                half_pixel_x=half_pixel_x,
                half_pixel_y=half_pixel_y,
                marker_layout=marker_layout,
                module_bits=module_bits,
                module_mm=module_mm,
                module_count=module_count,
                border_bits=border_bits,
            )
            if value <= 0.0:
                pixels[base + x] = 0
            elif value >= 255.0:
                pixels[base + x] = 255
            else:
                pixels[base + x] = int(value + 0.5)

    if blur_radius_px:
        box_blur(canvas, blur_radius_px)
    apply_lighting(
        canvas,
        gain=gain,
        vignette=vignette,
        noise_sigma=noise_sigma,
        rng=random.Random(seed),
    )

    truth = _build_truth(
        target=target,
        pose=pose,
        lens=lens,
        capture_id=capture_id,
        canvas=canvas,
        plane_homography=plane_to_image,
    )
    return CalibrationCapture(
        capture_id=capture_id,
        width=canvas.width,
        height=canvas.height,
        pixels=canvas.as_bytes(),
        ground_truth=truth,
        ground_truth_json=json.dumps(truth, ensure_ascii=False, indent=2) + "\n",
    )


def _build_truth(
    *,
    target: CalibTarget,
    pose: geometry.Pose,
    lens: LensModel,
    capture_id: str,
    canvas: Raster,
    plane_homography: geometry.Matrix3,
) -> dict[str, Any]:
    corners: list[dict[str, Any]] = []
    for corner_id, x_mm, y_mm in target.inner_corners():
        ideal = geometry.apply_homography(plane_homography, x_mm, y_mm)
        px = lens.distort(*ideal)
        inside = 0.0 <= px[0] < canvas.width and 0.0 <= px[1] < canvas.height
        corners.append(
            {
                "corner_id": corner_id,
                "object_mm": [round(x_mm, 6), round(y_mm, 6)],
                "ideal_px": [round(ideal[0], 6), round(ideal[1], 6)],
                "px": [round(px[0], 6), round(px[1], 6)],
                "inside_frame": inside,
            }
        )
    outline_distorted = [
        lens.distort(*geometry.apply_homography(plane_homography, x, y))
        for x, y in target.board_outline_mm()
    ]
    pattern_outline = [
        lens.distort(*geometry.apply_homography(plane_homography, x, y))
        for x, y in target.pattern_outline_mm()
    ]
    # 可见性以**棋盘本身**为准：白色留边被切掉不影响标定，图案缺一块才会少角点。
    fully_visible = all(
        0.0 <= px < size
        for point in pattern_outline
        for px, size in ((point[0], canvas.width), (point[1], canvas.height))
    )
    board_fully_visible = all(
        0.0 <= px < size
        for point in outline_distorted
        for px, size in ((point[0], canvas.width), (point[1], canvas.height))
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": GROUND_TRUTH_KIND,
        "capture_id": capture_id,
        "target": target.to_dict(),
        "image": {
            "width": canvas.width,
            "height": canvas.height,
            "pixel_sha256": hashlib.sha256(canvas.as_bytes()).hexdigest(),
        },
        "true_lens": {
            "intrinsics": {
                "fx": round(lens.fx, 6),
                "fy": round(lens.fy, 6),
                "cx": round(lens.cx, 6),
                "cy": round(lens.cy, 6),
            },
            "distortion": {
                "model": lens.model,
                "k1": lens.k1,
                "k2": lens.k2,
                "p1": lens.p1,
                "p2": lens.p2,
            },
        },
        "pose": pose.to_dict(),
        "board_outline_px": [[round(x, 6), round(y, 6)] for x, y in outline_distorted],
        "pattern_outline_px": [[round(x, 6), round(y, 6)] for x, y in pattern_outline],
        "fully_visible": fully_visible,
        "board_fully_visible": board_fully_visible,
        "corners": corners,
    }
