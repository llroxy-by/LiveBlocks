"""调试视图自检报告：把 ``fixtures/runs/`` 的运行记录真渲染一遍，打印验收对照。

与 LB-003 … LB-007 的报告同一思路（**证据来自真跑一遍**）：每个夹具在 ``index.json``
里声明「应该出现哪五层、多少条事件、哪些 ``LB-U0xx`` 问题码、渲染产物的 sha256」，
报告逐条核对，并把 Issue #20 的四条可观察验收当成硬条件：

1. 打开样例运行记录可看到五层数据同时呈现；
2. 回放同一记录两次结果一致；
3. 界面对缺失/损坏字段给出可见提示而非崩溃；
4. 附截图作为证据（截图存在、是真 PNG、且不是空白图——由 :mod:`liveblocks.ui.evidence` 量化）。

清单格式见 ``fixtures/README.md`` §8。
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from .docs import DOC_PATH, ui_document_gaps
from .evidence import ScreenshotStats, screenshot_stats
from .protocol import LAYER_ORDER, LAYER_TITLES, TIMING_LAYERS, UI_VERSION
from .record import parse_record_file
from .render import build_view
from .replay import build_replay

#: 仓库根（``src/liveblocks/ui/report.py`` 往上四级）。
_HERE = Path(__file__).resolve()
REPO_ROOT = _HERE.parents[3] if len(_HERE.parents) > 3 else _HERE.parent

#: 默认样例记录目录。
DEFAULT_RUN_DIR = Path("fixtures/runs")

#: 截图证据（Issue #20 第四条验收）。
EVIDENCE_FILES = (
    Path("docs/evidence/lb-008-debug-view.png"),
    Path("docs/evidence/lb-008-debug-view-step0.png"),
    Path("docs/evidence/lb-008-debug-view-all-ops.png"),
    Path("docs/evidence/lb-008-corrupt-record.png"),
)

#: 「两次回放一致」的比对项（全部是确定性渲染产物）。
REPLAY_ARTIFACTS = ("html", "text", "overlay")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass
class FixtureRender:
    """一个运行记录夹具真渲染一遍的结果。"""

    name: str
    path: Path
    family: str
    note: str = ""
    source: str = ""
    expect: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    layers: tuple[str, ...] = ()
    events: int = 0
    steps: int = 0
    issues: int = 0
    issue_codes: tuple[str, ...] = ()
    text: str = ""
    html: str = ""
    overlay: bytes = b""
    hashes: dict[str, str] = field(default_factory=dict)
    replay_wall_ms: float = 0.0
    render_wall_ms: float = 0.0
    determinism: tuple[bool, str] = (False, "未跑")
    step_probe: tuple[bool, str] = (False, "未跑")

    # --- 期望 vs 实际 -----------------------------------------------------

    @property
    def expected_layers(self) -> tuple[str, ...]:
        return tuple(self.expect.get("layers") or ())

    @property
    def layers_ok(self) -> bool:
        if self.family != "sample":
            return True
        missing = [layer for layer in LAYER_ORDER if layer not in self.layers]
        return not missing

    @property
    def codes_ok(self) -> bool:
        expected = set(self.expect.get("issue_codes") or ())
        return expected.issubset(set(self.issue_codes))

    @property
    def events_ok(self) -> bool:
        if "events" not in self.expect:
            return True
        return self.events == int(self.expect["events"])

    @property
    def hashes_ok(self) -> bool:
        expected = self.expect.get("hashes") or {}
        if not expected:
            return True
        return all(self.hashes.get(key) == value for key, value in expected.items())

    @property
    def hash_mismatches(self) -> tuple[str, ...]:
        expected = self.expect.get("hashes") or {}
        return tuple(key for key, value in expected.items() if self.hashes.get(key) != value)

    @property
    def visible_warnings(self) -> bool:
        """损坏记录也必须在渲染结果里**看得见**提示（不是只在 stderr 上）。"""
        if self.family != "corrupt":
            return True
        return "LB-U0" in self.html and 'id="warnings"' in self.html

    def passes(self) -> bool:
        return (
            not self.error
            and self.layers_ok
            and self.codes_ok
            and self.events_ok
            and self.hashes_ok
            and self.visible_warnings
            and self.determinism[0]
            and self.step_probe[0]
        )


def render_fixture(path: Path, *, expect: dict[str, Any] | None = None, name: str = "", family: str = "sample", note: str = "", source: str = "") -> FixtureRender:
    """把一份运行记录真渲染两遍：比对确定性、单步可用、产物哈希。"""
    fixture = FixtureRender(
        name=name or path.stem,
        path=path,
        family=family,
        note=note,
        source=source,
        expect=dict(expect or {}),
    )
    if not path.is_file():
        fixture.error = "夹具文件不存在"
        return fixture
    try:
        started = time.perf_counter()
        record = parse_record_file(path)
        replay = build_replay(record)
        fixture.replay_wall_ms = (time.perf_counter() - started) * 1000.0

        started = time.perf_counter()
        view = build_view(record, step=replay.total_steps, record_path=path)
        final_html = view.render_html()
        step0 = build_view(record, step=0, record_path=path)
        step0_html = step0.render_html()
        fixture.html = final_html
        fixture.text = view.render_text()
        fixture.overlay = view.overlay_png
        fixture.render_wall_ms = (time.perf_counter() - started) * 1000.0

        fixture.layers = view.complete_layers
        fixture.events = len(record.events)
        fixture.steps = len(record.steps)
        fixture.issues = len(view.issues)
        fixture.issue_codes = tuple(sorted({entry.code for entry in view.issues}))
        fixture.hashes = {
            "html_final": _sha256(final_html.encode("utf-8")),
            "html_step0": _sha256(step0_html.encode("utf-8")),
            "text_final": _sha256(fixture.text.encode("utf-8")),
            "overlay": _sha256(fixture.overlay),
        }

        # 第二条验收：同一记录回放两次，三种产物逐字节一致
        second = build_view(parse_record_file(path), step=replay.total_steps, record_path=path)
        pairs = {
            "html": second.render_html() == fixture.html,
            "text": second.render_text() == fixture.text,
            "overlay": second.overlay_png == fixture.overlay,
        }
        replay_same = build_replay(parse_record_file(path)).signature() == replay.signature()
        ok = all(pairs.values()) and replay_same
        detail = "、".join(f"{key}={'一致' if value else '不一致'}" for key, value in pairs.items())
        fixture.determinism = (ok, f"{detail}，时间线{'一致' if replay_same else '不一致'}")

        # 单步：第 0 帧与末帧必须不同（否则「单步」是假的），且越界被夹紧
        probed = (
            (replay.total_steps == 0 or step0_html != final_html)
            and replay.clamp(-5) == 0
            and replay.clamp(10**6) == replay.total_steps
        )
        fixture.step_probe = (
            probed,
            f"0..{replay.total_steps} 可单步，越界夹紧{'通过' if probed else '失败'}",
        )
    except Exception as exc:  # noqa: BLE001 - 报告要如实记录任何崩溃
        fixture.error = f"{type(exc).__name__}: {exc}"
    return fixture


@dataclass
class UiReport:
    """整个 ``fixtures/runs`` 目录的结果 + 四条验收对照。"""

    directory: Path
    fixtures: list[FixtureRender] = field(default_factory=list)
    manifest_error: str = ""
    doc_gaps: tuple[str, ...] = ()
    evidence: dict[str, Any] = field(default_factory=dict)
    evidence_problems: tuple[str, ...] = ()
    wall_ms: float = 0.0

    @property
    def samples(self) -> list[FixtureRender]:
        return [item for item in self.fixtures if item.family == "sample"]

    @property
    def corrupts(self) -> list[FixtureRender]:
        return [item for item in self.fixtures if item.family == "corrupt"]

    def acceptance(self) -> list[tuple[bool, str, str]]:
        samples = self.samples
        corrupts = self.corrupts
        five_layers = bool(samples) and all(item.layers_ok and not item.error for item in samples)
        five_detail = (
            "、".join(
                f"{item.name}: {'/'.join(LAYER_TITLES[layer] for layer in item.layers) or '（无）'}"
                for item in samples
            )
            or "没有样例记录"
        )
        deterministic = bool(self.fixtures) and all(item.determinism[0] for item in self.fixtures)
        deterministic_detail = (
            f"{len(self.fixtures)} 份记录各回放两次，"
            + "；".join(f"{item.name}: {item.determinism[1]}" for item in self.fixtures)
        )
        warned = bool(corrupts) and all(item.visible_warnings and not item.error for item in corrupts)
        warned_detail = (
            "、".join(f"{item.name}: {','.join(item.issue_codes) or '无'}" for item in corrupts) or "没有损坏记录夹具"
        )
        evidence_ok = not self.evidence_problems
        stats = self.evidence.get("stats") or {}
        evidence_detail = (
            "；".join(
                f"{name}: {info.get('describe', '?')}" for name, info in stats.items() if info.get("exists")
            )
            or "没有找到截图"
        )
        return [
            (five_layers, "打开样例运行记录可看到五层数据同时呈现", five_detail),
            (deterministic, "回放同一记录两次结果一致（HTML / 文本 / 叠加图 / 时间线逐字节比对）", deterministic_detail),
            (warned, "界面对缺失/损坏字段给出可见提示而非崩溃（渲染成功且视图里有 LB-U0xx 提示）", warned_detail),
            (
                evidence_ok,
                "附截图作为证据（真渲染：WebKitGTK + Xvfb，PNG 非空白，由 ui view 的 HTML 渲染）",
                evidence_detail if evidence_ok else "；".join(self.evidence_problems),
            ),
        ]

    def passes(self) -> bool:
        return all(ok for ok, _criterion, _evidence in self.acceptance()) and all(
            item.passes() for item in self.fixtures
        )


@lru_cache(maxsize=8)
def _cached_stats(path_str: str, mtime_ns: int, size: int) -> ScreenshotStats:
    """按「路径 + mtime + 大小」缓存截图统计（4 张 1440x1200 的真彩 PNG 解一遍要好几秒）。"""
    del mtime_ns, size  # 只当缓存键用
    return screenshot_stats(Path(path_str).read_bytes())


def _collect_evidence(paths: tuple[Path, ...]) -> tuple[dict[str, Any], tuple[str, ...]]:
    evidence: dict[str, Any] = {"stats": {}}
    problems: list[str] = []
    for relative in paths:
        path = REPO_ROOT / relative if not Path(relative).is_absolute() else Path(relative)
        if not path.is_file():
            evidence["stats"][str(relative)] = {"exists": False}
            problems.append(f"缺少截图证据：{relative}")
            continue
        try:
            info = path.stat()
            stats: ScreenshotStats = _cached_stats(str(path), info.st_mtime_ns, info.st_size)
        except (OSError, ValueError, Exception) as exc:  # noqa: BLE001 - 报告里如实记录
            evidence["stats"][str(relative)] = {"exists": True, "error": f"{type(exc).__name__}: {exc}"}
            problems.append(f"截图解码失败：{relative}（{exc}）")
            continue
        evidence["stats"][str(relative)] = {
            "exists": True,
            "describe": stats.describe(),
            "non_blank": stats.non_blank,
            "sha256": _sha256(path.read_bytes()),
            "width": stats.width,
            "height": stats.height,
        }
        if not stats.non_blank:
            problems.append(f"截图是空白图或层次过少：{relative}（{stats.describe()}）")
    return evidence, tuple(problems)


def build_report(directory: str | Path = DEFAULT_RUN_DIR) -> UiReport:
    """跑一遍 ``fixtures/runs``（样例 + 损坏记录），并核对文档与截图证据。"""
    started = time.perf_counter()
    run_dir = Path(directory)
    report = UiReport(directory=run_dir)
    manifest_path = run_dir / "index.json"
    manifest: dict[str, Any] = {}
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            report.manifest_error = f"{manifest_path} 读不出来：{exc}"
    else:
        report.manifest_error = f"缺少清单 {manifest_path}"
    entries = manifest.get("fixtures") if isinstance(manifest, dict) else None
    if not isinstance(entries, list):
        report.manifest_error = report.manifest_error or "清单里没有 fixtures 数组"
        entries = []
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("file"), str):
            report.manifest_error = f"{report.manifest_error}；清单里有一条不是对象的夹具条目".strip("；")
            continue
        path = run_dir / entry["file"]
        report.fixtures.append(
            render_fixture(
                path,
                expect=entry.get("expect") if isinstance(entry.get("expect"), dict) else {},
                name=str(entry.get("name") or path.stem),
                family=str(entry.get("family") or "sample"),
                note=str(entry.get("note") or ""),
                source=str(entry.get("source") or ""),
            )
        )
    report.doc_gaps = ui_document_gaps()
    report.evidence, report.evidence_problems = _collect_evidence(EVIDENCE_FILES)
    report.wall_ms = (time.perf_counter() - started) * 1000.0
    return report


def format_report(report: UiReport, *, doc_path: Path | None = None) -> str:
    """把报告排版成可读文本（终端/PR 证据用）。"""
    lines: list[str] = []
    lines.append(f"LB-008 调试视图自检（ui v{UI_VERSION}）：{report.directory}")
    lines.append("=" * 96)
    if report.manifest_error:
        lines.append(f"清单问题：{report.manifest_error}")
    partial = [item for item in report.fixtures if item.family == "partial"]
    lines.append(
        f"夹具 {len(report.fixtures)} 份（五层齐全的样例 {len(report.samples)} / 局部记录 "
        f"{len(partial)} / 损坏记录 {len(report.corrupts)}），整份报告实测 {report.wall_ms:.1f} ms"
    )
    header = f"{'夹具':<24}{'族':<9}{'层':<7}{'事件':<5}{'问题':<5}{'回放两次':<10}{'单步':<6}"
    lines.append(header)
    lines.append("-" * 96)
    for item in report.fixtures:
        if item.error:
            lines.append(f"{item.name:<24}{item.family:<9}渲染失败：{item.error}")
            continue
        layers = f"{len(item.layers)}/5"
        det = "一致" if item.determinism[0] else "不一致"
        step = "通过" if item.step_probe[0] else "失败"
        lines.append(
            f"{item.name:<24}{item.family:<9}{layers:<7}{item.events:<5}{item.issues:<5}{det:<10}{step:<6}"
            f" 码={','.join(item.issue_codes) or '无'}"
        )
        if item.note:
            lines.append(f"{'':<24}说明：{item.note}")
        if item.hash_mismatches:
            lines.append(f"{'':<24}哈希不符：{', '.join(item.hash_mismatches)}")
        if item.determinism[1]:
            lines.append(f"{'':<24}确定性：{item.determinism[1]}")
    lines.append("")
    lines.append("分层耗时（记录里的 RunHeader.timings_ms，回放时只读回显）")
    for item in report.fixtures:
        if item.error:
            continue
        record = parse_record_file(item.path)
        timings = record.timings
        shown = "、".join(
            f"{key}={timings[key]:.3f}ms" for key in TIMING_LAYERS if key in timings
        ) or "记录未提供"
        lines.append(f"  {item.name:<24}{shown}")
    lines.append("")
    lines.append(f"文档缺口：{len(report.doc_gaps)} 项" + (f"（{DOC_PATH.name}）" if doc_path is None else ""))
    for gap in report.doc_gaps:
        lines.append(f"  - {gap}")
    lines.append("")
    lines.append("截图证据（真渲染，非 mock-up）")
    for name, info in (report.evidence.get("stats") or {}).items():
        if not info.get("exists"):
            lines.append(f"  {name}: 缺失")
        elif info.get("error"):
            lines.append(f"  {name}: 解码失败（{info['error']}）")
        else:
            lines.append(f"  {name}: {info['describe']}，非空白={info['non_blank']}")
    lines.append("")
    lines.append("Issue #20 的四条可观察验收")
    for ok, criterion, evidence in report.acceptance():
        lines.append(f"  [{'通过' if ok else '失败'}] {criterion}")
        lines.append(f"        依据：{evidence}")
    lines.append("")
    lines.append("结论：" + ("全部通过" if report.passes() else "有失败项（见上）"))
    return "\n".join(lines) + "\n"


__all__ = [
    "DEFAULT_RUN_DIR",
    "EVIDENCE_FILES",
    "FixtureRender",
    "UiReport",
    "build_report",
    "format_report",
    "render_fixture",
]
