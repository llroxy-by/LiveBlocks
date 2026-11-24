"""端到端回归：跑全部场景 → 校验记录形状 → 比对黄金基准 → 自检破坏抓取 → 判时间上界。

这个模块就是 Issue #22 四条可观察验收的执行体：

1. **全链路命令退出码 0**：每个基准场景都真跑六阶段（生成 → 识别 → 排列 → 解析 → IR → 模拟），
   记录落盘到 ``--out``；
2. **运行记录包含全部层的 kind 且有 RunHeader**：由
   :func:`liveblocks.e2e.protocol.validate_record` 逐行校验（首行、层序、必填字段、事件 ``seq``、
   帧引用）；
3. **人为破坏一个 fixture 时回归明确失败并指出层级**：回归**自己**做一次破坏注入自检
   （默认把 ``normal`` 场景生成出来的图像抹平），要求比对**必须**失败、且失败能定位到阶段——
   抓不到就报 ``LB-E012``（回归是假通过）。命令行另有 ``--corrupt <层>`` 供人手工复现；
4. **回归在固定时间内完成**：``time_bound_ms``（默认 :data:`~liveblocks.e2e.protocol.TIME_BOUND_MS`）
   与单条记录的 ``record_bound_ms``，超了报 ``LB-E011``。

失败摘要（:func:`failure_summary`）按「阶段顺序取最早一条」给出根因层：破坏注入在最前面那层、
下游层的差异都是它的后果，所以只点名最上游的一层最有信息量。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

from .baseline import (
    BASELINE_NAME,
    BaselineError,
    baseline_document,
    compare_run,
    load_baseline,
    scene_entry,
    scene_names,
    write_baseline,
)
from ..synth import SynthContext
from .pipeline import ChainRun, run_chain
from .protocol import (
    CODE_CORRUPT_NOT_CAUGHT,
    CODE_TIME_BOUND,
    DEFAULT_SEED,
    RECORD_BOUND_MS,
    STAGE_LABELS,
    STAGES,
    TIME_BOUND_MS,
    E2eIssue,
    issue,
    validate_record,
)

#: 基准目录（``fixtures/e2e/baseline.json``）。
DEFAULT_DIR = Path("fixtures/e2e")

#: 运行记录与生成图像的落盘目录（不入库：``timings_ms`` 是实测墙钟，每跑一次都不一样）。
DEFAULT_OUT = Path("runs/e2e")

#: 破坏注入自检的默认目标。
DEFAULT_CORRUPT_PROBE = "scene-image"

#: LB-002 入库的合成场景目录：生成层应当能把它逐字节复现出来。
FIXTURE_SCENE_DIR = Path(__file__).resolve().parents[3] / "fixtures" / "scenes"


def _ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000.0, 3)


def fixture_matches(run: ChainRun, *, scene_dir: Path | None = None) -> bool | None:
    """生成层的图像是否与 LB-002 入库 fixture 逐字节相同（没有入库图时返回 ``None``）。

    只作为证据显示在验收对照里：整条链路的起点不是拍脑袋的数字，而是**复现**出来的图。
    """
    directory = FIXTURE_SCENE_DIR if scene_dir is None else Path(scene_dir)
    path = directory / f"{run.scene}.png"
    if not path.is_file():
        return None
    from .record import sha256_bytes

    return sha256_bytes(path.read_bytes()) == run.header["generation"]["image_sha256"]


def root_layer(issues: Iterable[E2eIssue]) -> str:
    """按阶段顺序取最早的一条问题所在层（没有可定位的层则返回空串）。"""
    ranked = [(STAGES.index(item.layer), item.layer) for item in issues if item.layer in STAGES]
    if not ranked:
        return ""
    return min(ranked)[1]


@dataclass
class SceneResult:
    """一个场景的回归结果。"""

    scene: str
    run: ChainRun | None
    record_path: Path | None
    issues: list[E2eIssue] = field(default_factory=list)
    wall_ms: float = 0.0

    @property
    def ok(self) -> bool:
        return not self.issues and self.run is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "scene": self.scene,
            "ok": self.ok,
            "record": str(self.record_path) if self.record_path else "",
            "lines": len(self.run.documents) if self.run is not None else 0,
            "kinds": list(self.run.kinds) if self.run is not None else [],
            "counts": self.run.counts() if self.run is not None else {},
            "timings_ms": self.run.timings if self.run is not None else {},
            "wall_ms": self.wall_ms,
            "issues": [item.to_dict() for item in self.issues],
        }


@dataclass
class CorruptionCheck:
    """破坏注入自检：证明「回归会失败，且失败点名到层」。"""

    target: str
    scene: str
    detected: bool
    detected_layer: str
    issues: list[E2eIssue] = field(default_factory=list)
    record_path: Path | None = None
    wall_ms: float = 0.0

    def describe(self) -> str:
        if self.detected:
            layer = STAGE_LABELS.get(self.detected_layer, self.detected_layer or "（未定位到层）")
            return f"破坏注入 {self.target}（场景 {self.scene}）→ 被抓到，最早失败层 {layer}"
        return f"破坏注入 {self.target}（场景 {self.scene}）→ 没有被抓到（回归是假通过）"

    def to_dict(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "scene": self.scene,
            "detected": self.detected,
            "detected_layer": self.detected_layer,
            "record": str(self.record_path) if self.record_path else "",
            "wall_ms": self.wall_ms,
            "issues": [item.to_dict() for item in self.issues],
        }


@dataclass
class RegressionReport:
    """一次完整回归的结果与验收对照。"""

    directory: Path
    out_dir: Path
    seed: int
    corrupt: str
    baseline: dict[str, Any]
    results: list[SceneResult] = field(default_factory=list)
    corruption_check: CorruptionCheck | None = None
    issues: list[E2eIssue] = field(default_factory=list)
    wall_ms: float = 0.0
    time_bound_ms: float = TIME_BOUND_MS
    record_bound_ms: float = RECORD_BOUND_MS
    baseline_updated: bool = False
    baseline_path: Path | None = None

    # --- 汇总 -------------------------------------------------------------

    def failures(self) -> list[E2eIssue]:
        """全部失败项（逐场景的问题 + 报告级问题），按场景与阶段排序。"""
        collected: list[E2eIssue] = []
        for result in self.results:
            collected.extend(result.issues)
        collected.extend(self.issues)
        if self.corruption_check is not None and not self.corruption_check.detected:
            collected.append(
                issue(
                    CODE_CORRUPT_NOT_CAUGHT,
                    f"破坏注入 {self.corruption_check.target} 没有被回归抓到，"
                    "说明比对环节形同虚设（回归是假通过）",
                    scene=self.corruption_check.scene,
                )
            )
        return sorted(
            collected,
            key=lambda item: (
                item.scene,
                STAGES.index(item.layer) if item.layer in STAGES else len(STAGES),
                item.code,
                item.line,
            ),
        )

    @property
    def ok(self) -> bool:
        return not self.failures()

    def failing_scenes(self) -> tuple[str, ...]:
        seen = {item.scene for item in self.failures() if item.scene}
        return tuple(scene for scene in (result.scene for result in self.results) if scene in seen)

    def root_layer(self) -> str:
        return root_layer(self.failures())

    def root_scene(self) -> str:
        for item in self.failures():
            if item.layer and item.scene:
                return item.scene
        for item in self.failures():
            if item.scene:
                return item.scene
        return ""

    @property
    def record_paths(self) -> tuple[Path, ...]:
        return tuple(result.record_path for result in self.results if result.record_path)

    @property
    def total_lines(self) -> int:
        return sum(len(result.run.documents) for result in self.results if result.run is not None)

    # --- 验收对照（Issue #22） --------------------------------------------

    def acceptance(self) -> list[tuple[bool, str, str]]:
        """四条可观察验收 + 两条本层加的硬条件，逐条附证据。"""
        lane_ok = bool(self.results) and all(result.run is not None for result in self.results)
        checks: list[tuple[bool, str, str]] = []

        known = [fixture_matches(result.run) for result in self.results if result.run is not None]
        matched = [flag for flag in known if flag is True]
        compared = [flag for flag in known if flag is not None]
        generation_note = (
            f"；生成层复现 LB-002 入库 fixtures {len(matched)}/{len(compared)}"
            if compared
            else "；没有可比对的入库场景图"
        )
        checks.append(
            (
                lane_ok,
                "全链路命令退出码 0（六个阶段真跑过且记录落盘）",
                f"{len(self.results)} 个场景 × 六阶段；落盘 {len(self.record_paths)} 份记录，"
                f"共 {self.total_lines} 行，实测 {self.wall_ms:.1f} ms{generation_note}",
            )
        )

        shape_issues = [item for item in self.failures() if item.code.startswith("LB-E00")]
        header_ok = lane_ok and not shape_issues
        kinds: list[str] = []
        for result in self.results:
            if result.run is not None:
                for kind in result.run.kinds:
                    if kind not in kinds:
                        kinds.append(kind)
        checks.append(
            (
                header_ok,
                "运行记录首行是 RunHeader 且包含全部层的 kind",
                f"kind 覆盖 {len(kinds)} 种：{'、'.join(kinds) or '—'}；"
                f"每份记录第一行都是 RunHeader（形状问题 {len(shape_issues)} 条）",
            )
        )

        baseline_issues = [
            item
            for item in self.failures()
            if item.code in ("LB-E010", "LB-E013")
        ]
        checks.append(
            (
                lane_ok and not baseline_issues,
                "与黄金基准一致（逐层指纹 + 计数 + 行数）",
                f"基准 {self.directory / BASELINE_NAME}：{len(self.baseline.get('scenes', []))} 个场景，"
                f"指纹按 canonical JSON + sha256，不含实测耗时；不一致 {len(baseline_issues)} 条"
                + ("（本轮为 --update-baseline，基准刚被重写）" if self.baseline_updated else ""),
            )
        )

        if self.corruption_check is None:
            corrupt_ok, corrupt_evidence = (
                self.corrupt != "none",
                f"本轮用 --corrupt {self.corrupt} 直接跑损坏链路，未做额外自检"
                if self.corrupt != "none"
                else "未做破坏注入自检（--no-self-check）",
            )
        else:
            corrupt_ok = self.corruption_check.detected
            corrupt_evidence = self.corruption_check.describe()
            if corrupt_ok and self.corruption_check.issues:
                corrupt_evidence += "；首条问题：" + self.corruption_check.issues[0].describe()
        checks.append((corrupt_ok, "人为破坏一个夹具时回归明确失败并指出层级", corrupt_evidence))

        bound_issues = [item for item in self.failures() if item.code == CODE_TIME_BOUND]
        checks.append(
            (
                not bound_issues and self.wall_ms <= self.time_bound_ms,
                "回归在固定时间内完成",
                f"实测 {self.wall_ms:.1f} ms / 上界 {self.time_bound_ms:g} ms"
                f"（单条记录上界 {self.record_bound_ms:g} ms）",
            )
        )
        return checks

    def passes(self) -> bool:
        return self.ok and all(ok for ok, _criterion, _evidence in self.acceptance())

    # --- 机读 -------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "directory": str(self.directory),
            "out_dir": str(self.out_dir),
            "baseline": str(self.baseline_path) if self.baseline_path else str(self.directory / BASELINE_NAME),
            "baseline_updated": self.baseline_updated,
            "seed": self.seed,
            "corrupt": self.corrupt,
            "wall_ms": self.wall_ms,
            "time_bound_ms": self.time_bound_ms,
            "record_bound_ms": self.record_bound_ms,
            "root_layer": self.root_layer(),
            "scenes": [result.to_dict() for result in self.results],
            "corruption_check": self.corruption_check.to_dict() if self.corruption_check else None,
            "failures": [item.to_dict() for item in self.failures()],
            "acceptance": [
                {"ok": ok, "criterion": criterion, "evidence": evidence}
                for ok, criterion, evidence in self.acceptance()
            ],
        }


def build_report(
    directory: str | Path = DEFAULT_DIR,
    *,
    out_dir: str | Path = DEFAULT_OUT,
    scenes: Sequence[str] | None = None,
    seed: int | None = None,
    corrupt: str = "none",
    write_records: bool = True,
    self_check: bool = True,
    update_baseline: bool = False,
    time_bound_ms: float | None = None,
    record_bound_ms: float | None = None,
    context: Any | None = None,
) -> RegressionReport:
    """跑一遍完整回归并返回报告（不打印、不退出——打印与退出码交给 CLI）。"""
    started = time.perf_counter()
    base = Path(directory)
    target = Path(out_dir)
    effective_seed = DEFAULT_SEED if seed is None else int(seed)
    bound_total = TIME_BOUND_MS if time_bound_ms is None else float(time_bound_ms)
    bound_record = RECORD_BOUND_MS if record_bound_ms is None else float(record_bound_ms)

    if update_baseline and corrupt != "none":
        raise ValueError("--update-baseline 与 --corrupt 不能同时用：基准只能用健康链路生成")

    baseline = load_baseline(base) if not update_baseline else {}
    if scenes:
        requested = tuple(scenes)
    elif update_baseline:
        requested = tuple(sorted((context or SynthContext.load()).presets))
    else:
        requested = scene_names(baseline)
    report = RegressionReport(
        directory=base,
        out_dir=target,
        seed=effective_seed,
        corrupt=corrupt,
        baseline=baseline,
        time_bound_ms=bound_total,
        record_bound_ms=bound_record,
        baseline_path=base / BASELINE_NAME,
    )

    runs: list[ChainRun] = []
    for scene in requested:
        started_scene = time.perf_counter()
        run = run_chain(
            scene,
            out_dir=target,
            seed=effective_seed,
            corrupt=corrupt,
            context=context,
            write=write_records,
        )
        runs.append(run)
        issues = list(
            validate_record(
                run.documents,
                scene=scene,
                image_exists=_image_resolver(run, target),
            )
        )
        if update_baseline:
            entry = None
        else:
            entry = scene_entry(baseline, scene)
        if not update_baseline:
            issues.extend(compare_run(run, entry, scene=scene))
        if run.wall_ms > bound_record:
            issues.append(
                issue(
                    CODE_TIME_BOUND,
                    f"单条记录实测 {run.wall_ms:.1f} ms 超过上界 {bound_record:g} ms",
                    scene=scene,
                )
            )
        report.results.append(
            SceneResult(
                scene=scene,
                run=run,
                record_path=run.record_path,
                issues=issues,
                wall_ms=_ms(started_scene),
            )
        )

    if update_baseline:
        document = baseline_document(
            runs, seed=effective_seed, time_bound_ms=bound_total, record_bound_ms=bound_record
        )
        report.baseline_path = write_baseline(base, document)
        report.baseline = document
        report.baseline_updated = True

    if self_check and corrupt == "none" and report.results:
        report.corruption_check = _corruption_probe(
            report.results[0].scene,
            baseline=report.baseline,
            seed=effective_seed,
            out_dir=target / "corrupt",
            target=DEFAULT_CORRUPT_PROBE,
            context=context,
        )

    report.wall_ms = _ms(started)
    if report.wall_ms > bound_total:
        report.issues.append(
            issue(
                CODE_TIME_BOUND,
                f"整份回归实测 {report.wall_ms:.1f} ms 超过固定上界 {bound_total:g} ms",
            )
        )
    return report


def _image_resolver(run: ChainRun, out_dir: Path):
    """``image_ref`` 解析：先按记录所在目录，再按当前工作目录（与 LB-008 视图同口径）。"""
    base = run.record_path.parent if run.record_path is not None else out_dir

    def exists(image_ref: str) -> bool:
        candidate = Path(image_ref)
        if candidate.is_absolute():
            return candidate.is_file()
        return (base / candidate).is_file() or candidate.is_file()

    return exists


def _corruption_probe(
    scene: str,
    *,
    baseline: dict[str, Any],
    seed: int,
    out_dir: Path,
    target: str,
    context: Any | None,
) -> CorruptionCheck:
    """对 ``scene`` 注入 ``target`` 破坏，要求比对**必须**失败并定位到层。"""
    started = time.perf_counter()
    run = run_chain(
        scene,
        out_dir=out_dir,
        seed=seed,
        corrupt=target,
        context=context,
        write=True,
    )
    issues = list(validate_record(run.documents, scene=scene, image_exists=_image_resolver(run, out_dir)))
    issues.extend(compare_run(run, scene_entry(baseline, scene), scene=scene))
    layer = root_layer(issues)
    return CorruptionCheck(
        target=target,
        scene=scene,
        detected=bool(issues) and bool(layer),
        detected_layer=layer,
        issues=issues,
        record_path=run.record_path,
        wall_ms=_ms(started),
    )


def failure_summary(report: RegressionReport) -> str:
    """失败摘要：根因层 + 逐条差异 + 复现命令。全部通过时只有一行。"""
    failures = report.failures()
    if not failures:
        return "失败摘要：无（全部场景与黄金基准一致）"
    lines = [f"失败摘要：回归失败（{len(failures)} 项）"]
    layer = report.root_layer()
    scene = report.root_scene()
    if layer:
        lines.append(f"  最早失败层：{layer} —— {STAGE_LABELS.get(layer, layer)}")
    else:
        lines.append("  最早失败层：（未能定位到层，见下方逐条问题）")
    if scene:
        lines.append(f"  最早失败场景：{scene}")
    for item in failures:
        lines.append(f"  - {item.describe()}")
    reproduce = ["liveblocks e2e regression", f"--dir {report.directory}", f"--out {report.out_dir}"]
    if scene:
        reproduce.append(f"--scene {scene}")
    if report.corrupt != "none":
        reproduce.append(f"--corrupt {report.corrupt}")
    lines.append("复现：" + " ".join(reproduce))
    return "\n".join(lines)


def format_report(report: RegressionReport) -> str:
    """人读报告：逐场景结论 + 破坏注入自检 + 验收对照 + 失败摘要。"""
    out: list[str] = []
    out.append("端到端回归报告（LB-009：生成 → 识别 → 排列 → 解析 → IR → 模拟）")
    out.append(
        f"基准 {report.baseline_path}（{len(report.baseline.get('scenes', []))} 个场景，种子 {report.seed}）"
    )
    out.append(
        f"输出 {report.out_dir}（记录 {len(report.record_paths)} 份"
        + ("，本轮已重写基准" if report.baseline_updated else "")
        + "）"
    )
    if report.corrupt != "none":
        out.append(f"破坏注入：{report.corrupt}（本轮所有场景都是被破坏的链路，期望失败）")
    out.append("")
    out.append("逐场景：")
    for result in report.results:
        flag = "OK  " if result.ok else "FAIL"
        summary = result.run.summary() if result.run is not None else "（没有产物）"
        out.append(f"  {flag} {result.scene:<16} {summary}  记录 {_lines(result)} 行  {result.wall_ms:8.1f} ms")
        for item in result.issues:
            out.append(f"       ! {item.describe()}")
    if report.corruption_check is not None:
        out.append("")
        out.append("破坏注入自检：" + report.corruption_check.describe())
        for item in report.corruption_check.issues[:2]:
            out.append(f"  ! {item.describe()}")
        if report.corruption_check.record_path is not None:
            out.append(f"  被破坏的记录：{report.corruption_check.record_path}")
    out.append("")
    out.append("验收对照：")
    for ok, criterion, evidence in report.acceptance():
        out.append(f"  [{'通过' if ok else '未通过'}] {criterion}")
        out.append(f"        证据：{evidence}")
    out.append("")
    out.append(failure_summary(report))
    return "\n".join(out) + "\n"


def _lines(result: SceneResult) -> int:
    return len(result.run.documents) if result.run is not None else 0


__all__ = [
    "DEFAULT_DIR",
    "DEFAULT_OUT",
    "DEFAULT_CORRUPT_PROBE",
    "FIXTURE_SCENE_DIR",
    "fixture_matches",
    "BaselineError",
    "SceneResult",
    "CorruptionCheck",
    "RegressionReport",
    "build_report",
    "failure_summary",
    "format_report",
    "root_layer",
]
