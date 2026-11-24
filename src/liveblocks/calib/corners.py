"""标定板角点检测：棋盘格与 ChArUco 两条流程（纯标准库，复用 LB-003 的二值化与亚像素细化）。

共同点：一块标定板上的**黑格**在 4 连通意义下彼此独立（黑白格只在角点处「点接触」，
自适应二值化会把那条亚像素桥也判成暗，所以先做 1 像素腐蚀把它断开），因此每个黑格就是一个
独立的连通域，可以用 LB-003 已有的「凸包 → 四边形化简 → 灰度亚像素边缘细化」量到约 0.2 px。
相邻两个黑格**共享**一个内角点，所以每个内角点先拿到 2 个独立估计，取平均后再用。

**为什么不用单应来编号**：镜头畸变让「板平面 → 图像」在整个画面上不是单应。用单应给角点
编号时，边缘处 4~8 px 的径向偏移会在画面中部变成半格以上的误差，足够把编号错开一格。
这里改用**纯拓扑**的做法：两个黑格共享一个角点 ⟺ 它们在棋盘上对角相邻。从一个靠近画面
中心的黑格出发做广度优先搜索，用「第 k 个角点对应 +1/−1 的方向偏移」把整块棋盘的编号铺开，
全程只用局部的角点对应关系，与畸变无关。铺完后只差一个「整块棋盘的旋转/镜像」自由度，
用「格号必须落在范围内、必须全是黑格、跨度必须与目标一致」三条硬条件穷举 8 种朝向即可定死。

两条流程的差别：

- **棋盘格**：只有上面的拓扑编号。
- **ChArUco**：在此之上再用 LB-003 检出的 ArUco 标记做**交叉核对**——每个标记都该落在
  某个白格里，白格的标记 id 由板定义唯一确定，于是「检出的标记 id」与「编号推出来的 id」
  应当逐一对上；对不上报 ``LB-B004``（error，结果不可用于标定）。这一步是**独立于拓扑编号**
  的第二道校验：拓扑编号只保证「格子关系自洽」，标记 id 才能证明「编号没整体错位」。

输出的角点是**拍摄图（带畸变）里的像素坐标**，与真值 ``corners[].px`` 同一口径。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from ..synth.calibtarget import CalibTarget
from ..synth.dictionaries import ArucoDictionary, load_aruco_dictionaries
from ..vision.frame import Frame
from ..vision.image import (
    GrayImage,
    Point,
    adaptive_binary,
    connected_components,
    convex_hull,
    is_convex_quad,
    polygon_area,
    refine_quad,
    side_lengths,
    simplify_to_quad,
)
from ..vision.markers import detect_markers
from .codes import diagnostic
from .solve import Observation, homography_dlt

#: 黑格候选的最小边长（像素）/ 长宽比 / 填充率下限（黑格是实心，填充率接近 0.9）。
MIN_SIDE_PX = 6.0
MIN_ASPECT = 0.35
MAX_ASPECT = 2.8
MIN_FILL_RATIO = 0.5
#: 边长相对中位数的允许范围：同一块板上的格子大小相近，偏差过大的是背景碎片。
MIN_RELATIVE_SIDE = 0.55
MAX_RELATIVE_SIDE = 1.8
#: 黑格四周必须能看到「亮邻居」的最小灰度差：黑格的四邻里有白格，纯背景碎片没有。
MIN_NEIGHBOUR_CONTRAST = 60.0
#: 亚像素细化时沿外法向的搜索半径与允许的角点位移（粗四边形来自凸包，误差可达 4 px）。
REFINE_REACH_PX = 3.5
REFINE_MAX_SHIFT_PX = 5.0

#: 角点聚类容差 = max(下限, 黑格边长的这个比例)：同一个角点的两个黑格估计必须并到一起。
CLUSTER_RATIO = 0.09
CLUSTER_MIN_PX = 1.5

#: 一个黑格的 4 个角点相对该格的「对角邻居偏移」，顺序与四边形顶点顺序一一对应（见模块文档）。
CORNER_NEIGHBOUR_OFFSETS = ((-1, -1), (1, -1), (1, 1), (-1, 1))
#: 一个黑格的 4 个角点对应的**格点**编号偏移（格点编号，不是格编号）。
CORNER_LATTICE_OFFSETS = ((0, 0), (1, 0), (1, 1), (0, 1))


@dataclass(frozen=True)
class Square:
    """一个黑格：细化后的四边形 + 判定用的度量。

    ``quad`` 的顶点顺序是**规范化**过的：从「图像里最靠左上」（``x + y`` 最小）的那个角点起，
    按原来的环序排列。规范化是拓扑编号能跨格子一致的前提——三角形/四边形的起点本身没有
    全局含义，不统一的话「第 k 个角点朝哪个方向的对角格」就是随机的。
    """

    quad: tuple[Point, Point, Point, Point]
    pixel_count: int
    fill_ratio: float

    @classmethod
    def from_quad(cls, quad: list[Point], *, pixel_count: int, fill_ratio: float) -> "Square":
        start = min(range(4), key=lambda index: quad[index][0] + quad[index][1])
        ordered = tuple(quad[(start + offset) % 4] for offset in range(4))
        return cls(
            quad=(ordered[0], ordered[1], ordered[2], ordered[3]),
            pixel_count=pixel_count,
            fill_ratio=fill_ratio,
        )

    @property
    def center(self) -> Point:
        return (
            sum(point[0] for point in self.quad) / 4.0,
            sum(point[1] for point in self.quad) / 4.0,
        )


@dataclass(frozen=True)
class CornerSet:
    """一帧的角点检测结果。"""

    target: CalibTarget
    corners: dict[int, Point]
    method: str
    squares: int
    diagnostics: tuple[dict[str, Any], ...] = ()
    markers: int = 0
    marker_matches: int = 0

    def __post_init__(self) -> None:
        expected = len(self.target.inner_corners())
        if len(self.corners) > expected:
            raise ValueError(f"角点数 {len(self.corners)} 超过目标定义的内角点数 {expected}")

    @property
    def missing(self) -> list[int]:
        return [
            corner_id for corner_id in self.target.object_points() if corner_id not in self.corners
        ]

    def observation(self, *, width: int, height: int) -> Observation:
        points = tuple(
            (corner_id, self.corners[corner_id][0], self.corners[corner_id][1])
            for corner_id in sorted(self.corners)
        )
        return Observation(width=width, height=height, points=points)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": "CalibrationCorners",
            "target": self.target.name,
            "method": self.method,
            "squares": self.squares,
            "markers": self.markers,
            "marker_matches": self.marker_matches,
            "expected": len(self.target.inner_corners()),
            "detected": len(self.corners),
            "corners": [
                {
                    "corner_id": corner_id,
                    "px": [
                        round(self.corners[corner_id][0], 4),
                        round(self.corners[corner_id][1], 4),
                    ],
                }
                for corner_id in sorted(self.corners)
            ],
            "diagnostics": [dict(entry) for entry in self.diagnostics],
        }


# --- 黑格检出 -------------------------------------------------------------------------


def _erode4(mask: bytearray, width: int, height: int) -> bytearray:
    """4 邻域腐蚀 1 像素：断开「黑白格只在角点处点接触」的那条亚像素桥。

    不腐蚀的话，自适应二值化会把角点附近的过渡像素判成暗，相邻黑格就粘成一个连通域
    （实测 9×6 的棋盘会粘成 3 对「长条」，进而被几何条件误判成 24 个格子）。
    """
    out = bytearray(len(mask))
    for y in range(1, height - 1):
        base = y * width
        for x in range(1, width - 1):
            index = base + x
            if (
                mask[index]
                and mask[index - 1]
                and mask[index + 1]
                and mask[index - width]
                and mask[index + width]
            ):
                out[index] = 1
    return out


def _dilate_pixels(component: list[int], mask: bytearray, width: int, height: int) -> list[int]:
    """把腐蚀后的连通域在**原掩码内**膨胀 1 像素，还原黑格的完整范围。"""
    grown: set[int] = set(component)
    for index in component:
        y, x = divmod(index, width)
        for dy in (-1, 0, 1):
            ny = y + dy
            if ny < 0 or ny >= height:
                continue
            base = ny * width
            for dx in (-1, 0, 1):
                nx = x + dx
                if nx < 0 or nx >= width:
                    continue
                neighbour = base + nx
                if mask[neighbour]:
                    grown.add(neighbour)
    return sorted(grown)


def detect_squares(
    image: GrayImage,
    *,
    block: int = 21,
    offset: int = 8,
    min_area: int = 80,
    exclude_quads: list[list[Point]] | None = None,
) -> tuple[list[Square], list[dict[str, Any]]]:
    """检出画面里所有「实心黑方块」并做亚像素细化。"""
    mask = adaptive_binary(image, block=block, offset=offset)
    eroded = _erode4(mask, image.width, image.height)
    components = connected_components(
        eroded, image.width, image.height, min_area=max(8, min_area // 2), connectivity=4
    )
    squares: list[Square] = []
    rejected = 0
    excluded_count = 0
    for component in components:
        filled = _dilate_pixels(component, mask, image.width, image.height)
        points: list[Point] = [
            (float(pixel % image.width), float(pixel // image.width)) for pixel in filled
        ]
        hull = convex_hull(points)
        if len(hull) < 4:
            rejected += 1
            continue
        quad = simplify_to_quad(hull)
        if len(quad) != 4 or not is_convex_quad(quad):
            rejected += 1
            continue
        sides = side_lengths(quad)
        longest = max(sides)
        if min(sides) < MIN_SIDE_PX or longest <= 0.0:
            rejected += 1
            continue
        if not MIN_ASPECT <= min(sides) / longest <= MAX_ASPECT:
            rejected += 1
            continue
        area = polygon_area(quad)
        if area <= 0.0:
            rejected += 1
            continue
        fill_ratio = len(filled) / area
        if fill_ratio < MIN_FILL_RATIO:
            rejected += 1
            continue
        if exclude_quads and any(
            _inside_quad(_square_center(quad), excluded) for excluded in exclude_quads
        ):
            # ChArUco 的 ArUco 标记（黑边环）也会被 4 连通域挑出来，但它不是棋盘黑格。
            excluded_count += 1
            continue
        black_level, white_level = _local_levels(image, filled, quad)
        if _neighbour_contrast(image, quad, black_level) < MIN_NEIGHBOUR_CONTRAST:
            # 四周全是同一种灰度：这不是黑格，而是「自适应二值化」在亮板旁边劈出来的一片背景。
            rejected += 1
            continue
        refined = refine_quad(
            image,
            quad,
            black_level=black_level,
            white_level=white_level,
            reach=REFINE_REACH_PX,
            max_corner_shift=REFINE_MAX_SHIFT_PX,
        )
        if refined == quad:
            # 退化情形（边缘被重采样糊掉、跨次噪声相关）：放大搜索半径再试一次。
            # 还不行就用凸包四边形当结果——宁可多 1 px 误差，也不丢格子。
            refined = refine_quad(
                image,
                quad,
                black_level=black_level,
                white_level=white_level,
                reach=REFINE_REACH_PX * 2.0,
                max_corner_shift=REFINE_MAX_SHIFT_PX * 1.5,
            )
        squares.append(
            Square.from_quad(refined, pixel_count=len(filled), fill_ratio=round(fill_ratio, 4))
        )

    squares = _drop_outliers(squares)
    diagnostics: list[dict[str, Any]] = []
    if not squares:
        diagnostics.append(
            diagnostic(
                "LB-B001",
                "error",
                f"画面里没有检出任何黑格（4 连通域候选 {len(components)} 个，被几何条件剔除 "
                f"{rejected} 个）：标定板可能不在画面里或对比度不足",
            )
        )
    return squares, diagnostics


def _square_center(quad: list[Point]) -> Point:
    return (sum(point[0] for point in quad) / 4.0, sum(point[1] for point in quad) / 4.0)


def _inside_quad(point: Point, quad: list[Point]) -> bool:
    """点是否落在四边形内（凸四边形，允许边界）。"""
    sign = 0
    n = len(quad)
    for index in range(n):
        x0, y0 = quad[index]
        x1, y1 = quad[(index + 1) % n]
        cross = (x1 - x0) * (point[1] - y0) - (y1 - y0) * (point[0] - x0)
        if cross > 1e-9:
            if sign < 0:
                return False
            sign = 1
        elif cross < -1e-9:
            if sign > 0:
                return False
            sign = -1
    return True


def _neighbour_contrast(image: GrayImage, quad: list[Point], black_level: float) -> float:
    """沿四边形外法向取一圈采样，返回「最亮的邻居」与黑格灰度的差。"""
    center_x = sum(point[0] for point in quad) / 4.0
    center_y = sum(point[1] for point in quad) / 4.0
    brightest = black_level
    for index in range(4):
        a = quad[index]
        b = quad[(index + 1) % 4]
        ex, ey = b[0] - a[0], b[1] - a[1]
        length = math.hypot(ex, ey)
        if length < 1e-9:
            continue
        nx, ny = -ey / length, ex / length
        mid_x, mid_y = (a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0
        if (mid_x - center_x) * nx + (mid_y - center_y) * ny < 0.0:
            nx, ny = -nx, -ny
        for step in (1.5, 3.0):
            for k in range(1, 6):
                t = k / 6.0
                x = a[0] + ex * t + nx * step
                y = a[1] + ey * t + ny * step
                value = image.sample(x, y)
                if value > brightest:
                    brightest = value
    return brightest - black_level


def _drop_outliers(squares: list[Square]) -> list[Square]:
    """按「边长相对中位数」剔除明显不是格子的候选（背景碎片、被切掉的碎块）。"""
    if len(squares) < 3:
        return squares
    sides = sorted(min(side_lengths(list(square.quad))) for square in squares)
    median = sides[len(sides) // 2]
    if median <= 0.0:
        return squares
    return [
        square
        for square in squares
        if MIN_RELATIVE_SIDE * median
        <= min(side_lengths(list(square.quad)))
        <= MAX_RELATIVE_SIDE * median
    ]


def _local_levels(image: GrayImage, component: list[int], quad: list[Point]) -> tuple[float, float]:
    """用「黑格本身」与「同一包围盒里的非黑格像素」估这一块的黑白灰度。"""
    width = image.width
    xs = [pixel % width for pixel in component]
    ys = [pixel // width for pixel in component]
    x0, x1 = max(0, min(xs)), min(image.width - 1, max(xs))
    y0, y1 = max(0, min(ys)), min(image.height - 1, max(ys))
    inside = set(component)
    dark_sum = 0.0
    dark_count = 0
    light_sum = 0.0
    light_count = 0
    for y in range(y0, y1 + 1):
        base = y * width
        for x in range(x0, x1 + 1):
            value = image.pixel(x, y)
            if base + x in inside:
                dark_sum += value
                dark_count += 1
            else:
                light_sum += value
                light_count += 1
    black = dark_sum / dark_count if dark_count else 0.0
    white = light_sum / light_count if light_count else float(black + 64.0)
    if white - black < 16.0:
        # 包围盒里几乎没有亮像素（例如黑格彼此贴边）：退化成「黑格均值 + 固定差」
        white = black + 64.0
    return black, white


# --- 拓扑编号：角点聚类 + 广度优先铺格 -------------------------------------------------


def _cluster_corners(squares: list[Square]) -> tuple[list[list[int]], list[Point], int]:
    """把全部黑格角点聚成「唯一的几何角点」。

    返回 ``(cluster_of[i][k], centers, max_cluster_size)``：第 ``i`` 个黑格的第 ``k`` 个角点
    属于哪个簇、每个簇的平均坐标、以及一个簇最多被几个黑格共用（正常是 2）。
    """
    sides = sorted(
        min(side_lengths(list(square.quad))) for square in squares
    )
    median_side = sides[len(sides) // 2] if sides else 0.0
    tolerance = max(CLUSTER_MIN_PX, CLUSTER_RATIO * median_side)
    sums: list[list[float]] = []  # [sum_x, sum_y, count]
    cluster_of: list[list[int]] = []
    for square in squares:
        row: list[int] = []
        for point in square.quad:
            for index, cluster in enumerate(sums):
                cx = cluster[0] / cluster[2]
                cy = cluster[1] / cluster[2]
                if math.hypot(point[0] - cx, point[1] - cy) <= tolerance:
                    cluster[0] += point[0]
                    cluster[1] += point[1]
                    cluster[2] += 1
                    row.append(index)
                    break
            else:
                sums.append([point[0], point[1], 1.0])
                row.append(len(sums) - 1)
        cluster_of.append(row)
    centers = [(cluster[0] / cluster[2], cluster[1] / cluster[2]) for cluster in sums]
    largest = max((int(cluster[2]) for cluster in sums), default=0)
    return cluster_of, centers, largest


def _walk_lattice(
    squares: list[Square], cluster_of: list[list[int]]
) -> tuple[dict[int, tuple[int, int]], dict[tuple[int, int], tuple[int, int]], int]:
    """用广度优先铺出每个黑格的格编号。

    从**每个**还没访问过的黑格各铺一次（多源），取「连成一片的黑格最多」的那一块，其余当作
    碎片丢弃（例如 ChArUco 上没被译码的标记黑边环，它们和棋盘没有共享角点）。
    返回 ``(cell_of[i] = (col, row), lattice_of[(i, k)] = (i_point, j_point), 被丢弃的黑格数)``。
    """
    if not squares:
        return {}, {}, 0
    index_by_cluster: dict[int, list[tuple[int, int]]] = {}
    for square_index, clusters in enumerate(cluster_of):
        for corner_index, cluster in enumerate(clusters):
            index_by_cluster.setdefault(cluster, []).append((square_index, corner_index))

    components: list[dict[int, tuple[int, int]]] = []
    assigned: set[int] = set()
    for seed in range(len(squares)):
        if seed in assigned:
            continue
        cell_of: dict[int, tuple[int, int]] = {seed: (0, 0)}
        queue = [seed]
        while queue:
            current = queue.pop(0)
            col, row = cell_of[current]
            for corner_index, cluster in enumerate(cluster_of[current]):
                for square_index, _other_corner in index_by_cluster.get(cluster, ()):
                    if square_index == current or square_index in cell_of:
                        continue
                    dcol, drow = CORNER_NEIGHBOUR_OFFSETS[corner_index]
                    cell_of[square_index] = (col + dcol, row + drow)
                    queue.append(square_index)
        assigned.update(cell_of)
        components.append(cell_of)

    best = max(components, key=lambda component: (len(component), -min(component)))
    dropped = sum(len(component) for component in components) - len(best)
    lattice_of: dict[tuple[int, int], tuple[int, int]] = {}
    for square_index, (col, row) in best.items():
        for corner_index in range(4):
            dcol, drow = CORNER_LATTICE_OFFSETS[corner_index]
            lattice_of[(square_index, corner_index)] = (col + dcol, row + drow)
    return best, lattice_of, dropped


def _variants() -> list[tuple[str, Any]]:
    """棋盘格编号的 8 种「旋转 / 镜像」变换（保持黑格与角点的对应关系）。"""
    return [
        ("identity", lambda i, j: (i, j)),
        ("rot90", lambda i, j: (-j, i)),
        ("rot180", lambda i, j: (-i, -j)),
        ("rot270", lambda i, j: (j, -i)),
        ("mirror", lambda i, j: (j, i)),
        ("mirror+rot90", lambda i, j: (-i, j)),
        ("mirror+rot180", lambda i, j: (-j, -i)),
        ("mirror+rot270", lambda i, j: (i, -j)),
    ]


def _apply_variant(
    name: str,
    transform,
    target: CalibTarget,
    cell_of: dict[int, tuple[int, int]],
    lattice_of: dict[tuple[int, int], tuple[int, int]],
) -> tuple[dict[int, tuple[int, int]], dict[tuple[int, int], tuple[int, int]]] | None:
    """按一个变换重整编号，并检查「范围 / 黑格 / 跨度」三条硬条件；不合格返回 ``None``。"""
    mapped_cells = {index: transform(col, row) for index, (col, row) in cell_of.items()}
    min_col = min(cell[0] for cell in mapped_cells.values())
    min_row = min(cell[1] for cell in mapped_cells.values())
    cells: dict[int, tuple[int, int]] = {}
    for index, (col, row) in mapped_cells.items():
        shifted = (col - min_col, row - min_row)
        if not (0 <= shifted[0] < target.squares_x and 0 <= shifted[1] < target.squares_y):
            return None
        if not target.is_black_cell(*shifted):
            return None
        cells[index] = shifted
    if len(set(cells.values())) != len(cells):
        return None
    # 跨度必须与目标一致：9 列 × 6 行（转置会变成 6 列 × 9 行，因此必须挡掉）
    cols = [col for col, _row in cells.values()]
    rows = [row for _col, row in cells.values()]
    if max(cols) - min(cols) + 1 != target.squares_x:
        return None
    if max(rows) - min(rows) + 1 != target.squares_y:
        return None
    lattice: dict[tuple[int, int], tuple[int, int]] = {}
    for key, (point_i, point_j) in lattice_of.items():
        mapped = transform(point_i, point_j)
        lattice[key] = (mapped[0] - min_col, mapped[1] - min_row)
    return cells, lattice


def _expected_cell_count(target: CalibTarget) -> int:
    return sum(
        1
        for row in range(target.squares_y)
        for col in range(target.squares_x)
        if target.is_black_cell(col, row)
    )


def _select_variant(
    target: CalibTarget,
    cell_of: dict[int, tuple[int, int]],
    lattice_of: dict[tuple[int, int], tuple[int, int]],
) -> tuple[str, dict[int, tuple[int, int]], dict[tuple[int, int], tuple[int, int]]] | None:
    """穷举 8 种朝向，取「黑格数最多 + 覆盖整块板」的那个。"""
    if not cell_of:
        return None
    expected = _expected_cell_count(target)
    best: tuple[int, str, dict, dict] | None = None
    for name, transform in _variants():
        applied = _apply_variant(name, transform, target, cell_of, lattice_of)
        if applied is None:
            continue
        cells, lattice = applied
        score = len(cells)
        if score == expected:
            return name, cells, lattice
        if best is None or score > best[0]:
            best = (score, name, cells, lattice)
    if best is None:
        return None
    return best[1], best[2], best[3]


# --- ChArUco 标记交叉核对 -------------------------------------------------------------


def _inverse(h: list[float]) -> list[list[float]]:
    """9 元素（行优先）单应 → 3×3 逆矩阵（嵌套列表，供 :func:`_project` 用）。"""
    (a, b, c), (d, e, f), (g, i, j) = (
        (h[0], h[1], h[2]),
        (h[3], h[4], h[5]),
        (h[6], h[7], h[8]),
    )
    det = a * (e * j - f * i) - b * (d * j - f * g) + c * (d * i - e * g)
    if abs(det) < 1e-18:
        raise ValueError("单应不可逆")
    inv_det = 1.0 / det
    return [
        [(e * j - f * i) * inv_det, (c * i - b * j) * inv_det, (b * f - c * e) * inv_det],
        [(f * g - d * j) * inv_det, (a * j - c * g) * inv_det, (c * d - a * f) * inv_det],
        [(d * i - e * g) * inv_det, (b * g - a * i) * inv_det, (a * e - b * d) * inv_det],
    ]


def _project(m: list[list[float]], x: float, y: float) -> Point:
    """用 3×3 矩阵（嵌套列表）把点投到目标平面。"""
    w = m[2][0] * x + m[2][1] * y + m[2][2]
    if abs(w) < 1e-12:
        raise ValueError("反投影退化：w ≈ 0")
    return (
        (m[0][0] * x + m[0][1] * y + m[0][2]) / w,
        (m[1][0] * x + m[1][1] * y + m[1][2]) / w,
    )


def _marker_cells(
    target: CalibTarget,
    detected_markers: list[Any],
    cells: dict[int, tuple[int, int]],
    square_centers: list[Point],
) -> tuple[int, int, list[dict[str, Any]]]:
    """用检出的 ArUco 标记核对棋盘编号：返回 ``(检出数, 对上数, 诊断)``。

    做法：用「已被编号的黑格中心（毫米）→ 观测中心（像素）」拟合一个单应，把每个标记中心
    反投影成格坐标并取整，再查板定义里该格应有的标记 id。畸变让这个单应不完美（残差几个像素），
    但格子有 20 mm（约 30 px），取整仍然稳。
    """
    diagnostics: list[dict[str, Any]] = []
    expected_ids = target.marker_layout()
    sources: list[Point] = []
    targets: list[Point] = []
    for index, (col, row) in cells.items():
        sources.append(target.cell_center_mm(col, row))
        targets.append(square_centers[index])
    if len(sources) < 4:
        return len(detected_markers), 0, diagnostics
    try:
        homography = homography_dlt(sources, targets)
        inverse = _inverse(homography)
    except (ValueError, ZeroDivisionError) as error:
        diagnostics.append(
            diagnostic("LB-B004", "warning", f"标记核对跳过：黑格单应不可解（{error}）")
        )
        return len(detected_markers), 0, diagnostics

    offset_x = target.margin_mm - target.board_width_mm / 2.0
    offset_y = target.margin_mm - target.board_height_mm / 2.0
    matches = 0
    usable = 0
    for marker in detected_markers:
        if marker.marker_id is None:
            continue
        try:
            x_mm, y_mm = _project(inverse, marker.center_px[0], marker.center_px[1])
        except ValueError:
            continue
        col = int(math.floor((x_mm - offset_x) / target.square_mm))
        row = int(math.floor((y_mm - offset_y) / target.square_mm))
        expected_id = expected_ids.get((col, row))
        if expected_id is None:
            diagnostics.append(
                diagnostic(
                    "LB-B004",
                    "warning",
                    f"标记 {marker.marker_id} 反投影到格 ({col},{row})，该格在目标定义里不是标记格",
                )
            )
            continue
        usable += 1
        if expected_id == marker.marker_id:
            matches += 1
        else:
            diagnostics.append(
                diagnostic(
                    "LB-B004",
                    "warning",
                    f"标记 id 对不上：格 ({col},{row}) 应为标记 {expected_id}，"
                    f"实际检出 {marker.marker_id}",
                )
            )
    if usable and matches < usable:
        diagnostics.append(
            diagnostic(
                "LB-B004",
                "error",
                f"ChArUco 标记与编号对不上：{matches}/{usable} 个标记 id 一致，"
                "编号可能整体错位（结果不可用于标定）",
            )
        )
    return len(detected_markers), matches, diagnostics


# --- 对外入口 -------------------------------------------------------------------------


def detect_corners(
    image: GrayImage,
    target: CalibTarget,
    *,
    aruco: ArucoDictionary | None = None,
    squares: list[Square] | None = None,
) -> CornerSet:
    """检出标定板内角点（带畸变的像素坐标），返回 :class:`CornerSet`。"""
    diagnostics: list[dict[str, Any]] = []
    detected_markers: list[Any] = []
    method = "checkerboard-grid"
    if target.kind == "charuco":
        dictionary = aruco or _target_dictionary(target)
        frame = Frame(frame_id="calib-frame", image=image, image_ref="calib-frame")
        detected = detect_markers(frame, aruco=dictionary)
        detected_markers = list(detected.markers)
        method = "charuco-grid+markers"

    if squares is None:
        squares, square_diagnostics = detect_squares(
            image,
            exclude_quads=[
                list(marker.quad) for marker in detected_markers if marker.marker_id is not None
            ],
        )
    else:
        square_diagnostics = []
    diagnostics.extend(square_diagnostics)
    if not squares:
        return CornerSet(
            target=target, corners={}, method=method, squares=0, diagnostics=tuple(diagnostics)
        )

    cluster_of, centers, largest_cluster = _cluster_corners(squares)
    cell_of, lattice_of, dropped = _walk_lattice(squares, cluster_of)
    if largest_cluster > 2:
        diagnostics.append(
            diagnostic(
                "LB-B002",
                "warning",
                f"有角点被 {largest_cluster} 个黑格共用（正常 ≤ 2）：角点聚类可能把相邻角点并到了一起",
            )
        )
    if dropped:
        diagnostics.append(
            diagnostic(
                "LB-B002",
                "warning",
                f"有 {dropped} 个黑格与最大的棋盘块不连通（编号铺不到），已忽略",
                dropped=dropped,
            )
        )

    selected = _select_variant(target, cell_of, lattice_of)
    if selected is None:
        diagnostics.append(
            diagnostic(
                "LB-B002",
                "error",
                f"格点编号失败：{len(squares)} 个黑格的相对关系与目标 {target.squares_x}×"
                f"{target.squares_y} 棋盘不符",
            )
        )
        return CornerSet(
            target=target,
            corners={},
            method=method,
            squares=len(squares),
            diagnostics=tuple(diagnostics),
        )
    _variant_name, cells, lattice = selected

    markers = len(detected_markers)
    marker_matches = 0
    if target.kind == "charuco":
        _detected_count, marker_matches, marker_diagnostics = _marker_cells(
            target, detected_markers, cells, [square.center for square in squares]
        )
        diagnostics.extend(marker_diagnostics)

    expected = _expected_cell_count(target)
    if len(cells) < expected:
        diagnostics.append(
            diagnostic(
                "LB-B003",
                "warning",
                f"黑格缺失：{len(cells)}/{expected} 个黑格被编号（板没完整入画或被遮挡）",
                missing_cells=expected - len(cells),
            )
        )

    point_estimates: dict[tuple[int, int], list[Point]] = {}
    for square_index, clusters in enumerate(cluster_of):
        if square_index not in cells:
            continue
        for corner_index in range(4):
            key = lattice.get((square_index, corner_index))
            if key is None:
                continue
            point_estimates.setdefault(key, []).append(centers[clusters[corner_index]])

    offset_x = target.margin_mm - target.board_width_mm / 2.0
    offset_y = target.margin_mm - target.board_height_mm / 2.0
    corners: dict[int, Point] = {}
    for corner_id, x_mm, y_mm in target.inner_corners():
        i = int(round((x_mm - offset_x) / target.square_mm))
        j = int(round((y_mm - offset_y) / target.square_mm))
        estimates = point_estimates.get((i, j))
        if not estimates:
            continue
        corners[corner_id] = (
            sum(point[0] for point in estimates) / len(estimates),
            sum(point[1] for point in estimates) / len(estimates),
        )

    expected_corners = len(target.inner_corners())
    if len(corners) < expected_corners:
        diagnostics.append(
            diagnostic(
                "LB-B003",
                "warning",
                f"内角点缺失：{len(corners)}/{expected_corners}"
                f"（缺 {expected_corners - len(corners)} 个，通常是板没完整入画或被遮挡）",
                missing=expected_corners - len(corners),
            )
        )

    return CornerSet(
        target=target,
        corners=corners,
        method=method,
        squares=len(squares),
        diagnostics=tuple(diagnostics),
        markers=markers,
        marker_matches=marker_matches,
    )


def _target_dictionary(target: CalibTarget) -> ArucoDictionary:
    dictionaries = load_aruco_dictionaries()
    return dictionaries[target.dictionary]
