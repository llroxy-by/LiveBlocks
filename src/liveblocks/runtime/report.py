"""执行自检报告：把 ``fixtures/exec/`` 的 ``Ir`` 夹具真跑一遍，输出可读摘要与验收对照。

与 LB-003 / LB-004 / LB-005 / LB-006 的报告同一思路：**证据来自真跑一遍**。每个夹具在
``index.json``（清单）里声明「期望什么 ``status``、跑了几步、几条事件、哪些 ``LB-R0xx`` 码、
``limits`` 是多少、要不要中途中断」，报告逐条核对，并把 Issue #18 的四条可观察验收当成硬条件：

1. 样例 IR 执行后事件 ``seq`` 严格递增且 ``backend: "sim"``；
2. 死循环（P0 里的等价形态：超长步骤 / 超长 ``wait``）样例以 ``limit_exceeded`` / ``timeout`` 结束
   而不是挂死；
3. 测试总耗时在设定上限内；
4. 带一个可读的状态输出（最终状态 JSON）。

另外三条范围项也当硬条件：``limits`` 来自 IR（回显一致）、中断生效、golden（事件流 + 最终状态）
逐字节一致。清单格式见 ``fixtures/README.md`` §7。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from .semantics import DOC_PATH, runtime_document_gaps
from .sim import SimExecutor, SimRun, ir_from_document, validate_run

#: 仓库根（源码布局 ``src/liveblocks/runtime/report.py`` 往上四级）；安装成 wheel 时可能不成立，
#: 因此 ``build_report`` 允许用 ``doc_path`` 显式指定执行层文档。
_HERE = Path(__file__).resolve()
REPO_ROOT = _HERE.parents[3] if len(_HERE.parents) > 3 else _HERE.parent

#: 单个夹具的实测墙钟耗时上限（毫秒）：虚拟时钟不睡真实时间，跑几十步应是毫秒级。
PER_RUN_WALL_MS_BOUND = 250.0

#: 整份报告（全部夹具真跑一遍）的实测墙钟耗时上限（毫秒）。
REPORT_WALL_MS_BOUND = 3000.0

#: 「非终止样例」的期望状态：只要**不是**挂死、且以这两个状态之一收尾就算通过。
NON_TERMINATING_STATUSES = ("limit_exceeded", "timeout")


@dataclass(frozen=True)
class FixtureRun:
    """一个执行夹具的结果 + 各项结论。"""

    name: str
    ir_path: Path
    events_path: Path
    state_path: Path
    expected_status: str
    expected_steps: int
    expected_events: int
    expected_codes: tuple[str, ...]
    expected_limits: dict[str, int]
    abort_at_step: int | None = None
    source: str = ""
    note: str = ""
    run: SimRun | None = None
    golden_events: str = ""
    golden_state: str = ""
    ir_error: str = ""
    run_error: str = ""
    stream_errors: tuple[str, ...] = ()

    @property
    def status(self) -> str:
        return "" if self.run is None else self.run.status

    @property
    def steps_executed(self) -> int:
        return 0 if self.run is None else self.run.steps_executed

    @property
    def events_count(self) -> int:
        return 0 if self.run is None else len(self.run.events)

    @property
    def codes(self) -> tuple[str, ...]:
        return () if self.run is None else self.run.codes

    @property
    def limits(self) -> dict[str, int]:
        return {} if self.run is None else dict(self.run.state["limits"])

    @property
    def wall_ms(self) -> float:
        return 0.0 if self.run is None else self.run.wall_ms

    @property
    def duration_ms(self) -> float:
        return 0.0 if self.run is None else self.run.duration_ms

    @property
    def events_match(self) -> bool:
        return bool(self.run is not None) and self.run.events_text() == self.golden_events

    @property
    def state_match(self) -> bool:
        return bool(self.run is not None) and self.run.state_text() == self.golden_state

    @property
    def seq_strictly_increasing(self) -> bool:
        if self.run is None:
            return False
        seqs = [event["seq"] for event in self.run.events]
        return seqs == list(range(1, len(seqs) + 1))

    @property
    def sim_only(self) -> bool:
        if self.run is None:
            return False
        return all(event["backend"] == "sim" for event in self.run.events) and (
            self.run.result["backend"] == "sim" and self.run.state["backend"] == "sim"
        )

    @property
    def clock_matches_semantics(self) -> bool:
        """``duration_ms`` 必须等于各步 ``cost_ms`` 之和（超时运行额外截到 ``timeout_ms``）。"""
        if self.run is None:
            return False
        total = 0.0
        for event in self.run.events:
            if event["phase"] == "finish":
                total += float(event["detail"].get("cost_ms", 0.0))
        expected = total
        if self.status == "timeout":
            expected = float(self.run.state["limits"]["timeout_ms"])
        return abs(expected - self.run.duration_ms) < 1e-6

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "ir": str(self.ir_path),
            "events": str(self.events_path),
            "state": str(self.state_path),
            "expected": {
                "status": self.expected_status,
                "steps": self.expected_steps,
                "events": self.expected_events,
                "codes": list(self.expected_codes),
                "limits": dict(self.expected_limits),
                "abort_at_step": self.abort_at_step,
            },
            "actual": {
                "status": self.status,
                "steps": self.steps_executed,
                "events": self.events_count,
                "codes": list(self.codes),
                "limits": self.limits,
                "duration_ms": self.duration_ms,
                "wall_ms": round(self.wall_ms, 3),
                "seq_strictly_increasing": self.seq_strictly_increasing,
                "sim_only": self.sim_only,
                "events_match": self.events_match,
                "state_match": self.state_match,
            },
            "ir_error": self.ir_error,
            "run_error": self.run_error,
            "stream_errors": list(self.stream_errors),
            "source": self.source,
            "note": self.note,
            "result": None if self.run is None else self.run.result,
            "final_state": None if self.run is None else self.run.state,
        }


@dataclass(frozen=True)
class ExecReport:
    """一组夹具的报告 + Issue #18 的验收对照。"""

    directory: Path
    fixtures: tuple[FixtureRun, ...]
    doc_gaps: tuple[str, ...] = ()
    manifest_error: str = ""
    wall_ms: float = 0.0
    report_bound_ms: float = REPORT_WALL_MS_BOUND
    run_bound_ms: float = PER_RUN_WALL_MS_BOUND

    def to_dict(self) -> dict[str, Any]:
        return {
            "directory": str(self.directory),
            "manifest_error": self.manifest_error,
            "doc_gaps": list(self.doc_gaps),
            "wall_ms": round(self.wall_ms, 3),
            "report_bound_ms": self.report_bound_ms,
            "run_bound_ms": self.run_bound_ms,
            "fixtures": [fixture.to_dict() for fixture in self.fixtures],
            "acceptance": [
                {"ok": ok, "criterion": criterion, "evidence": evidence}
                for ok, criterion, evidence in self.acceptance()
            ],
        }

    @property
    def usable(self) -> list[FixtureRun]:
        return [fixture for fixture in self.fixtures if fixture.run is not None]

    # -- 验收对照 -----------------------------------------------------------
    def acceptance(self) -> list[tuple[bool, str, str]]:
        results: list[tuple[bool, str, str]] = []

        # 1. 事件 seq 严格递增且 backend: "sim"
        problems: list[str] = []
        for fixture in self.fixtures:
            if fixture.run is None:
                problems.append(f"{fixture.name}: 没跑出结果")
                continue
            if not fixture.seq_strictly_increasing:
                problems.append(f"{fixture.name}: seq 不是 1..n 的严格递增序列")
            if not fixture.sim_only:
                problems.append(f"{fixture.name}: 出现非 sim 后端")
            problems.extend(f"{fixture.name}: {error}" for error in fixture.stream_errors)
        ok = bool(self.fixtures) and not problems
        detail = (
            f"{len(self.usable)} 个夹具的事件流全部 seq 严格递增、backend=sim，"
            f"共 {sum(fixture.events_count for fixture in self.usable)} 条事件"
            if not problems
            else "；".join(problems)
        )
        results.append((ok, '事件 seq 严格递增且 backend: "sim"', detail))

        # 2. 非终止样例以 limit_exceeded / timeout 结束，而不是挂死
        non_terminating = [
            fixture for fixture in self.fixtures if fixture.expected_status in NON_TERMINATING_STATUSES
        ]
        problems = []
        for fixture in non_terminating:
            if fixture.status not in NON_TERMINATING_STATUSES:
                problems.append(f"{fixture.name}: 期望 {list(NON_TERMINATING_STATUSES)}，实际 {fixture.status!r}")
            elif fixture.wall_ms > self.run_bound_ms:
                problems.append(f"{fixture.name}: 实测 {fixture.wall_ms:.1f} ms 超过 {self.run_bound_ms:g} ms")
        ok = bool(non_terminating) and not problems
        detail = (
            "、".join(f"{fixture.name}→{fixture.status}（实测 {fixture.wall_ms:.2f} ms）" for fixture in non_terminating)
            if not problems
            else "；".join(problems)
        )
        results.append((ok, "非终止样例以 limit_exceeded / timeout 结束而非挂死", detail))

        # 3. 实测耗时在设定上限内
        slow = [f"{fixture.name}: {fixture.wall_ms:.1f} ms" for fixture in self.usable if fixture.wall_ms > self.run_bound_ms]
        ok = bool(self.usable) and not slow and self.wall_ms <= self.report_bound_ms
        detail = (
            f"全部 {len(self.usable)} 个夹具真跑一遍共 {self.wall_ms:.2f} ms"
            f"（单夹具上限 {self.run_bound_ms:g} ms，整份报告上限 {self.report_bound_ms:g} ms）"
        )
        if slow:
            detail = "这些夹具超过单夹具上限：" + "、".join(slow)
        elif self.wall_ms > self.report_bound_ms:
            detail = f"整份报告实测 {self.wall_ms:.2f} ms 超过上限 {self.report_bound_ms:g} ms"
        results.append((ok, "测试总耗时在设定上限内（虚拟时钟不睡真实时间）", detail))

        # 3b. 虚拟时钟的耗时与语义表一致（duration = Σ 各步 cost）
        problems = [f"{fixture.name}: duration_ms 与各步 cost_ms 之和不符" for fixture in self.usable if not fixture.clock_matches_semantics]
        ok = bool(self.usable) and not problems
        detail = (
            f"{len(self.usable)} 个夹具的 duration_ms 都等于各步 cost_ms 之和"
            "（超时样例另按 limits.timeout_ms 截断）"
            if not problems
            else "；".join(problems)
        )
        results.append((ok, "虚拟时钟：duration_ms = Σ 各步 cost_ms", detail))

        # 4. 带了可读的状态输出（最终状态 JSON）
        problems = []
        for fixture in self.fixtures:
            if fixture.run is None:
                problems.append(f"{fixture.name}: 没有最终状态")
                continue
            if not fixture.state_path.is_file():
                problems.append(f"{fixture.name}: 缺少状态文件 {fixture.state_path}")
            if not fixture.state_match:
                problems.append(f"{fixture.name}: 最终状态与入库 JSON 不一致")
        ok = bool(self.fixtures) and not problems
        detail = (
            f"{len(self.fixtures)} 个夹具都产出并校验了最终状态 JSON（kind=ExecState，含 world/limits/事件计数）"
            if not problems
            else "；".join(problems)
        )
        results.append((ok, "带一个可读的状态输出（最终状态 JSON）", detail))

        # 5. limits 来自 IR，并被回显到事件流与结果
        problems = []
        for fixture in self.fixtures:
            if fixture.run is None:
                problems.append(f"{fixture.name}: 没跑出结果，无法核对 limits")
                continue
            if fixture.limits != fixture.expected_limits:
                problems.append(f"{fixture.name}: limits {fixture.limits} 与清单 {fixture.expected_limits} 不符")
            start_log = fixture.run.events[0]["detail"].get("limits") if fixture.run.events else None
            if start_log != fixture.expected_limits:
                problems.append(f"{fixture.name}: 起步日志里的 limits {start_log} 与 IR 不符")
            if fixture.expected_status == "limit_exceeded" and fixture.steps_executed != fixture.expected_limits.get(
                "max_steps"
            ):
                problems.append(
                    f"{fixture.name}: 步数上限生效时应有 {fixture.expected_limits.get('max_steps')} 步，"
                    f"实际 {fixture.steps_executed}"
                )
            if fixture.expected_status == "timeout" and abs(
                fixture.duration_ms - float(fixture.expected_limits.get("timeout_ms", 0))
            ) > 1e-6:
                problems.append(
                    f"{fixture.name}: 超时时虚拟时钟应停在 {fixture.expected_limits.get('timeout_ms')} ms，"
                    f"实际 {fixture.duration_ms} ms"
                )
        ok = bool(self.fixtures) and not problems
        detail = (
            f"{len(self.fixtures)} 个夹具的 limits 都取自 IR（起步日志、ExecResult、ExecState 三处一致）"
            if not problems
            else "；".join(problems)
        )
        results.append((ok, "执行限制来自 IR（limits 显式回显）", detail))

        # 6. 中断（abort）在步骤边界生效
        abortable = [fixture for fixture in self.fixtures if fixture.abort_at_step is not None]
        problems = []
        for fixture in abortable:
            if fixture.status != "aborted":
                problems.append(f"{fixture.name}: 期望 aborted，实际 {fixture.status!r}")
            elif fixture.steps_executed != fixture.abort_at_step:
                problems.append(
                    f"{fixture.name}: 应在第 {fixture.abort_at_step} 步中断，实际执行 {fixture.steps_executed} 步"
                )
            elif "LB-R004" not in fixture.codes:
                problems.append(f"{fixture.name}: 缺少 LB-R004 诊断（实际 {list(fixture.codes)}）")
        ok = bool(abortable) and not problems
        detail = (
            "、".join(
                f"{fixture.name}→aborted@{fixture.steps_executed} 步" for fixture in abortable
            )
            if not problems
            else "；".join(problems)
        )
        results.append((ok, "中断在步骤边界生效并以 aborted 结束", detail))

        # 7. golden（事件流 + 最终状态）逐字节一致 → 同一 IR 跑两次结果一致
        problems = [fixture.name for fixture in self.fixtures if not fixture.events_match]
        state_problems = [fixture.name for fixture in self.fixtures if not fixture.state_match]
        ok = bool(self.fixtures) and not problems and not state_problems
        detail = f"{len(self.fixtures)} 个夹具的事件流（JSONL）与最终状态（JSON）都与入库 golden 逐字节一致"
        if problems or state_problems:
            detail = "事件流不一致：" + ("、".join(problems) or "-") + "；最终状态不一致：" + (
                "、".join(state_problems) or "-"
            )
        results.append((ok, "事件流与最终状态是确定性的（golden 快照一致）", detail))

        return results


