"""S6 ``ExecEvent`` / ``ExecResult``（`docs/PROTOCOL.md` §7）与 ``ExecState`` 的数据模型、校验与序列化。

§7 的两条约定在这里落地：

- **``seq`` 严格递增、``at_ms`` 相对运行起点**：``seq`` 从 1 开始按产出顺序 +1，``at_ms`` 是虚拟
  时钟的毫秒读数（单调不减，见 :mod:`liveblocks.runtime.semantics`）。
- **``backend`` 只有 ``sim``**：本层是模拟后端，事件里的 ``backend`` 一律 ``sim``；协议 §7.1 明确
  「任何来自模拟器的事件都必须标 ``sim``，禁止以模拟结果冒充硬件验证」。

三份文档的形状：

1. ``ExecEvent``（§7.1）—— 原样照抄协议字段，**不加字段**；
2. ``ExecResult``（§7.2）—— 原样照抄协议字段，**不加字段**；
3. ``ExecState`` —— 本层**加法式**新增的可读状态文档（§7 没有「最终状态」对象；它等价于
   「一次运行的收尾快照」，把 ``limits``、世界状态与事件计数放在一起，便于人直接读）。
   新 ``kind`` 属于协议缺口，已记进 ``docs/EXEC.md`` §10 与 ``docs/DECISIONS.md`` D-018。

序列化沿用其它层的做法：``json.dumps(..., ensure_ascii=False, indent=2)`` + 结尾一个换行，
键序由构造顺序写死，因此「同一 IR 跑两次 → 字节一致」可以逐字节断言。
"""

from __future__ import annotations

import json
from typing import Any

from ..ir.ops import LIMIT_KEYS, LIMIT_MINIMUM
from ..vision.protocol import SCHEMA_VERSION, VERSION_PATTERN, validate_diagnostics
from .semantics import (
    BACKENDS,
    DIAGNOSTICS_BY_CODE,
    EXEC_VERSION,
    FACINGS,
    PHASES,
    RESULT_STATUSES,
    STATUS_CODES,
)

#: ``kind`` 取值。
KIND_EXEC_EVENT = "ExecEvent"
KIND_EXEC_RESULT = "ExecResult"
KIND_EXEC_STATE = "ExecState"

#: §0 的「未知枚举值降级为 ``unknown``」：校验器同时接受 ``unknown``。
UNKNOWN = "unknown"


def canonical_text(document: dict[str, Any]) -> str:
    """把文档序列化成**可逐字节比对**的文本（固定缩进、不排序键、非 ASCII 原样、结尾一个换行）。"""
    return json.dumps(document, ensure_ascii=False, indent=2) + "\n"


def events_jsonl(events: tuple[dict[str, Any], ...] | list[dict[str, Any]]) -> str:
    """事件流按 §8 的写法落成 JSONL（一行一个事件，紧凑分隔符，结尾一个换行）。"""
    return "".join(json.dumps(event, ensure_ascii=False) + "\n" for event in events)


def exec_event(
    *,
    run_id: str,
    seq: int,
    at_ms: float,
    step_id: str,
    phase: str,
    detail: dict[str, Any] | None = None,
    backend: str = "sim",
    schema_version: str = SCHEMA_VERSION,
) -> dict[str, Any]:
    """按 §7.1 构造一个 ``ExecEvent``（字段顺序 = 协议的示例顺序）。"""
    return {
        "schema_version": schema_version,
        "kind": KIND_EXEC_EVENT,
        "run_id": run_id,
        "seq": int(seq),
        "at_ms": round(float(at_ms), 6),
        "step_id": step_id,
        "phase": phase,
        "backend": backend,
        "detail": {} if detail is None else dict(detail),
    }


def exec_result(
    *,
    run_id: str,
    status: str,
    steps_executed: int,
    duration_ms: float,
    diagnostics: list[dict[str, Any]] | tuple[dict[str, Any], ...] = (),
    backend: str = "sim",
    schema_version: str = SCHEMA_VERSION,
) -> dict[str, Any]:
    """按 §7.2 构造一个 ``ExecResult``（字段顺序 = 协议的示例顺序）。"""
    return {
        "schema_version": schema_version,
        "kind": KIND_EXEC_RESULT,
        "run_id": run_id,
        "status": status,
        "steps_executed": int(steps_executed),
        "duration_ms": round(float(duration_ms), 6),
        "backend": backend,
        "diagnostics": [dict(entry) for entry in diagnostics],
    }


