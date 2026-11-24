"""标定求解：单应 → Zhang 闭式初值 → 交替 Levenberg–Marquardt 精化（纯标准库）。

输入是一组「视图」：每个视图若干 ``(角点 id, u_px, v_px)`` 观测 + 目标上该 id 的毫米坐标。
输出是 ``LensModel``（内参 + plumb_bob 畸变）、每个视图的位姿、以及重投影误差统计。

算法分三步（每一步都可单独测）：

1. **单应**：每个视图用归一化 DLT 求「目标平面毫米 → 图像像素」的单应 ``H``
   （Hartley 归一化 + 正规方程 + Jacobi 特征分解取最小特征向量）。
2. **闭式初值（Zhang）**：由 ``H`` 的旋转约束 ``h1ᵀ·B·h2 = 0``、``h1ᵀ·B·h1 = h2ᵀ·B·h2``
   解出 ``B`` 的最小特征向量，再拆出 ``fx, fy, cx, cy``；每个视图的位姿由 ``K⁻¹·H`` 极分解
   得到（正交化 ``r1, r2``，``r3 = r1 × r2``）。这一步**不含畸变**，所以只是初值。
3. **精化**：交替做两个 LM 块——固定内参/畸变、逐视图解 6 维位姿；固定位姿、解 8 维
   内参与畸变。两块都用中心差分雅可比 + 对角缩放阻尼的正规方程。交替比一次性解
   8 + 6·N 维的全参数问题快得多（纯 Python 里差别是数量级），而在这个问题上收敛到了同样的解。

所有迭代都是定次数、定顺序的浮点运算，因此同样的输入必然给出同样的输出（可作 golden 快照）。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from ..synth.lens import LensModel

Point = tuple[float, float]

#: 剔除一轮外点的阈值：残差超过 ``max(倍率 × RMS, 绝对下限)`` 的观测不参与第二轮拟合。
#: 目的是挡住「编号错一格」「板上有脏点」这类**孤立**错误——它们会把整套内参一起带偏。
OUTLIER_FACTOR = 6.0
OUTLIER_FLOOR_PX = 2.0
#: 第二轮拟合时每个视图至少要留这么多角点，否则不动（宁可保留可疑点也不让视图失效）。
OUTLIER_MIN_POINTS = 6

#: 联合 LM 的收敛阈值（单轮代价的相对改进）。
REFINE_TOLERANCE = 1e-9
#: 联合 LM 的阻尼调度：起步值、每次成功后的缩小倍数、失败后的放大倍数、单轮最多试探几次。
#: 阻尼缩小的步子必须够大，否则「先被拒绝、再小幅接受」会让 λ 长时间停在高位，
#: 迭代变成爬行（实测 λ 从 1 起步时 400 轮才到位；按下面的调度 20 轮就到）。
START_DAMPING = 1e-3
DAMPING_DOWN = 0.1
DAMPING_UP = 10.0
MAX_ATTEMPTS = 20
#: 畸变系数的合理范围：超出即说明这一步迈进了非物理区域，按「试探失败」处理。
MAX_ABSOLUTE_K = 1.0
MAX_ABSOLUTE_P = 0.1
#: 联合 LM（内参 + 位姿）的最大迭代数。
JOINT_ITERATIONS = 40
#: 中心差分步长（位姿用弧度/毫米，内参用相对步长）。
POSE_STEP = 1e-6
INTRINSIC_STEP = 1e-4


class CalibrationError(ValueError):
    """标定无法进行（视图不够、退化、数值失败）。"""


# --- 基础线性代数 ---------------------------------------------------------------------


def solve_linear(matrix: list[list[float]], rhs: list[float]) -> list[float]:
    """列主元高斯消元解 ``A·x = b``（会就地改写传入的副本）。"""
    n = len(matrix)
    if n == 0 or len(rhs) != n or any(len(row) != n for row in matrix):
        raise CalibrationError(f"方程组形状不对：{n}×{n} vs rhs {len(rhs)}")
    a = [list(row) + [rhs[i]] for i, row in enumerate(matrix)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(a[r][col]))
        if abs(a[pivot][col]) < 1e-14:
            raise CalibrationError(f"方程组在列 {col} 上退化（主元 ≈ 0）")
        if pivot != col:
            a[col], a[pivot] = a[pivot], a[col]
        inv = 1.0 / a[col][col]
        piv_row = a[col]
        for r in range(col + 1, n):
            factor = a[r][col] * inv
            if factor == 0.0:
                continue
            row = a[r]
            for c in range(col, n + 1):
                row[c] -= factor * piv_row[c]
    x = [0.0] * n
    for r in range(n - 1, -1, -1):
        acc = a[r][n]
        row = a[r]
        for c in range(r + 1, n):
            acc -= row[c] * x[c]
        x[r] = acc / row[r]
    return x


def jacobi_eigen(matrix: list[list[float]], *, sweeps: int = 64) -> tuple[list[float], list[list[float]]]:
    """对称矩阵的 Jacobi 特征分解，返回 ``(特征值, 特征向量矩阵)``。

    特征向量按列给出：第 ``k`` 个特征向量是 ``vectors[i][k]``。定次数扫描，结果确定。
    """
    n = len(matrix)
    a = [list(row) for row in matrix]
    v = [[1.0 if i == j else 0.0 for j in range(n)] for i in range(n)]
    for _ in range(sweeps):
        off = 0.0
        for i in range(n):
            for j in range(i + 1, n):
                off += a[i][j] * a[i][j]
        if off <= 1e-30:
            break
        for p in range(n):
            for q in range(p + 1, n):
                apq = a[p][q]
                if abs(apq) <= 1e-18:
                    continue
                theta = (a[q][q] - a[p][p]) / (2.0 * apq)
                t = math.copysign(1.0, theta) / (abs(theta) + math.sqrt(theta * theta + 1.0))
                c = 1.0 / math.sqrt(t * t + 1.0)
                s = t * c
                for k in range(n):
                    akp = a[k][p]
                    akq = a[k][q]
                    a[k][p] = c * akp - s * akq
                    a[k][q] = s * akp + c * akq
                for k in range(n):
                    apk = a[p][k]
                    aqk = a[q][k]
                    a[p][k] = c * apk - s * aqk
                    a[q][k] = s * apk + c * aqk
                for k in range(n):
                    vkp = v[k][p]
                    vkq = v[k][q]
                    v[k][p] = c * vkp - s * vkq
                    v[k][q] = s * vkp + c * vkq
    values = [a[i][i] for i in range(n)]
    return values, v


def smallest_eigenvector(matrix: list[list[float]]) -> list[float]:
    """对称半正定矩阵最小特征值对应的单位特征向量。"""
    values, vectors = jacobi_eigen(matrix)
    index = min(range(len(values)), key=lambda i: values[i])
    vector = [vectors[row][index] for row in range(len(values))]
    norm = math.sqrt(sum(value * value for value in vector))
    if norm <= 0.0:
        raise CalibrationError("特征向量为零向量：输入矩阵退化")
    return [value / norm for value in vector]


# --- 单应 -----------------------------------------------------------------------------


def _normalizer(points: list[Point]) -> tuple[list[Point], list[list[float]]]:
    """Hartley 归一化：质心移到原点、平均距离缩到 √2，返回归一化点与 3×3 变换。"""
    n = len(points)
    mx = sum(point[0] for point in points) / n
    my = sum(point[1] for point in points) / n
    dist = sum(math.hypot(point[0] - mx, point[1] - my) for point in points) / n
    scale = 1.0 if dist <= 1e-12 else math.sqrt(2.0) / dist
    normalized = [((point[0] - mx) * scale, (point[1] - my) * scale) for point in points]
    matrix = [[scale, 0.0, -scale * mx], [0.0, scale, -scale * my], [0.0, 0.0, 1.0]]
    return normalized, matrix


def _mat_mul(a: list[list[float]], b: list[list[float]]) -> list[list[float]]:
    n, k, m = len(a), len(b), len(b[0])
    return [[sum(a[i][t] * b[t][j] for t in range(k)) for j in range(m)] for i in range(n)]


def _mat_vec(a: list[list[float]], x: list[float]) -> list[float]:
    return [sum(row[i] * x[i] for i in range(len(x))) for row in a]


def homography_dlt(source: list[Point], target: list[Point]) -> list[float]:
    """归一化 DLT：返回把 ``source`` 映到 ``target`` 的 3×3 单应（行优先 9 个浮点）。"""
    if len(source) != len(target) or len(source) < 4:
        raise CalibrationError(f"单应至少需要 4 组对应点：{len(source)} vs {len(target)}")
    src_norm, src_t = _normalizer(source)
    dst_norm, dst_t = _normalizer(target)
    rows: list[list[float]] = []
    for (x, y), (u, v) in zip(src_norm, dst_norm):
        rows.append([-x, -y, -1.0, 0.0, 0.0, 0.0, u * x, u * y, u])
        rows.append([0.0, 0.0, 0.0, -x, -y, -1.0, v * x, v * y, v])
    normal = [[sum(row[i] * row[j] for row in rows) for j in range(9)] for i in range(9)]
    h = smallest_eigenvector(normal)
    h_matrix = [[h[0], h[1], h[2]], [h[3], h[4], h[5]], [h[6], h[7], h[8]]]
    try:
        dst_inv = _inv3(dst_t)
    except CalibrationError:
        raise CalibrationError("目标点归一化矩阵不可逆") from None
    full = _mat_mul(dst_inv, _mat_mul(h_matrix, src_t))
    scale = full[2][2]
    if abs(scale) < 1e-12:
        # 退化的归一化：用整体范数归一，避免把整张单应缩放到 0。
        norm = math.sqrt(sum(value * value for row in full for value in row)) or 1.0
        scale = norm
    return [value / scale for row in full for value in row]


def _inv3(m: list[list[float]]) -> list[list[float]]:
    (a, b, c), (d, e, f), (g, h, i) = m
    det = a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g)
    if abs(det) < 1e-14:
        raise CalibrationError(f"3×3 矩阵不可逆（det={det!r}）")
    inv_det = 1.0 / det
    return [
        [(e * i - f * h) * inv_det, (c * h - b * i) * inv_det, (b * f - c * e) * inv_det],
        [(f * g - d * i) * inv_det, (a * i - c * g) * inv_det, (c * d - a * f) * inv_det],
        [(d * h - e * g) * inv_det, (b * g - a * h) * inv_det, (a * e - b * d) * inv_det],
    ]


def apply_homography(h: list[float], x: float, y: float) -> Point:
    w = h[6] * x + h[7] * y + h[8]
    if abs(w) < 1e-12:
        raise CalibrationError("单应退化：w ≈ 0")
    return (
        (h[0] * x + h[1] * y + h[2]) / w,
        (h[3] * x + h[4] * y + h[5]) / w,
    )


# --- Zhang 闭式初值 -------------------------------------------------------------------


def _v_row(h: list[float], i: int, j: int) -> list[float]:
    """Zhang 的 ``v_ij``（``i``/``j`` 是单应的列号 0/1/2）。"""
    return [
        h[i] * h[j],
        h[i] * h[3 + j] + h[3 + i] * h[j],
        h[3 + i] * h[3 + j],
        h[6 + i] * h[j] + h[i] * h[6 + j],
        h[6 + i] * h[3 + j] + h[3 + i] * h[6 + j],
        h[6 + i] * h[6 + j],
    ]


def zhang_intrinsics(homographies: list[list[float]]) -> tuple[float, float, float, float]:
    """由 ≥3 个单应解出 ``(fx, fy, cx, cy)``（假设零倾斜、方形像素尺度由 β/α 分开给）。"""
    if len(homographies) < 3:
        raise CalibrationError(f"Zhang 闭式解需要 ≥3 个视图：{len(homographies)}")
    rows: list[list[float]] = []
    for h in homographies:
        v12 = _v_row(h, 0, 1)
        v11 = _v_row(h, 0, 0)
        v22 = _v_row(h, 1, 1)
        rows.append(v12)
        rows.append([v11[k] - v22[k] for k in range(6)])
    normal = [[sum(row[i] * row[j] for row in rows) for j in range(6)] for i in range(6)]
    b = smallest_eigenvector(normal)
    b11, b12, b22, b13, b23, b33 = b
    denom = b11 * b22 - b12 * b12
    if abs(denom) < 1e-18 or abs(b11) < 1e-18:
        raise CalibrationError("Zhang 闭式解的 B 矩阵退化（视图几何太差）")
    v0 = (b12 * b13 - b11 * b23) / denom
    lam = b33 - (b13 * b13 + v0 * (b12 * b13 - b11 * b23)) / b11
    if lam <= 0.0:
        raise CalibrationError(f"Zhang 闭式解得到非正的尺度 λ={lam!r}（焦距无法确定）")
    alpha_sq = lam / b11
    if alpha_sq <= 0.0:
        raise CalibrationError(f"Zhang 闭式解得到非正的 α²={alpha_sq!r}")
    alpha = math.sqrt(alpha_sq)
    beta_sq = lam * b11 / denom
    if beta_sq <= 0.0:
        raise CalibrationError(f"Zhang 闭式解得到非正的 β²={beta_sq!r}")
    beta = math.sqrt(beta_sq)
    gamma = -b12 * alpha * alpha * beta / lam
    u0 = gamma * v0 / beta - b13 * alpha * alpha / lam
    if not all(math.isfinite(value) for value in (alpha, beta, u0, v0)):
        raise CalibrationError("Zhang 闭式解得到非有限值")
    return alpha, beta, u0, v0


def focal_from_homographies(homographies: list[list[float]], cx: float, cy: float) -> float:
    """以给定主点、假设 fx=fy=f 时由单应的正交约束直接估 f（Zhang 闭式解失败时的兜底）。

    把单应平移到以主点为原点的坐标后，``K = diag(f, f, 1)``，于是 ``M = K⁻¹·H'`` 的两列
    ``c1、c2`` 必须正交且等长：

    - ``c1·c2 = 0``   → ``f² = -(h'11·h'12 + h'21·h'22) / (h'31·h'32)``
    - ``|c1| = |c2|`` → ``f² = ((h'11² + h'21²) - (h'12² + h'22²)) / (h'32² - h'31²)``

    两个式子都给正数时各出一个估计，取全部估计的中位数。
    """
    estimates: list[float] = []
    for h in homographies:
        h31, h32, h33 = h[6], h[7], h[8]
        p11 = h[0] - cx * h31
        p12 = h[1] - cx * h32
        p21 = h[3] - cy * h31
        p22 = h[4] - cy * h32
        den_orth = h31 * h32
        if abs(den_orth) > 1e-18:
            num_orth = -(p11 * p12 + p21 * p22)
            if num_orth > 0.0:
                estimates.append(math.sqrt(num_orth / den_orth))
        den_norm = h32 * h32 - h31 * h31
        if abs(den_norm) > 1e-18:
            num_norm = (p11 * p11 + p21 * p21) - (p12 * p12 + p22 * p22)
            if num_norm / den_norm > 0.0:
                estimates.append(math.sqrt(num_norm / den_norm))
    if not estimates:
        raise CalibrationError("无法从单应估计焦距")
    estimates.sort()
    return estimates[len(estimates) // 2]


def rodrigues(rvec: list[float]) -> list[list[float]]:
    """旋转向量 → 旋转矩阵。"""
    rx, ry, rz = rvec
    theta = math.sqrt(rx * rx + ry * ry + rz * rz)
    if theta < 1e-12:
        return [[1.0, -rz, ry], [rz, 1.0, -rx], [-ry, rx, 1.0]]
    kx, ky, kz = rx / theta, ry / theta, rz / theta
    c, s = math.cos(theta), math.sin(theta)
    v = 1.0 - c
    return [
        [c + kx * kx * v, kx * ky * v - kz * s, kx * kz * v + ky * s],
        [ky * kx * v + kz * s, c + ky * ky * v, ky * kz * v - kx * s],
        [kz * kx * v - ky * s, kz * ky * v + kx * s, c + kz * kz * v],
    ]


def rotation_to_rvec(r: list[list[float]]) -> list[float]:
    """旋转矩阵 → 旋转向量。"""
    trace = r[0][0] + r[1][1] + r[2][2]
    cos_theta = max(-1.0, min(1.0, (trace - 1.0) / 2.0))
    theta = math.acos(cos_theta)
    if theta < 1e-9:
        return [0.0, 0.0, 0.0]
    if abs(math.pi - theta) < 1e-6:
        # θ≈π：由对称部分取轴（数值上少见的退化分支，只保证不崩）
        axis = [
            math.sqrt(max(0.0, (r[0][0] + 1.0) / 2.0)),
            math.sqrt(max(0.0, (r[1][1] + 1.0) / 2.0)),
            math.sqrt(max(0.0, (r[2][2] + 1.0) / 2.0)),
        ]
        return [axis[0] * theta, axis[1] * theta, axis[2] * theta]
    factor = theta / (2.0 * math.sin(theta))
    return [
        (r[2][1] - r[1][2]) * factor,
        (r[0][2] - r[2][0]) * factor,
        (r[1][0] - r[0][1]) * factor,
    ]


def extrinsics_from_homography(h: list[float], lens: LensModel) -> tuple[list[float], list[float]]:
    """由单应分解出 ``(rvec, tvec)``（``K⁻¹·H`` 的列即 λ·r1、λ·r2、λ·t）。"""
    k_inv = [[1.0 / lens.fx, 0.0, -lens.cx / lens.fx], [0.0, 1.0 / lens.fy, -lens.cy / lens.fy], [0.0, 0.0, 1.0]]
    m = _mat_mul(k_inv, [[h[0], h[1], h[2]], [h[3], h[4], h[5]], [h[6], h[7], h[8]]])
    col1 = [m[0][0], m[1][0], m[2][0]]
    col2 = [m[0][1], m[1][1], m[2][1]]
    col3 = [m[0][2], m[1][2], m[2][2]]
    norm1 = math.sqrt(sum(value * value for value in col1))
    norm2 = math.sqrt(sum(value * value for value in col2))
    if norm1 < 1e-12 or norm2 < 1e-12:
        raise CalibrationError("单应分解退化：λ ≈ 0")
    scale = 2.0 / (norm1 + norm2)
    if m[2][2] < 0.0:  # 目标必须在相机前方
        scale = -scale
    r1 = [value * scale for value in col1]
    r2 = [value * scale for value in col2]
    t = [value * scale for value in col3]
    # Gram-Schmidt 正交化
    n1 = math.sqrt(sum(value * value for value in r1))
    r1 = [value / n1 for value in r1]
    dot = sum(r1[i] * r2[i] for i in range(3))
    r2 = [r2[i] - dot * r1[i] for i in range(3)]
    n2 = math.sqrt(sum(value * value for value in r2))
    r2 = [value / n2 for value in r2]
    r3 = [
        r1[1] * r2[2] - r1[2] * r2[1],
        r1[2] * r2[0] - r1[0] * r2[2],
        r1[0] * r2[1] - r1[1] * r2[0],
    ]
    rotation = [[r1[0], r2[0], r3[0]], [r1[1], r2[1], r3[1]], [r1[2], r2[2], r3[2]]]
    return rotation_to_rvec(rotation), t


# --- 投影与残差 -----------------------------------------------------------------------


def project_point(
    lens: LensModel, rvec: list[float], tvec: list[float], x_mm: float, y_mm: float
) -> Point:
    """把目标平面上的 ``(X, Y, 0)`` 投影到（带畸变的）图像像素。"""
    r = rodrigues(rvec)
    cam_x = r[0][0] * x_mm + r[0][1] * y_mm + tvec[0]
    cam_y = r[1][0] * x_mm + r[1][1] * y_mm + tvec[1]
    cam_z = r[2][0] * x_mm + r[2][1] * y_mm + tvec[2]
    if cam_z <= 1e-9:
        raise CalibrationError(f"目标点跑到相机背部：z={cam_z!r}")
    xd, yd = lens.distort_normalized(cam_x / cam_z, cam_y / cam_z)
    return lens.fx * xd + lens.cx, lens.fy * yd + lens.cy


@dataclass(frozen=True)
class Observation:
    """一个视图的角点观测：``(角点 id, u_px, v_px)``。"""

    width: int
    height: int
    points: tuple[tuple[int, float, float], ...]

    @property
    def corner_ids(self) -> tuple[int, ...]:
        return tuple(corner_id for corner_id, _u, _v in self.points)


@dataclass(frozen=True)
class ViewPose:
    """一个视图求出的位姿（目标平面 → 相机）。"""

    rvec: tuple[float, float, float]
    tvec: tuple[float, float, float]

    @property
    def z_mm(self) -> float:
        return self.tvec[2]

    def to_dict(self) -> dict[str, object]:
        return {
            "rvec": [round(value, 6) for value in self.rvec],
            "tvec": [round(value, 6) for value in self.tvec],
        }


@dataclass(frozen=True)
class CalibrationResult:
    """一次标定的完整结果。"""

    lens: LensModel
    object_points_mm: dict[int, Point]
    observations: tuple[Observation, ...]
    poses: tuple[ViewPose, ...]
    residuals_px: tuple[tuple[float, float, float], ...]
    rounds: int
    dropped_points: int = 0

    @property
    def point_count(self) -> int:
        return sum(len(obs.points) for obs in self.observations)

    @property
    def view_count(self) -> int:
        return len(self.observations)

    @property
    def reprojection_rms_px(self) -> float:
        if not self.residuals_px:
            return 0.0
        return math.sqrt(
            sum(dx * dx + dy * dy for _cid, dx, dy in self.residuals_px) / len(self.residuals_px)
        )

    @property
    def reprojection_max_px(self) -> float:
        if not self.residuals_px:
            return 0.0
        return max(math.hypot(dx, dy) for _cid, dx, dy in self.residuals_px)

    def per_view_rms_px(self) -> list[float]:
        values: list[float] = []
        for index, obs in enumerate(self.observations):
            pose = self.poses[index]
            squared = 0.0
            for corner_id, u, v in obs.points:
                x_mm, y_mm = self.object_points_mm[corner_id]
                pu, pv = project_point(self.lens, list(pose.rvec), list(pose.tvec), x_mm, y_mm)
                squared += (pu - u) ** 2 + (pv - v) ** 2
            values.append(math.sqrt(squared / max(1, len(obs.points))))
        return values


# --- 精化 -----------------------------------------------------------------------------


def _residuals(
    lens: LensModel,
    poses: list[ViewPose],
    observations: list[Observation],
    object_points: dict[int, Point],
    *,
    view_index: int | None = None,
) -> list[float]:
    indices = range(len(observations)) if view_index is None else (view_index,)
    out: list[float] = []
    for index in indices:
        pose = poses[index]
        rvec = list(pose.rvec)
        tvec = list(pose.tvec)
        for corner_id, u, v in observations[index].points:
            x_mm, y_mm = object_points[corner_id]
            pu, pv = project_point(lens, rvec, tvec, x_mm, y_mm)
            out.append(pu - u)
            out.append(pv - v)
    return out


def _sum_squares(values: list[float]) -> float:
    return sum(value * value for value in values)


def _view_residuals(
    lens: LensModel,
    pose: ViewPose,
    observation: Observation,
    object_points: dict[int, Point],
) -> list[float]:
    out: list[float] = []
    rvec = list(pose.rvec)
    tvec = list(pose.tvec)
    for corner_id, u, v in observation.points:
        x_mm, y_mm = object_points[corner_id]
        pu, pv = project_point(lens, rvec, tvec, x_mm, y_mm)
        out.append(pu - u)
        out.append(pv - v)
    return out


def _lens_from_params(params: list[float]) -> LensModel:
    return LensModel(
        fx=params[0], fy=params[1], cx=params[2], cy=params[3],
        k1=params[4], k2=params[5], p1=params[6], p2=params[7],
    )


def _damped_solve(matrix: list[list[float]], rhs: list[float], damping: float) -> list[float]:
    """在 ``matrix`` 上按比例阻尼后解方程（阻尼项保证在病态方向上不炸）。"""
    n = len(matrix)
    damped = [row[:] for row in matrix]
    for i in range(n):
        damped[i][i] += damping * max(abs(matrix[i][i]), 1e-9)
    return solve_linear(damped, rhs)


def joint_refine(
    lens: LensModel,
    poses: list[ViewPose],
    observations: list[Observation],
    object_points: dict[int, Point],
    *,
    iterations: int = JOINT_ITERATIONS,
    trace: list[tuple[int, float, float]] | None = None,
) -> tuple[LensModel, list[ViewPose], int, float]:
    """联合 Levenberg–Marquardt：内参 + 畸变 + 每视图位姿一起解（Schur 补消去位姿块）。

    为什么不交替解两个块：交替坐标下降在「畸变 ↔ 位姿 ↔ 主点」这种强耦合问题上会停在
    重投影只剩 0.1~0.3 px 的错误点上（实测：连**无噪声**的理想数据都被拉偏 fx 7%、cx 15 px）。
    联合解把耦合项一起消掉，同一份数据能回到真值附近。

    规模上这里只需要 8×8 的 Schur 补：位姿块按视图独立（6×6），所以每轮代价是
    `O(残差数 × (8² + 8·6 + 6²))`，纯 Python 也就几十毫秒。
    """
    views = len(observations)
    n_int = 8
    n_pose = 6
    pose_columns = [list(pose.rvec) + list(pose.tvec) for pose in poses]

    def view_residual_vector(index: int, lens_value: LensModel, pose_params: list[float]) -> list[float]:
        pose = ViewPose(
            (pose_params[0], pose_params[1], pose_params[2]),
            (pose_params[3], pose_params[4], pose_params[5]),
        )
        return _view_residuals(lens_value, pose, observations[index], object_points)

    def total_cost(lens_value: LensModel, pose_params_all: list[list[float]]) -> float:
        acc = 0.0
        for index in range(views):
            values = view_residual_vector(index, lens_value, pose_params_all[index])
            acc += _sum_squares(values)
        return acc

    intrinsic_params = [
        lens.fx, lens.fy, lens.cx, lens.cy, lens.k1, lens.k2, lens.p1, lens.p2
    ]
    intrinsic_steps = [
        max(abs(lens.fx) * INTRINSIC_STEP, 1e-6),
        max(abs(lens.fy) * INTRINSIC_STEP, 1e-6),
        max(abs(lens.cx) * INTRINSIC_STEP, 1e-6),
        max(abs(lens.cy) * INTRINSIC_STEP, 1e-6),
        INTRINSIC_STEP,
        INTRINSIC_STEP,
        INTRINSIC_STEP,
        INTRINSIC_STEP,
    ]
    pose_steps = [POSE_STEP] * 3 + [POSE_STEP * 1e3] * 3  # rvec 用弧度步长，tvec 用毫米步长

    total_residuals = sum(len(observation.points) for observation in observations)
    cost = total_cost(lens, pose_columns)
    damping = START_DAMPING
    used = 0
    for iteration in range(iterations):
        used = iteration + 1
        current_lens = _lens_from_params(intrinsic_params)
        residual_vectors = [
            view_residual_vector(index, current_lens, pose_columns[index]) for index in range(views)
        ]
        lens_jac: list[list[list[float]]] = []
        for index in range(views):
            columns: list[list[float]] = []
            for p in range(n_int):
                step = intrinsic_steps[p]
                plus = intrinsic_params[:]
                plus[p] += step
                minus = intrinsic_params[:]
                minus[p] -= step
                r_plus = view_residual_vector(index, _lens_from_params(plus), pose_columns[index])
                r_minus = view_residual_vector(index, _lens_from_params(minus), pose_columns[index])
                columns.append(
                    [(r_plus[k] - r_minus[k]) / (2.0 * step) for k in range(len(r_plus))]
                )
            lens_jac.append(columns)
        pose_jac: list[list[list[float]]] = []
        for index in range(views):
            columns = []
            for p in range(n_pose):
                step = pose_steps[p]
                plus = pose_columns[index][:]
                plus[p] += step
                minus = pose_columns[index][:]
                minus[p] -= step
                r_plus = view_residual_vector(index, current_lens, plus)
                r_minus = view_residual_vector(index, current_lens, minus)
                columns.append(
                    [(r_plus[k] - r_minus[k]) / (2.0 * step) for k in range(len(r_plus))]
                )
            pose_jac.append(columns)

        # 正规方程的分块：U（内参 8×8）、V_i（位姿 6×6）、W_i（8×6）、g_i、g_p
        u_block = [[0.0] * n_int for _ in range(n_int)]
        g_int = [0.0] * n_int
        v_blocks: list[list[list[float]]] = []
        w_blocks: list[list[list[float]]] = []
        g_pose: list[list[float]] = []
        for index in range(views):
            residuals = residual_vectors[index]
            jl = lens_jac[index]
            jp = pose_jac[index]
            v_block = [[0.0] * n_pose for _ in range(n_pose)]
            w_block = [[0.0] * n_pose for _ in range(n_int)]
            g_p = [0.0] * n_pose
            for a in range(n_int):
                col_a = jl[a]
                g_int[a] += sum(col_a[k] * residuals[k] for k in range(len(residuals)))
                for b in range(a, n_int):
                    col_b = jl[b]
                    value = sum(col_a[k] * col_b[k] for k in range(len(residuals)))
                    u_block[a][b] += value
                    if b != a:
                        u_block[b][a] += value
                for b in range(n_pose):
                    w_block[a][b] += sum(col_a[k] * jp[b][k] for k in range(len(residuals)))
            for a in range(n_pose):
                col_a = jp[a]
                g_p[a] += sum(col_a[k] * residuals[k] for k in range(len(residuals)))
                for b in range(a, n_pose):
                    col_b = jp[b]
                    value = sum(col_a[k] * col_b[k] for k in range(len(residuals)))
                    v_block[a][b] += value
                    if b != a:
                        v_block[b][a] += value
            v_blocks.append(v_block)
            w_blocks.append(w_block)
            g_pose.append(g_p)

        improved = False
        for _attempt in range(MAX_ATTEMPTS):
            schur = [row[:] for row in u_block]
            rhs = [-value for value in g_int]
            v_inverse_columns: list[list[list[float]]] = []
            for index in range(views):
                v_block = v_blocks[index]
                w_block = w_blocks[index]
                g_p = g_pose[index]
                try:
                    solved_g = _damped_solve(v_block, g_p, damping)
                    # 逐列解 V⁻¹·Wᵀ，得到 Z（6×8）
                    z_columns: list[list[float]] = []
                    for a in range(n_int):
                        column = [w_block[a][b] for b in range(n_pose)]
                        z_columns.append(_damped_solve(v_block, column, damping))
                except CalibrationError:
                    v_inverse_columns = []
                    break
                v_inverse_columns.append(z_columns)
                # Schur 补：(U - Σ W·V⁻¹·Wᵀ)·δ_i = -g_i + Σ W·V⁻¹·g_p
                for a in range(n_int):
                    rhs[a] += sum(w_block[a][b] * solved_g[b] for b in range(n_pose))
                    for b in range(n_int):
                        schur[a][b] -= sum(w_block[a][t] * z_columns[b][t] for t in range(n_pose))
            if not v_inverse_columns:
                damping *= DAMPING_UP
                continue
            try:
                delta_int = _damped_solve(schur, rhs, damping)
            except CalibrationError:
                damping *= DAMPING_UP
                continue
            candidate_int = [intrinsic_params[i] + delta_int[i] for i in range(n_int)]
            if candidate_int[0] <= 1.0 or candidate_int[1] <= 1.0:
                damping *= DAMPING_UP
                continue
            if (
                abs(candidate_int[4]) > MAX_ABSOLUTE_K
                or abs(candidate_int[5]) > MAX_ABSOLUTE_K
                or abs(candidate_int[6]) > MAX_ABSOLUTE_P
                or abs(candidate_int[7]) > MAX_ABSOLUTE_P
            ):
                damping *= DAMPING_UP
                continue
            candidate_lens = _lens_from_params(candidate_int)
            candidate_poses: list[list[float]] = []
            failed = False
            for index in range(views):
                v_block = v_blocks[index]
                w_block = w_blocks[index]
                g_p = g_pose[index]
                adjusted = [
                    g_p[b] + sum(w_block[a][b] * delta_int[a] for a in range(n_int))
                    for b in range(n_pose)
                ]
                try:
                    delta_pose = _damped_solve(v_block, [-value for value in adjusted], damping)
                except CalibrationError:
                    failed = True
                    break
                candidate_poses.append(
                    [pose_columns[index][i] + delta_pose[i] for i in range(n_pose)]
                )
            if failed:
                damping *= DAMPING_UP
                continue
            try:
                candidate_cost = total_cost(candidate_lens, candidate_poses)
            except CalibrationError:
                damping *= DAMPING_UP
                continue
            if candidate_cost < cost:
                improvement = (cost - candidate_cost) / max(cost, 1e-30)
                cost = candidate_cost
                intrinsic_params = candidate_int
                pose_columns = candidate_poses
                damping = max(damping * DAMPING_DOWN, 1e-12)
                improved = improvement > REFINE_TOLERANCE
                break
            damping *= DAMPING_UP
        if trace is not None:
            trace.append((iteration + 1, math.sqrt(cost / max(1, total_residuals)), damping))
        if not improved:
            break

    lens = _lens_from_params(intrinsic_params)
    poses = [
        ViewPose(
            (pose_columns[i][0], pose_columns[i][1], pose_columns[i][2]),
            (pose_columns[i][3], pose_columns[i][4], pose_columns[i][5]),
        )
        for i in range(views)
    ]
    return lens, poses, used, cost


def _initial_poses(
    observations: list[Observation], object_points: dict[int, Point], lens: LensModel
) -> list[ViewPose]:
    """用当前内参把每个视图解成位姿（单应分解；畸变假设为 0）。"""
    poses: list[ViewPose] = []
    for observation in observations:
        source = [object_points[corner_id] for corner_id, _u, _v in observation.points]
        target = [(u, v) for _cid, u, v in observation.points]
        homography = homography_dlt(source, target)
        rvec, tvec = extrinsics_from_homography(homography, lens)
        poses.append(ViewPose(tuple(rvec), tuple(tvec)))  # type: ignore[arg-type]
    return poses


def _drop_outliers(
    lens: LensModel,
    poses: list[ViewPose],
    observations: list[Observation],
    object_points: dict[int, Point],
) -> tuple[list[Observation], int]:
    """去掉残差明显偏大的观测点；返回 ``(保留下来的视图, 去掉的点数)``。"""
    residuals = _residuals(lens, poses, observations, object_points)
    if not residuals:
        return observations, 0
    rms = math.sqrt(_sum_squares(residuals) / len(residuals))
    limit = max(OUTLIER_FACTOR * rms, OUTLIER_FLOOR_PX)
    kept: list[Observation] = []
    dropped = 0
    for index, observation in enumerate(observations):
        points: list[tuple[int, float, float]] = []
        pose = poses[index]
        for corner_id, u, v in observation.points:
            x_mm, y_mm = object_points[corner_id]
            pu, pv = project_point(lens, list(pose.rvec), list(pose.tvec), x_mm, y_mm)
            if math.hypot(pu - u, pv - v) > limit:
                dropped += 1
                continue
            points.append((corner_id, u, v))
        if len(points) < OUTLIER_MIN_POINTS:
            # 这个视图剔完就废了：整条保留，宁可带一个可疑点。
            kept.append(observation)
            dropped -= len(observation.points) - len(points)
            continue
        kept.append(Observation(width=observation.width, height=observation.height, points=tuple(points)))
    if dropped <= 0:
        return observations, 0
    return kept, dropped


def calibrate(
    observations: list[Observation],
    object_points: dict[int, Point],
    *,
    initial: LensModel | None = None,
    iterations: int = JOINT_ITERATIONS,
) -> CalibrationResult:
    """对一组视图求内参 + 畸变 + 每视图位姿。

    ``object_points`` 是 ``角点 id → (X_mm, Y_mm)``（目标平面，``Z = 0``）。
    ``initial`` 只给检索用（例如已知近似内参时给初值）；不给就走完整流程：
    单应 → Zhang 闭式初值 → 联合 LM 精化。
    """
    if len(observations) < 3:
        raise CalibrationError(f"标定至少需要 3 个视图：{len(observations)}")
    for observation in observations:
        if len(observation.points) < 6:
            raise CalibrationError(
                f"视图 {observation.width}×{observation.height} 只有 {len(observation.points)} 个角点："
                "少于 6 个无法稳定解单应"
            )
        for corner_id, _u, _v in observation.points:
            if corner_id not in object_points:
                raise CalibrationError(f"观测里的角点 id {corner_id} 不在目标定义里")

    homographies = [
        homography_dlt(
            [object_points[corner_id] for corner_id, _u, _v in observation.points],
            [(u, v) for _cid, u, v in observation.points],
        )
        for observation in observations
    ]

    if initial is not None:
        lens = initial
    else:
        try:
            fx, fy, cx, cy = zhang_intrinsics(homographies)
        except CalibrationError:
            width = observations[0].width
            height = observations[0].height
            focal = focal_from_homographies(homographies, width / 2.0, height / 2.0)
            if not math.isfinite(focal) or focal <= 0.0:
                raise CalibrationError("初值估计失败：焦距不是正数") from None
            fx = fy = focal
            cx, cy = width / 2.0 - 0.5, height / 2.0 - 0.5
        lens = LensModel(fx=fx, fy=fy, cx=cx, cy=cy)

    poses = _initial_poses(observations, object_points, lens)
    lens, poses, rounds, _cost = joint_refine(
        lens, poses, observations, object_points, iterations=iterations
    )

    # 一轮外点剔除：把残差明显偏大的观测去掉再拟合一次（只做一轮，避免把系统性误差裁掉）。
    kept, dropped = _drop_outliers(lens, poses, observations, object_points)
    if dropped:
        lens, poses, second_rounds, _cost = joint_refine(
            lens, poses, kept, object_points, iterations=iterations
        )
        rounds += second_rounds
        observations = kept

    residuals: list[tuple[float, float, float]] = []
    for index, observation in enumerate(observations):
        pose = poses[index]
        for corner_id, u, v in observation.points:
            x_mm, y_mm = object_points[corner_id]
            pu, pv = project_point(lens, list(pose.rvec), list(pose.tvec), x_mm, y_mm)
            residuals.append((float(corner_id), pu - u, pv - v))

    return CalibrationResult(
        lens=lens,
        object_points_mm=dict(object_points),
        observations=tuple(observations),
        poses=tuple(poses),
        residuals_px=tuple(residuals),
        rounds=rounds,
        dropped_points=dropped,
    )