def load_manifest(directory: Path) -> dict[str, Any]:
    """读取夹具清单（``index.json``）；结构不对时抛 ``ValueError``。"""
    path = directory / "index.json"
    if not path.is_file():
        raise ValueError(f"缺少执行夹具清单：{path}")
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict) or not isinstance(document.get("fixtures"), list):
        raise ValueError(f"{path} 必须是含 fixtures 数组的对象")
    return document


def run_fixture(entry: dict[str, Any], directory: Path) -> FixtureRun:
    """按清单条目真跑一遍，返回逐项结论。"""
    name = str(entry["name"])
    fixture = FixtureRun(
        name=name,
        ir_path=directory / str(entry["ir"]),
        events_path=directory / str(entry["events"]),
        state_path=directory / str(entry["state"]),
        expected_status=str(entry.get("status", "ok")),
        expected_steps=int(entry.get("steps", 0)),
        expected_events=int(entry.get("events_count", 0)),
        expected_codes=tuple(str(code) for code in entry.get("codes", [])),
        expected_limits={str(key): int(value) for key, value in dict(entry.get("limits", {})).items()},
        abort_at_step=None if entry.get("abort_at_step") is None else int(entry["abort_at_step"]),
        source=str(entry.get("source", "")),
        note=str(entry.get("note", "")),
    )

    golden_events = (
        fixture.events_path.read_text(encoding="utf-8") if fixture.events_path.is_file() else ""
    )
    golden_state = fixture.state_path.read_text(encoding="utf-8") if fixture.state_path.is_file() else ""
    if not golden_events:
        return replace(fixture, ir_error=f"找不到 golden 事件流 {fixture.events_path}")
    if not golden_state:
        return replace(fixture, ir_error=f"找不到 golden 最终状态 {fixture.state_path}")

    if not fixture.ir_path.is_file():
        return replace(
            fixture, golden_events=golden_events, golden_state=golden_state, ir_error=f"找不到 IR 夹具 {fixture.ir_path}"
        )
    try:
        document = json.loads(fixture.ir_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        return replace(
            fixture,
            golden_events=golden_events,
            golden_state=golden_state,
            ir_error=f"{fixture.ir_path} 不是合法 JSON：{error}",
        )
    try:
        ir = ir_from_document(document)
    except ValueError as error:
        return replace(
            fixture,
            golden_events=golden_events,
            golden_state=golden_state,
            ir_error=f"输入不是合法 Ir：{error}",
        )

    abort_at = fixture.abort_at_step
    should_abort = None
    if abort_at is not None:
        should_abort = lambda step_index, elapsed_ms: step_index >= abort_at  # noqa: E731
    executor = SimExecutor(run_id=f"run-{name}", should_abort=should_abort)
    try:
        run = executor.run(ir)
    except Exception as error:  # noqa: BLE001 —— 报告要如实记录崩溃，而不是让报告本身崩掉
        return replace(
            fixture,
            golden_events=golden_events,
            golden_state=golden_state,
            run_error=f"{type(error).__name__}: {error}",
        )
    errors: list[str] = []
    try:
        validate_run(run)
    except ValueError as error:
        errors.append(str(error))
    return replace(
        fixture,
        golden_events=golden_events,
        golden_state=golden_state,
        run=run,
        stream_errors=tuple(errors),
    )


def build_report(directory: Path, *, doc_path: Path | None = None) -> ExecReport:
    """对夹具目录真跑一遍，并按 Issue #18 的验收给出结论。"""
    started = time.perf_counter()
    manifest_error = ""
    fixtures: list[FixtureRun] = []
    try:
        manifest = load_manifest(directory)
    except ValueError as error:
        manifest_error = str(error)
        manifest = {"fixtures": []}
    for entry in manifest["fixtures"]:
        try:
            fixtures.append(run_fixture(entry, directory))
        except (ValueError, KeyError, TypeError) as error:
            manifest_error = f"清单条目有问题：{error}"
            break

    doc = doc_path if doc_path is not None else REPO_ROOT / DOC_PATH
    if not doc.is_file():
        doc_gaps: tuple[str, ...] = (f"缺少执行层文档 {doc}",)
    else:
        doc_gaps = tuple(runtime_document_gaps(doc.read_text(encoding="utf-8")))
    wall_ms = (time.perf_counter() - started) * 1000.0
    return ExecReport(
        directory=directory,
        fixtures=tuple(fixtures),
        doc_gaps=doc_gaps,
        manifest_error=manifest_error,
        wall_ms=wall_ms,
    )


def format_report(report: ExecReport) -> str:
    """可读报告：每个夹具一行 + 验收对照 + 合计。"""
    lines: list[str] = [f"执行夹具目录 {report.directory}"]
    if report.manifest_error:
        lines.append(f"清单问题：{report.manifest_error}")
    for fixture in report.fixtures:
        flag = "Ok" if _fixture_ok(fixture) else "FAIL"
        codes = ",".join(fixture.codes) or "-"
        detail = fixture.ir_error or fixture.run_error
        if not detail:
            detail = (
                f"事件 {fixture.events_count} 条（seq 1..{fixture.events_count}，backend=sim），"
                f"虚拟耗时 {fixture.duration_ms:g} ms，实测 {fixture.wall_ms:.2f} ms，"
                f"事件流{'一致' if fixture.events_match else '不一致'}，"
                f"状态{'一致' if fixture.state_match else '不一致'}"
            )
        lines.append(
            f"{flag:<4} {fixture.name:<26} status {fixture.status or '-':<15} 步骤 {fixture.steps_executed}"
            f"  诊断 [{codes}]  {detail}"
        )
    lines.append("")
    lines.append("验收对照：")
    for ok, criterion, evidence in report.acceptance():
        lines.append(f"  [{'通过' if ok else '未通过'}] {criterion}")
        lines.append(f"          依据：{evidence}")
    lines.append("")
    lines.append(
        f"合计 {len(report.fixtures)} 个夹具 / 执行步骤 "
        f"{sum(fixture.steps_executed for fixture in report.fixtures)} 个 / 事件 "
        f"{sum(fixture.events_count for fixture in report.fixtures)} 条 / 诊断 "
        f"{sum(len(fixture.codes) for fixture in report.fixtures)} 条 / "
        f"执行层文档缺口 {len(report.doc_gaps)} 项 / 实测总耗时 {report.wall_ms:.2f} ms"
    )
    return "\n".join(lines) + "\n"


def _fixture_ok(fixture: FixtureRun) -> bool:
    if fixture.ir_error or fixture.run_error or fixture.stream_errors:
        return False
    if fixture.status != fixture.expected_status:
        return False
    if fixture.steps_executed != fixture.expected_steps:
        return False
    if fixture.events_count != fixture.expected_events:
        return False
    if tuple(fixture.codes) != fixture.expected_codes:
        return False
    if fixture.limits != fixture.expected_limits:
        return False
    if not fixture.events_match or not fixture.state_match:
        return False
    if not fixture.seq_strictly_increasing or not fixture.sim_only:
        return False
    return True


__all__ = [
    "NON_TERMINATING_STATUSES",
    "PER_RUN_WALL_MS_BOUND",
    "REPORT_WALL_MS_BOUND",
    "REPO_ROOT",
    "ExecReport",
    "FixtureRun",
    "build_report",
    "format_report",
    "load_manifest",
    "run_fixture",
]