def exec_state(
    *,
    run_id: str,
    status: str,
    steps_executed: int,
    duration_ms: float,
    limits: dict[str, int],
    world: dict[str, Any],
    events: int,
    last_seq: int,
    backend: str = "sim",
    schema_version: str = SCHEMA_VERSION,
    exec_version: str | None = None,
) -> dict[str, Any]:
    """构造 ``ExecState``：一次运行的最终状态（可读）。

    ``limits`` 原样回显 ``Ir.limits``，这样「限制来自 IR」在输出里是可见事实，而不是口头承诺。
    """
    document: dict[str, Any] = {
        "schema_version": schema_version,
        "kind": KIND_EXEC_STATE,
        "exec_version": EXEC_VERSION if exec_version is None else exec_version,
        "run_id": run_id,
        "backend": backend,
        "status": status,
        "steps_executed": int(steps_executed),
        "duration_ms": round(float(duration_ms), 6),
        "limits": {key: int(limits[key]) for key in LIMIT_KEYS},
        "world": {
            "pos": {"x": int(world["x"]), "y": int(world["y"])},
            "facing": world["facing"],
            "holding": int(world["holding"]),
            "jumps": int(world["jumps"]),
        },
        "events": int(events),
        "last_seq": int(last_seq),
    }
    return document


def _check_common(document: Any, kind: str) -> None:
    if not isinstance(document, dict):
        raise ValueError(f"{kind} 必须是对象")
    if document.get("kind") != kind:
        raise ValueError(f"kind 必须是 {kind!r}，实际 {document.get('kind')!r}")
    version = document.get("schema_version")
    if not isinstance(version, str) or VERSION_PATTERN.fullmatch(version) is None:
        raise ValueError(f"schema_version 必须是 'major.minor[.patch]' 字符串，实际 {version!r}")
    run_id = document.get("run_id")
    if not isinstance(run_id, str) or not run_id:
        raise ValueError(f"run_id 必须是非空字符串，实际 {run_id!r}")
    backend = document.get("backend")
    if backend not in BACKENDS and backend != UNKNOWN:
        raise ValueError(f"backend 必须属于 {list(BACKENDS)}，实际 {backend!r}")


