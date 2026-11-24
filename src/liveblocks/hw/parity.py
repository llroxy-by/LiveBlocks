"""双后端一致性（parity）：同一份 ``Ir`` 分别用 ``sim`` 与 ``hw`` 跑，比较事件流结构与关键状态。

``sim`` 与 ``hw`` 是**两条不同的代码路径**：一条在进程内按语义表走，一条把每一步编码成串口帧、
经 pty 交给设备模拟器执行、再把设备回报的 ``state_delta`` 写回事件流。两者的一致性不是「同一份
结果抄两遍」，而是：同一张语义表（:mod:`liveblocks.runtime.semantics` 与 ``apply_step``）、
同一套判定顺序（中断 → 步数 → 语义 → 超时）、同一批终止文案（:mod:`liveblocks.runtime.sim`
的 ``*_stop`` 函数）。

比较口径（`docs/HW.md` §7 逐条对应）：

=================  ==========================================================
比什么             怎么比
=================  ==========================================================
事件条数 / 顺序     数量相等，``seq`` 都是 1..n
``phase``           逐条相等
``step_id``         逐条相等
``at_ms``           逐条相等（虚拟时钟，两边同一套换算）
``detail``          逐条相等，唯一例外是起步 ``log`` 的 ``detail.backend``（sim/hw）
``ExecResult``      ``status`` / ``steps_executed`` / ``duration_ms`` / 诊断码逐项相等
``ExecState``       ``status`` / ``steps_executed`` / ``duration_ms`` / ``limits`` / ``world`` /
                    ``events`` / ``last_seq`` 相等；``backend`` 必须各自是自己的名字
=================  ==========================================================

**不算一致的情况**：``hw`` 那次没有真的交换过帧（``link.frames_out == 0``）——那说明设备根本没参与，
一致性就没有意义。:func:`compare_runs` 会把它算作差异。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..runtime.sim import SimRun
from .executor import HwRun
from .protocol import BACKEND_HW
from ..runtime.semantics import BACKEND_SIM


@dataclass
class ParityReport:
    """一次双后端比较的结论。``ok`` 为 ``False`` 时 ``differences`` 逐条写明差在哪。"""

    run_id: str
    backend_sim: str
    backend_hw: str
    events: int
    differences: list[str] = field(default_factory=list)
    hw_frames_out: int = 0
    hw_frames_in: int = 0

    @property
    def ok(self) -> bool:
        return not self.differences

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "ok": self.ok,
            "events": self.events,
            "backends": {"sim": self.backend_sim, "hw": self.backend_hw},
            "hw_frames": {"out": self.hw_frames_out, "in": self.hw_frames_in},
            "differences": list(self.differences),
        }

    def lines(self) -> list[str]:
        """给人看的几行结论（CLI 直接用）。"""
        head = (
            f"parity {self.run_id}：{'一致' if self.ok else '不一致'}"
            f"（事件 {self.events} 条，hw 帧 收 {self.hw_frames_in} / 发 {self.hw_frames_out}）"
        )
        return [head] + [f"  ✗ {item}" for item in self.differences]


def _detail_equal(sim_detail: dict[str, Any], hw_detail: dict[str, Any]) -> bool:
    """``detail`` 比较：起步 ``log`` 的 ``backend`` 各家写各家的名字，其余必须逐字段相等。"""
    left = dict(sim_detail)
    right = dict(hw_detail)
    if "backend" in left or "backend" in right:
        left.pop("backend", None)
        right.pop("backend", None)
    return left == right


def compare_runs(sim_run: SimRun, hw_run: HwRun) -> ParityReport:
    """比较一次 sim 运行与一次 hw 运行；返回 :class:`ParityReport`（不抛异常）。"""
    report = ParityReport(
        run_id=sim_run.run_id,
        backend_sim=str(sim_run.result.get("backend")),
        backend_hw=str(hw_run.result.get("backend")),
        events=len(sim_run.events),
        hw_frames_out=int(hw_run.link.get("frames_out", 0)),
        hw_frames_in=int(hw_run.link.get("frames_in", 0)),
    )
    diffs = report.differences

    if report.backend_sim != BACKEND_SIM:
        diffs.append(f"sim 侧 backend 应为 {BACKEND_SIM!r}，实际 {report.backend_sim!r}")
    if report.backend_hw != BACKEND_HW:
        diffs.append(f"hw 侧 backend 应为 {BACKEND_HW!r}，实际 {report.backend_hw!r}")
    if report.hw_frames_out == 0:
        diffs.append("hw 侧一帧命令都没发出去：设备没有真的参与这次运行")

    if len(sim_run.events) != len(hw_run.events):
        diffs.append(f"事件条数不同：sim {len(sim_run.events)} / hw {len(hw_run.events)}")
    for position, (sim_event, hw_event) in enumerate(zip(sim_run.events, hw_run.events)):
        where = f"events[{position}]"
        if sim_event["seq"] != hw_event["seq"]:
            diffs.append(f"{where}: seq {sim_event['seq']} / {hw_event['seq']}")
        if sim_event["phase"] != hw_event["phase"]:
            diffs.append(f"{where}: phase {sim_event['phase']!r} / {hw_event['phase']!r}")
        if sim_event["step_id"] != hw_event["step_id"]:
            diffs.append(f"{where}: step_id {sim_event['step_id']!r} / {hw_event['step_id']!r}")
        if float(sim_event["at_ms"]) != float(hw_event["at_ms"]):
            diffs.append(f"{where}: at_ms {sim_event['at_ms']} / {hw_event['at_ms']}")
        if not _detail_equal(sim_event["detail"], hw_event["detail"]):
            diffs.append(f"{where}: detail 不同\n    sim={sim_event['detail']}\n    hw ={hw_event['detail']}")

    for key in ("status", "steps_executed", "duration_ms"):
        if sim_run.result[key] != hw_run.result[key]:
            diffs.append(f"ExecResult.{key}：sim {sim_run.result[key]!r} / hw {hw_run.result[key]!r}")
    sim_codes = [str(entry.get("code")) for entry in sim_run.result["diagnostics"]]
    hw_codes = [str(entry.get("code")) for entry in hw_run.result["diagnostics"]]
    if sim_codes != hw_codes:
        diffs.append(f"ExecResult 诊断码：sim {sim_codes} / hw {hw_codes}")

    for key in ("status", "steps_executed", "duration_ms", "limits", "world", "events", "last_seq"):
        if sim_run.state[key] != hw_run.state[key]:
            diffs.append(f"ExecState.{key}：sim {sim_run.state[key]!r} / hw {hw_run.state[key]!r}")
    if sim_run.state["backend"] != BACKEND_SIM:
        diffs.append(f"ExecState.backend（sim）应为 {BACKEND_SIM!r}")
    if hw_run.state["backend"] != BACKEND_HW:
        diffs.append(f"ExecState.backend（hw）应为 {BACKEND_HW!r}")
    return report


__all__ = ["ParityReport", "compare_runs"]
