"""ArUco 码字译码：从「疑似标记四边形」采样 6×6 模块并比对入库位表。

流程（对应 ``docs/DECISIONS.md`` D-013）：

1. 用 4 点直接线性变换（DLT）求「单位正方形 → 四边形」的单应矩阵；
2. 按模块中心采样灰度，得到 6×6 的模块均值；
3. 黑边（外圈 20 个模块）必须整圈偏暗，否则直接判为「不是标记」；
4. 内圈 4×4 位串与字典逐个码字比对，允许最多 ``max_bit_errors`` 个位错（低光照、轻微遮挡）；
5. 同时在 4 个 90° 旋转下比对，得到标记自身坐标系的朝向 ``rotations``。

``rotations`` 的含义：观察到的位串等于字典码字顺时针旋转 ``rotations`` 次的字符串。由此可把
检测到的四边形顶点映射回标记自身的左上/右上/右下/左下：``corners[(i + rotations) % 4]``。
这个映射由 ``tests/test_vision_decode.py`` 用「已知 yaw 的合成图 + LB-002 真值角点」验证。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from ..synth.dictionaries import ArucoDictionary
from ..synth.geometry import Matrix3, apply_homography, mat_inv, mat_mul
from .image import GrayImage, Point

#: 采样时用来估计「白模块」参考亮度的分位数（取最亮的几个模块的均值）。
WHITE_PERCENTILE_COUNT = 5


#: 单位正方形四顶点（左上、右上、右下、左下）。
UNIT_SQUARE: tuple[Point, Point, Point, Point] = (
    (0.0, 0.0),
    (1.0, 0.0),
    (1.0, 1.0),
    (0.0, 1.0),
)


def _normalizer(points: list[Point]) -> Matrix3:
    """Hartley 归一化矩阵：平移到质心、缩放到平均距离 ``√2``。

    4 点 DLT 对**小四边形**是病态的：不归一化时透视项会被放得很大，四边形内部还算吻合，
    但一外推（例如由标记外推卡片四角）就飞掉。归一化是标准做法，代价是三次矩阵乘法。
    """
    n = len(points)
    cx = sum(point[0] for point in points) / n
    cy = sum(point[1] for point in points) / n
    mean_distance = sum(math.hypot(point[0] - cx, point[1] - cy) for point in points) / n
    scale = math.sqrt(2.0) / mean_distance if mean_distance > 0.0 else 1.0
    return ((scale, 0.0, -scale * cx), (0.0, scale, -scale * cy), (0.0, 0.0, 1.0))


def homography(source: list[Point], target: list[Point]) -> Matrix3:
    """求把 4 个 ``source`` 顶点映射到对应 ``target`` 顶点的单应矩阵（归一化 4 点 DLT）。"""
    if len(source) != 4 or len(target) != 4:
        raise ValueError(f"需要 4 个顶点，实际 source={len(source)} target={len(target)}")
    source_norm = _normalizer(source)
    target_norm = _normalizer(target)
    normalised_source = [apply_homography(source_norm, u, v) for u, v in source]
    normalised_target = [apply_homography(target_norm, x, y) for x, y in target]
    rows: list[list[float]] = []
    rhs: list[float] = []
    for (u, v), (x, y) in zip(normalised_source, normalised_target):
        rows.append([u, v, 1.0, 0.0, 0.0, 0.0, -u * x, -v * x])
        rhs.append(x)
        rows.append([0.0, 0.0, 0.0, u, v, 1.0, -u * y, -v * y])
        rhs.append(y)
    h = _solve_linear(rows, rhs)
    normalised: Matrix3 = (
        (h[0], h[1], h[2]),
        (h[3], h[4], h[5]),
        (h[6], h[7], 1.0),
    )
    return mat_mul(mat_inv(target_norm), mat_mul(normalised, source_norm))


def homography_from_unit_square(quad: list[Point]) -> Matrix3:
    """求把单位正方形 ``(0,0)-(1,1)`` 映射到 ``quad`` 的单应矩阵。"""
    return homography(list(UNIT_SQUARE), list(quad))


def _solve_linear(matrix: list[list[float]], rhs: list[float]) -> list[float]:
    """高斯消元（部分主元），用于 8×8 的 DLT 方程组。"""
    n = len(matrix)
    a = [row[:] for row in matrix]
    b = rhs[:]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(a[r][col]))
        if abs(a[pivot][col]) < 1e-12:
            raise ValueError("单应矩阵退化：四边形面积过小或顶点重合")
        a[col], a[pivot] = a[pivot], a[col]
        b[col], b[pivot] = b[pivot], b[col]
        pivot_value = a[col][col]
        for r in range(col + 1, n):
            factor = a[r][col] / pivot_value
            if factor == 0.0:
                continue
            for c in range(col, n):
                a[r][c] -= factor * a[col][c]
            b[r] -= factor * b[col]
    solution = [0.0] * n
    for row in range(n - 1, -1, -1):
        total = b[row]
        for c in range(row + 1, n):
            total -= a[row][c] * solution[c]
        solution[row] = total / a[row][row]
    return solution


def sample_modules(image: GrayImage, quad: list[Point], modules: int) -> list[list[float]]:
    """按模块中心采样，返回 ``modules x modules`` 的灰度均值（行优先，y 向下）。"""
    homography = homography_from_unit_square(quad)
    values: list[list[float]] = []
    for row in range(modules):
        line: list[float] = []
        for col in range(modules):
            u = (col + 0.5) / modules
            v = (row + 0.5) / modules
            x, y = apply_homography(homography, u, v)
            line.append(image.sample(x, y))
        values.append(line)
    return values


def rotations_of(code: str, modules: int) -> tuple[str, str, str, str]:
    """码字的 4 个旋转（0°、顺时针 90°、180°、顺时针 270°）。"""
    grid = [list(code[r * modules : (r + 1) * modules]) for r in range(modules)]
    seen: list[str] = []
    for _ in range(4):
        seen.append("".join("".join(row) for row in grid))
        grid = [list(row) for row in zip(*grid[::-1])]  # 顺时针 90°
    return (seen[0], seen[1], seen[2], seen[3])


@dataclass(frozen=True)
class CodeMatch:
    """一次码字比对的结果。"""

    marker_id: int | None
    rotations: int
    bit_errors: int
    runner_up_id: int | None
    runner_up_errors: int
    tied_with: tuple[int, ...]

    @property
    def ambiguous(self) -> bool:
        return len(self.tied_with) > 1


@dataclass(frozen=True)
class DecodeResult:
    """一个四边形候选的完整译码结果。"""

    match: CodeMatch
    bits: str
    black_level: float
    white_level: float
    border_dark: bool
    border_max: float
    quadrant_contrast: float

    @property
    def contrast_term(self) -> float:
        """模块黑白对比度归一到 [0,1]（128 灰度差即满分）。"""
        return min(max((self.white_level - self.black_level) / 128.0, 0.0), 1.0)


def _threshold(values: list[float], border: list[float]) -> tuple[float, float, float]:
    """用黑边均值与内圈最亮模块均值的中点作为判定阈值。"""
    black = sum(border) / len(border)
    inner_sorted = sorted(values)
    brightest = inner_sorted[-WHITE_PERCENTILE_COUNT:]
    white = sum(brightest) / len(brightest)
    threshold = (black + white) / 2.0
    return black, white, threshold


def decode_marker(
    image: GrayImage,
    quad: list[Point],
    aruco: ArucoDictionary,
    *,
    max_bit_errors: int = 1,
) -> DecodeResult:
    """译码一个四边形候选。``match.marker_id`` 为 ``None`` 表示未能可靠识别。"""
    if max_bit_errors < 0:
        raise ValueError(f"max_bit_errors 不能为负：{max_bit_errors}")
    modules = aruco.marker_modules + 2 * aruco.border_bits
    if aruco.border_bits != 1:
        raise ValueError("本实现只支持 1 个模块宽的黑边（DICT_4X4_50 的约定）")
    values = sample_modules(image, quad, modules)

    border: list[float] = []
    inner: list[float] = []
    for row in range(modules):
        for col in range(modules):
            if row == 0 or col == 0 or row == modules - 1 or col == modules - 1:
                border.append(values[row][col])
            else:
                inner.append(values[row][col])

    black, white, threshold = _threshold(inner, border)
    bits = "".join("1" if value > threshold else "0" for value in inner)
    border_max = max(border)
    border_dark = border_max <= threshold

    best: tuple[int, int, int] | None = None  # (errors, marker_id, rotations)
    tied: list[int] = []
    runner_up: tuple[int, int] | None = None  # (errors, marker_id)
    for marker_id, code in enumerate(aruco.codes):
        for rotation, rotated in enumerate(rotations_of(code, aruco.marker_modules)):
            errors = sum(1 for a, b in zip(bits, rotated) if a != b)
            candidate = (errors, marker_id, rotation)
            if best is None or candidate[0] < best[0]:
                if best is not None and best[1] != marker_id:
                    runner_up = (best[0], best[1])
                best = candidate
                tied = [marker_id]
            elif candidate[0] == best[0]:
                if marker_id != best[1] and marker_id not in tied:
                    tied.append(marker_id)
                if runner_up is None or candidate[0] < runner_up[0]:
                    runner_up = (candidate[0], marker_id)
            elif runner_up is None or errors < runner_up[0]:
                runner_up = (errors, marker_id)

    assert best is not None  # 字典非空时必然有候选
    errors, marker_id, rotation = best
    if not border_dark or errors > max_bit_errors:
        match = CodeMatch(
            marker_id=None,
            rotations=rotation,
            bit_errors=errors,
            runner_up_id=marker_id,
            runner_up_errors=errors,
            tied_with=tuple(tied),
        )
    elif len(tied) > 1:
        match = CodeMatch(
            marker_id=None,
            rotations=rotation,
            bit_errors=errors,
            runner_up_id=tied[0] if tied[0] != marker_id else tied[1],
            runner_up_errors=errors,
            tied_with=tuple(tied),
        )
    else:
        match = CodeMatch(
            marker_id=marker_id,
            rotations=rotation,
            bit_errors=errors,
            runner_up_id=None if runner_up is None else runner_up[1],
            runner_up_errors=0 if runner_up is None else runner_up[0],
            tied_with=(marker_id,),
        )

    return DecodeResult(
        match=match,
        bits=bits,
        black_level=black,
        white_level=white,
        border_dark=border_dark,
        border_max=border_max,
        quadrant_contrast=min(max((white - black) / 128.0, 0.0), 1.0),
    )


def marker_corners_in_marker_frame(quad: list[Point], rotations: int) -> list[Point]:
    """把检测到的四边形顶点映射成标记自身坐标系的左上/右上/右下/左下。"""
    return [quad[(i + rotations) % 4] for i in range(4)]


def angle_deg_ccw(corners: list[Point]) -> float:
    """由标记自身的左上→右上边算面内旋转角（度，**逆时针为正**，遵循协议 §0）。

    图像坐标系 y 向下，所以「视觉上的逆时针」等于 ``-atan2(dy, dx)``。
    """
    (x0, y0), (x1, y1) = corners[0], corners[1]
    return -math.degrees(math.atan2(y1 - y0, x1 - x0))
