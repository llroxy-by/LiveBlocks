"""回放引擎：把一份运行记录展开成「可单步的时间线」。

回放的含义（``docs/UI.md`` §3）——**只从记录重算，不重跑业务层**：

- 时间线的第 0 帧是「还没处理任何事件」，第 k 帧是「处理完前 k 条 ``ExecEvent``」；
- 每帧的世界状态由事件 ``detail.state_delta`` 依次折叠得到（§7.1 的加法式字段），
  折叠规则固定：``pos`` 与其它键按「后者覆盖前者」结算，不做插值、不做推断；
- 每帧还记录「当前是哪一步 ``step_id``」「累计虚拟耗时 ``at_ms``」「哪些步已经走完」。

确定性：``Replay`` 的全部字段只依赖 ``RunRecord`` 的字段（不含时间、不含哈希随机序），
因此**同一记录回放两次逐字段相等**——这正是 Issue #20 第二条验收。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .protocol import format_ms
from .record import RunRecord

#: 折叠状态时的初始世界（P0 占位语义，与 ``docs/EXEC.md`` 的 ``ExecState.world`` 一致）。
BASE_WORLD: dict[str, Any] = {
    "pos": {"x": 0, "y": 0},
    "facing": "north",
    "holding": 0,
    "jumps": 0,
}


def _copy_world(world: dict[str, Any]) -> dict[str, Any]:
    return {
        "pos": dict(world.get("pos") or {"x": 0, "y": 0}),
        "facing": world.get("facing", "north"),
        "holding": world.get("holding", 0),
        "jumps": world.get("jumps", 0),
    }


def fold_world(world: dict[str, Any], delta: Any) -> dict[str, Any]:
    """把一条事件的 ``state_delta`` 折进世界状态；形状不认识就原样返回。"""
    result = _copy_world(world)
    if not isinstance(delta, dict):
        return result
    pos = delta.get("pos")
    if isinstance(pos, dict):
        merged = dict(result["pos"])
        for key in ("x", "y"):
            value = pos.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                merged[key] = value
        result["pos"] = merged
    for key in ("facing", "holding", "jumps"):
        if key in delta:
            value = delta[key]
            if key == "facing":
                if isinstance(value, str) and value:
                    result["facing"] = value
            elif isinstance(value, (int, float)) and not isinstance(value, bool):
                result[key] = value
    return result


@dataclass(frozen=True)
class StepState:
    """回放时间线上的一帧。``index`` 是「已处理的事件条数」。"""

    index: int
    event: dict[str, Any] | None
    world: dict[str, Any]
    at_ms: float
    current_step_id: str
    steps_done: tuple[str, ...]

    @property
    def world_text(self) -> str:
        pos = self.world.get("pos") or {}
        return (
            f"pos=({pos.get('x', '?')}, {pos.get('y', '?')}) "
            f"facing={self.world.get('facing', '?')} "
            f"holding={self.world.get('holding', '?')} jumps={self.world.get('jumps', '?')}"
        )

    def describe(self) -> str:
        head = "未处理任何事件" if self.event is None else (
            f"seq={self.event.get('seq')} phase={self.event.get('phase')} "
            f"step={self.event.get('step_id') or '-'} at_ms={format_ms(self.at_ms)}"
        )
        return f"[{self.index}] {head} | {self.world_text}"


@dataclass
class Replay:
    """一份记录的时间线 + 收尾结论。"""

    record: RunRecord
    states: tuple[StepState, ...] = ()
    #: ``state_delta`` 缺失的 ``finish`` 事件条数（>0 时界面上标注「世界状态不完整」）。
    partial_world: int = 0
    #: 记录里的最终状态（``ExecResult`` 的收尾结论）；缺失时为 ``None``。
    result: dict[str, Any] | None = None
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def total_steps(self) -> int:
        """单步的最大下标（= 事件条数）。"""
        return max(0, len(self.states) - 1)

    def clamp(self, step: int | None) -> int:
        if step is None:
            return self.total_steps
        if step < 0:
            return 0
        return min(int(step), self.total_steps)

    def state_at(self, step: int | None) -> StepState:
        return self.states[self.clamp(step)]

    @property
    def final_state(self) -> StepState:
        return self.states[-1]

    def signature(self) -> str:
        """回放结果的可比指纹（确定性测试与 ``ui replay`` 用）。"""
        lines = [
            f"run_id={self.record.run_id} backend={self.record.backend} "
            f"events={len(self.record.events)} partial_world={self.partial_world}",
        ]
        for state in self.states:
            lines.append(state.describe())
        if self.result is not None:
            lines.append(
                f"result status={self.result.get('status')} "
                f"steps={self.result.get('steps_executed')} "
                f"duration_ms={format_ms(float(self.result.get('duration_ms') or 0.0))}"
            )
        return "\n".join(lines)


def build_replay(record: RunRecord) -> Replay:
    """构造回放时间线。任何字段缺失都降级为提示，不抛异常。"""
    world = _copy_world(BASE_WORLD)
    states: list[StepState] = [
        StepState(
            index=0,
            event=None,
            world=_copy_world(world),
            at_ms=0.0,
            current_step_id="",
            steps_done=(),
        )
    ]
    steps_done: list[str] = []
    partial = 0
    for position, event in enumerate(record.events, start=1):
        detail = event.get("detail") if isinstance(event.get("detail"), dict) else {}
        delta = detail.get("state_delta")
        if event.get("phase") == "finish" and not isinstance(delta, dict):
            partial += 1
        world = fold_world(world, delta)
        step_id = str(event.get("step_id") or "")
        if event.get("phase") == "finish" and step_id and step_id not in steps_done:
            steps_done.append(step_id)
        states.append(
            StepState(
                index=position,
                event=event,
                world=_copy_world(world),
                at_ms=float(event.get("at_ms") or 0.0),
                current_step_id=step_id,
                steps_done=tuple(steps_done),
            )
        )
    notes: list[str] = []
    if partial:
        notes.append(
            f"{partial} 条 finish 事件没有 state_delta：世界状态是「按已知增量折叠」的结果，不是全量状态"
        )
    if not record.events:
        notes.append("记录里没有 ExecEvent：单步只显示初始世界与收尾结论")
    return Replay(
        record=record,
        states=tuple(states),
        partial_world=partial,
        result=record.result or None,
        notes=tuple(notes),
    )


__all__ = ["BASE_WORLD", "StepState", "Replay", "build_replay", "fold_world"]
