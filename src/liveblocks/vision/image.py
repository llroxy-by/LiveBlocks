"""灰度图基础运算：积分图、局部自适应二值化、连通域、凸包与四边形化简。

全部只用标准库 ``math``，不依赖 numpy / OpenCV：LB-002 的生成器是零依赖的，识别层保持同样
的性质，测试与 CI 才不需要额外安装（见 ``docs/DECISIONS.md`` D-013）。

约定：

- 像素 ``(x, y)`` 的中心在 ``(x + 0.5, y + 0.5)``；返回的角点坐标用**像素边界**表示，即
  ``(0, 0)`` 是左上像素的外角、``(width, height)`` 是右下像素的外角。这样「识别出的角点」与
  LB-002 真值里的 ``corners_px`` 可以直接比较（真值是按单应投影得到的连续坐标）。
- 所有遍历顺序固定（行优先），同样的输入必然得到同样的输出。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

Point = tuple[float, float]


@dataclass(frozen=True)
class GrayImage:
    """8bit 灰度图，行优先。"""

    width: int
    height: int
    pixels: bytes

    def __post_init__(self) -> None:
        if self.width <= 0 or self.height <= 0:
            raise ValueError(f"图像尺寸必须为正：{self.width}x{self.height}")
        if len(self.pixels) != self.width * self.height:
            raise ValueError(
                f"像素数不符：{self.width}x{self.height} 需要 "
                f"{self.width * self.height}，实际 {len(self.pixels)}"
            )

    def pixel(self, x: int, y: int) -> int:
        return self.pixels[y * self.width + x]

    def sample(self, x: float, y: float) -> float:
        """双线性插值采样（以像素中心为采样点，越界按边缘夹取）。"""
        x -= 0.5
        y -= 0.5
        x0 = int(x // 1)
        y0 = int(y // 1)
        tx = x - x0
        ty = y - y0
        return (
            self._clamped(x0, y0) * (1.0 - tx) * (1.0 - ty)
            + self._clamped(x0 + 1, y0) * tx * (1.0 - ty)
            + self._clamped(x0, y0 + 1) * (1.0 - tx) * ty
            + self._clamped(x0 + 1, y0 + 1) * tx * ty
        )

    def _clamped(self, x: int, y: int) -> float:
        if x < 0:
            x = 0
        elif x >= self.width:
            x = self.width - 1
        if y < 0:
            y = 0
        elif y >= self.height:
            y = self.height - 1
        return float(self.pixels[y * self.width + x])


def integral_image(image: GrayImage) -> list[int]:
    """积分图（尺寸 ``(width+1) * (height+1)``），用于 O(1) 求矩形和。"""
    w, h = image.width, image.height
    stride = w + 1
    table = [0] * (stride * (h + 1))
    pixels = image.pixels
    for y in range(h):
        row = (y + 1) * stride
        prev = y * stride
        acc = 0
        base = y * w
        for x in range(w):
            acc += pixels[base + x]
            table[row + x + 1] = table[prev + x + 1] + acc
    return table


def _rect_sum(table: list[int], stride: int, x0: int, y0: int, x1: int, y1: int) -> int:
    return table[y1 * stride + x1] - table[y0 * stride + x1] - table[y1 * stride + x0] + table[y0 * stride + x0]


def adaptive_binary(
    image: GrayImage, *, block: int = 21, offset: int = 8
) -> bytearray:
    """局部均值二值化，返回「暗于邻域均值 ``offset``」的掩码（1 = 暗）。

    ``block`` 是均值窗口边长（奇数）。局部分块均值同时适应均匀光与低光照（LB-002 的
    ``low-light`` 样例整幅图偏暗），比全局阈值稳健；代价是每像素一次矩形和查询。
    """
    if block < 3:
        raise ValueError(f"block 至少为 3：{block}")
    if block % 2 == 0:
        block += 1
    w, h = image.width, image.height
    stride = w + 1
    table = integral_image(image)
    radius = block // 2
    pixels = image.pixels
    mask = bytearray(w * h)
    for y in range(h):
        y0 = y - radius
        if y0 < 0:
            y0 = 0
        y1 = y + radius + 1
        if y1 > h:
            y1 = h
        row = y * w
        for x in range(w):
            x0 = x - radius
            if x0 < 0:
                x0 = 0
            x1 = x + radius + 1
            if x1 > w:
                x1 = w
            area = (x1 - x0) * (y1 - y0)
            mean = _rect_sum(table, stride, x0, y0, x1, y1) // area
            if pixels[row + x] <= mean - offset:
                mask[row + x] = 1
    return mask


def connected_components(mask: bytearray, width: int, height: int, *, min_area: int = 1) -> list[list[int]]:
    """8 连通的暗像素连通域，返回每个连通域的像素下标列表（按扫描顺序）。

    用显式栈做深度优先，避免递归深度限制；面积为 ``min_area`` 以下的连通域直接丢弃。
    """
    if len(mask) != width * height:
        raise ValueError("掩码尺寸与图像尺寸不符")
    seen = bytearray(width * height)
    components: list[list[int]] = []
    for start in range(width * height):
        if not mask[start] or seen[start]:
            continue
        seen[start] = 1
        stack = [start]
        pixels: list[int] = []
        while stack:
            index = stack.pop()
            pixels.append(index)
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
                    if mask[neighbour] and not seen[neighbour]:
                        seen[neighbour] = 1
                        stack.append(neighbour)
        if len(pixels) >= min_area:
            components.append(pixels)
    return components


def convex_hull(points: list[Point]) -> list[Point]:
    """Andrew 单调链凸包（逆时针，去掉重复端点）。"""
    if len(points) < 3:
        return list(points)
    ordered = sorted(set(points))

    def cross(o: Point, a: Point, b: Point) -> float:
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower: list[Point] = []
    for point in ordered:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], point) <= 0.0:
            lower.pop()
        lower.append(point)
    upper: list[Point] = []
    for point in reversed(ordered):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], point) <= 0.0:
            upper.pop()
        upper.append(point)
    return lower[:-1] + upper[:-1]


def polygon_area(polygon: list[Point]) -> float:
    """多边形面积（鞋带公式，取绝对值）。"""
    n = len(polygon)
    if n < 3:
        return 0.0
    total = 0.0
    for i in range(n):
        x0, y0 = polygon[i]
        x1, y1 = polygon[(i + 1) % n]
        total += x0 * y1 - x1 * y0
    return abs(total) / 2.0


def simplify_to_quad(hull: list[Point]) -> list[Point]:
    """把凸包化简成 4 个顶点：每次删掉「贡献三角形面积最小」的顶点。

    对投影后的 ArUco 标记（凸四边形）来说，这等价于找回 4 个真实角点；对圆形、长条形等
    非四边形形状，得到的是「最像四边形」的近似，后续的码字校验会把它们筛掉。
    """
    points = list(hull)
    if len(points) <= 4:
        return points
    while len(points) > 4:
        best_index = 0
        best_area = None
        count = len(points)
        for i in range(count):
            a = points[i - 1]
            b = points[i]
            c = points[(i + 1) % count]
            area = abs((b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]))
            if best_area is None or area < best_area:
                best_area = area
                best_index = i
        points.pop(best_index)
    return points


def side_lengths(quad: list[Point]) -> list[float]:
    n = len(quad)
    return [
        ((quad[(i + 1) % n][0] - quad[i][0]) ** 2 + (quad[(i + 1) % n][1] - quad[i][1]) ** 2) ** 0.5
        for i in range(n)
    ]


def is_convex_quad(quad: list[Point]) -> bool:
    """四个顶点是否构成凸四边形（按同一个方向绕行，所有叉积同号）。"""
    if len(quad) != 4:
        return False
    sign = 0.0
    for i in range(4):
        ax, ay = quad[i]
        bx, by = quad[(i + 1) % 4]
        cx, cy = quad[(i + 2) % 4]
        cross = (bx - ax) * (cy - by) - (by - ay) * (cx - bx)
        if abs(cross) <= 1e-9:
            continue
        if sign == 0.0:
            sign = cross
        elif cross * sign < 0.0:
            return False
    return True


def point_in_quad(point: Point, quad: list[Point]) -> bool:
    """点是否落在凸四边形内（含边界）。"""
    x, y = point
    n = len(quad)
    sign = 0
    for i in range(n):
        x0, y0 = quad[i]
        x1, y1 = quad[(i + 1) % n]
        cross = (x1 - x0) * (y - y0) - (y1 - y0) * (x - x0)
        if cross > 1e-9:
            if sign < 0:
                return False
            sign = 1
        elif cross < -1e-9:
            if sign > 0:
                return False
            sign = -1
    return True


def _line_through(points: list[Point]) -> tuple[float, float, float]:
    """对一组点做总体最小二乘直线拟合，返回单位法向 ``(a, b)`` 与常数 ``c``（``a*x + b*y + c = 0``）。"""
    n = len(points)
    mx = sum(point[0] for point in points) / n
    my = sum(point[1] for point in points) / n
    sxx = sum((point[0] - mx) ** 2 for point in points)
    syy = sum((point[1] - my) ** 2 for point in points)
    sxy = sum((point[0] - mx) * (point[1] - my) for point in points)
    # 2x2 协方差矩阵的主方向即直线方向；法向与它垂直。
    angle = 0.5 * math.atan2(2.0 * sxy, sxx - syy)
    direction = (math.cos(angle), math.sin(angle))
    a = -direction[1]
    b = direction[0]
    c = -(a * mx + b * my)
    return a, b, c


def _intersect(
    line1: tuple[float, float, float], line2: tuple[float, float, float]
) -> Point | None:
    a1, b1, c1 = line1
    a2, b2, c2 = line2
    det = a1 * b2 - a2 * b1
    if abs(det) < 1e-12:
        return None
    return ((b1 * c2 - b2 * c1) / det, (c1 * a2 - c2 * a1) / det)


def refine_quad(
    image: "GrayImage",
    quad: list[Point],
    *,
    black_level: float,
    white_level: float,
    step: float = 0.2,
    reach: float = 1.6,
    min_points: int = 6,
    max_corner_shift: float = 3.0,
) -> list[Point]:
    """用**灰度亚像素边缘**细化四边形角点：每条边沿外法向找黑白过渡点，再拟合直线求交。

    粗角点来自凸包化简，误差受像素栅格限制（约 1~2 px）。这里的做法：

    1. 每条边按 1 px 一段取样，沿外法向在 ``±reach`` 像素内逐 ``step`` 采样灰度（双线性）；
    2. 在与边缘同侧的过渡里取梯度最大的一处，用线性插值求出「灰度 = 黑白中点」的亚像素位置；
    3. 用这些亚像素点做总体最小二乘直线拟合，相邻两条直线求交得到角点。

    亚像素点来自真实的灰度过渡而不是二值化台阶，因此在 LB-002 的 fixtures 上把角点误差从
    约 1.5 px 压到 0.4 px（见 ``tests/test_vision_markers.py``）。

    某条边的过渡点太少（标记被画面裁切或被遮挡）或结果偏离粗角点太多时**放弃细化**，
    返回原四边形，避免在证据不足时硬凑。
    """
    if len(quad) != 4:
        return list(quad)
    level = (black_level + white_level) / 2.0
    cx = sum(point[0] for point in quad) / 4.0
    cy = sum(point[1] for point in quad) / 4.0
    lines: list[tuple[float, float, float]] = []
    for i in range(4):
        a = quad[i]
        b = quad[(i + 1) % 4]
        ex = b[0] - a[0]
        ey = b[1] - a[1]
        length = math.hypot(ex, ey)
        if length < 1e-9:
            return list(quad)
        dx, dy = ex / length, ey / length
        nx, ny = -dy, dx
        mid_x, mid_y = (a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0
        if (mid_x - cx) * nx + (mid_y - cy) * ny < 0.0:  # (nx, ny) 指向四边形外侧
            nx, ny = -nx, -ny
        crossings: list[Point] = []
        bins = max(1, int(length))
        for k in range(bins):
            t = (k + 0.5) / bins
            base_x = a[0] + ex * t
            base_y = a[1] + ey * t
            best_gradient = 0.0
            best_offset = 0.0
            previous: tuple[float, float] | None = None
            offset = -reach
            while offset <= reach + 1e-9:
                value = image.sample(base_x + nx * offset, base_y + ny * offset)
                if previous is not None:
                    prev_offset, prev_value = previous
                    if prev_value != value and (prev_value - level) * (value - level) <= 0.0:
                        crossing = prev_offset + (level - prev_value) * (offset - prev_offset) / (
                            value - prev_value
                        )
                        gradient = abs(value - prev_value)
                        if gradient > best_gradient:
                            best_gradient = gradient
                            best_offset = crossing
                previous = (offset, value)
                offset += step
            if best_gradient > 0.0:
                crossings.append((base_x + nx * best_offset, base_y + ny * best_offset))
        if len(crossings) < min_points:
            return list(quad)
        lines.append(_line_through(crossings))
    refined: list[Point] = []
    for i in range(4):
        corner = _intersect(lines[i - 1], lines[i])
        if corner is None:
            return list(quad)
        if math.hypot(corner[0] - quad[i][0], corner[1] - quad[i][1]) > max_corner_shift:
            return list(quad)
        refined.append(corner)
    return refined


def outside_frame(points: list[Point], width: int, height: int) -> list[bool]:
    """每个点是否落在画面外（与 LB-002 真值同一口径：``0 <= x < width`` 且 ``0 <= y < height``）。"""
    return [not (0.0 <= x < float(width) and 0.0 <= y < float(height)) for x, y in points]


def quad_iou(a: list[Point], b: list[Point]) -> float:
    """两个四边形的交并比估计：用「采样网格内的点同时落在两者内」的比例近似。

    只用于去重时判断「两个候选是不是同一个标记」，不做精确几何。
    """
    left = max(min(p[0] for p in a), min(p[0] for p in b))
    right = min(max(p[0] for p in a), max(p[0] for p in b))
    top = max(min(p[1] for p in a), min(p[1] for p in b))
    bottom = min(max(p[1] for p in a), max(p[1] for p in b))
    if right <= left or bottom <= top:
        return 0.0
    inside_both = 0
    inside_any = 0
    steps = 8
    for iy in range(steps):
        y = top + (iy + 0.5) * (bottom - top) / steps
        for ix in range(steps):
            x = left + (ix + 0.5) * (right - left) / steps
            in_a = point_in_quad((x, y), a)
            in_b = point_in_quad((x, y), b)
            if in_a and in_b:
                inside_both += 1
            if in_a or in_b:
                inside_any += 1
    if inside_any == 0:
        return 0.0
    return inside_both / inside_any