def _check_number(value: Any, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{where} 必须是数字，实际 {value!r}")
    if value < 0:
        raise ValueError(f"{where} 不能为负，实际 {value!r}")
    return float(value)


def validate_exec_event(document: dict[str, Any]) -> None:
    """校验 ``ExecEvent``（§7.1）：形状 + 枚举 + ``seq`` / ``at_ms`` 的类型与下界。"""
    _check_common(document, KIND_EXEC_EVENT)
    seq = document.get("seq")
    if not isinstance(seq, int) or isinstance(seq, bool):
        raise ValueError(f"seq 必须是整数，实际 {seq!r}")
    if seq < 1:
        raise ValueError(f"seq 从 1 开始递增，实际 {seq!r}")
    _check_number(document.get("at_ms"), "at_ms")
    step_id = document.get("step_id")
    if not isinstance(step_id, str):
        raise ValueError(f"step_id 必须是字符串（不绑定步骤时用空字符串），实际 {step_id!r}")
    phase = document.get("phase")
    if phase not in PHASES and phase != UNKNOWN:
        raise ValueError(f"phase 必须属于 {list(PHASES)}，实际 {phase!r}")
    if not isinstance(document.get("detail"), dict):
        raise ValueError("detail 必须是对象（§7.1 示例里是 {state_delta: …}）")


def validate_exec_result(document: dict[str, Any]) -> None:
    """校验 ``ExecResult``（§7.2）：形状 + ``status`` 枚举 + 计数/时长下界 + 诊断形状与码区间。"""
    _check_common(document, KIND_EXEC_RESULT)
    status = document.get("status")
    if status not in RESULT_STATUSES and status != UNKNOWN:
        raise ValueError(f"status 必须属于 {list(RESULT_STATUSES)}，实际 {status!r}")
    steps = document.get("steps_executed")
    if not isinstance(steps, int) or isinstance(steps, bool) or steps < 0:
        raise ValueError(f"steps_executed 必须是非负整数，实际 {steps!r}")
    _check_number(document.get("duration_ms"), "duration_ms")
    diagnostics = document.get("diagnostics")
    validate_diagnostics(diagnostics)
    for position, entry in enumerate(diagnostics):
        code = str(entry.get("code", ""))
        if code not in DIAGNOSTICS_BY_CODE:
            raise ValueError(
                f"diagnostics[{position}]: 诊断码 {code!r} 不在执行区间 {list(DIAGNOSTICS_BY_CODE)} 里"
            )
    expected = STATUS_CODES.get(str(status), ())
    if expected:
        codes = [str(entry.get("code", "")) for entry in diagnostics]
        if not any(code in codes for code in expected):
            raise ValueError(f"status={status} 必须带一条 {list(expected)} 诊断，实际 {codes}")


def validate_exec_state(document: dict[str, Any]) -> None:
    """校验 ``ExecState``（本层加法式文档）：状态、limits、世界状态与事件计数。"""
    _check_common(document, KIND_EXEC_STATE)
    version = document.get("exec_version")
    if not isinstance(version, str) or VERSION_PATTERN.fullmatch(version) is None:
        raise ValueError(f"exec_version 必须是 'major.minor[.patch]' 字符串，实际 {version!r}")
    status = document.get("status")
    if status not in RESULT_STATUSES and status != UNKNOWN:
        raise ValueError(f"status 必须属于 {list(RESULT_STATUSES)}，实际 {status!r}")
    steps = document.get("steps_executed")
    if not isinstance(steps, int) or isinstance(steps, bool) or steps < 0:
        raise ValueError(f"steps_executed 必须是非负整数，实际 {steps!r}")
    _check_number(document.get("duration_ms"), "duration_ms")
    limits = document.get("limits")
    if not isinstance(limits, dict):
        raise ValueError("limits 必须是对象（回显 Ir.limits）")
    for key in LIMIT_KEYS:
        value = limits.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value < LIMIT_MINIMUM:
            raise ValueError(f"limits.{key} 必须是 ≥ {LIMIT_MINIMUM} 的整数，实际 {value!r}")
    world = document.get("world")
    if not isinstance(world, dict):
        raise ValueError("world 必须是对象")
    pos = world.get("pos")
    if not isinstance(pos, dict):
        raise ValueError("world.pos 必须是对象")
    for axis in ("x", "y"):
        value = pos.get(axis)
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError(f"world.pos.{axis} 必须是整数，实际 {value!r}")
    if world.get("facing") not in FACINGS:
        raise ValueError(f"world.facing 必须属于 {list(FACINGS)}，实际 {world.get('facing')!r}")
    for key in ("holding", "jumps"):
        value = world.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(f"world.{key} 必须是非负整数，实际 {value!r}")
    events = document.get("events")
    if not isinstance(events, int) or isinstance(events, bool) or events < 0:
        raise ValueError(f"events 必须是事件条数（非负整数），实际 {events!r}")
    last_seq = document.get("last_seq")
    if not isinstance(last_seq, int) or isinstance(last_seq, bool) or last_seq < 0:
        raise ValueError(f"last_seq 必须是非负整数，实际 {last_seq!r}")
    if events and last_seq < events:
        raise ValueError("last_seq 不能小于事件条数（seq 从 1 开始且严格递增）")


def validate_event_stream(events: list[dict[str, Any]] | tuple[dict[str, Any], ...]) -> None:
    """校验一条事件流的跨事件约束：``seq`` 严格递增、``at_ms`` 单调不减、``backend`` 一致。"""
    if not isinstance(events, (list, tuple)):
        raise ValueError("事件流必须是数组")
    previous_seq = 0
    previous_at = 0.0
    backend: str | None = None
    for position, event in enumerate(events):
        validate_exec_event(event)
        if event["seq"] <= previous_seq:
            raise ValueError(
                f"events[{position}]: seq 必须严格递增，上一事件 {previous_seq}，本事件 {event['seq']}"
            )
        if event["at_ms"] < previous_at:
            raise ValueError(
                f"events[{position}]: at_ms 必须单调不减，上一事件 {previous_at}，本事件 {event['at_ms']}"
            )
        if backend is None:
            backend = event["backend"]
        elif event["backend"] != backend:
            raise ValueError(f"events[{position}]: backend 与事件流不一致（{backend} vs {event['backend']}）")
        previous_seq = event["seq"]
        previous_at = event["at_ms"]


__all__ = [
    "KIND_EXEC_EVENT",
    "KIND_EXEC_RESULT",
    "KIND_EXEC_STATE",
    "UNKNOWN",
    "canonical_text",
    "events_jsonl",
    "exec_event",
    "exec_result",
    "exec_state",
    "validate_event_stream",
    "validate_exec_event",
    "validate_exec_result",
    "validate_exec_state",
]
