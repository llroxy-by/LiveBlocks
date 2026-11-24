"""回放（``liveblocks.ui.replay``）与「两次回放结果一致」的确定性测试。

对应 Issue #20 第二条验收：**同一份记录回放两次结果一致**。这里从三个层面钉住它：

1. 解析 + 回放在同进程里跑两次，``RunRecord`` / ``Replay`` 逐字段相等；
2. 渲染产物（HTML / 文本 / 叠加图）在同进程里逐字节相等；
3. 换 ``PYTHONHASHSEED`` 起子进程再渲染，stdout 的哈希仍然一致（排除哈希随机序的影响）。
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from liveblocks.ui.record import parse_record_file, parse_record_text
from liveblocks.ui.render import build_view, build_view_from_file
from liveblocks.ui.replay import BASE_WORLD, build_replay, fold_world

REPO_ROOT = Path(__file__).resolve().parents[1]
RUN_DIR = REPO_ROOT / "fixtures" / "runs"
ALL_RECORDS = tuple(sorted(RUN_DIR.glob("*.jsonl"))) + tuple(sorted((RUN_DIR / "corrupt").glob("*.jsonl")))


def _sha(data: str | bytes) -> str:
    return hashlib.sha256(data.encode("utf-8") if isinstance(data, str) else data).hexdigest()


# --- 世界状态折叠 --------------------------------------------------------------


def test_fold_world_only_applies_known_deltas() -> None:
    world = fold_world(BASE_WORLD, {"pos": {"x": 3, "y": -1}, "facing": "east", "jumps": 2})
    assert world == {"pos": {"x": 3, "y": -1}, "facing": "east", "holding": 0, "jumps": 2}


def test_fold_world_ignores_unknown_shapes() -> None:
    assert fold_world(BASE_WORLD, "not a dict") == BASE_WORLD
    assert fold_world(BASE_WORLD, {"pos": {"x": "many"}}) == BASE_WORLD
    assert fold_world(BASE_WORLD, {"facing": 12}) == BASE_WORLD
    assert fold_world(BASE_WORLD, {"holding": True}) == BASE_WORLD


def test_fold_world_does_not_mutate_its_input() -> None:
    world = {"pos": {"x": 1, "y": 2}, "facing": "north", "holding": 0, "jumps": 0}
    folded = fold_world(world, {"pos": {"x": 9}})
    assert world["pos"]["x"] == 1
    assert folded["pos"]["x"] == 9


# --- 时间线形状 ---------------------------------------------------------------


def test_timeline_has_one_state_per_event_plus_the_initial_one() -> None:
    record = parse_record_file(RUN_DIR / "sample-all-ops.jsonl")
    replay = build_replay(record)
    assert len(replay.states) == len(record.events) + 1
    assert replay.total_steps == len(record.events)
    assert replay.states[0].event is None
    assert replay.states[0].index == 0
    assert [state.index for state in replay.states] == list(range(len(record.events) + 1))


def test_steps_done_grows_with_finish_events() -> None:
    replay = build_replay(parse_record_file(RUN_DIR / "sample-all-ops.jsonl"))
    final = replay.final_state
    assert len(final.steps_done) == 8
    assert final.steps_done[0] == "s0"
    assert final.steps_done == tuple(f"s{index}" for index in range(8))
    assert replay.final_state.world["jumps"] == 1


def test_world_state_is_folded_event_by_event() -> None:
    replay = build_replay(parse_record_file(RUN_DIR / "sample-clipped.jsonl"))
    assert replay.states[0].world == BASE_WORLD
    final = replay.final_state
    assert final.world["facing"] == "west"  # turn-right 3 个 90° → 朝西
    assert final.at_ms == 10.0


def test_step_is_clamped_instead_of_raising() -> None:
    replay = build_replay(parse_record_file(RUN_DIR / "sample-clipped.jsonl"))
    assert replay.clamp(None) == replay.total_steps
    assert replay.clamp(-99) == 0
    assert replay.clamp(10**6) == replay.total_steps
    assert replay.state_at(-1).index == 0


def test_missing_state_delta_is_flagged() -> None:
    text = "\n".join(
        [
            json.dumps({"kind": "RunHeader", "run_id": "run-p", "backend": "sim"}),
            json.dumps(
                {
                    "kind": "ExecEvent",
                    "seq": 1,
                    "at_ms": 0.0,
                    "step_id": "s0",
                    "phase": "finish",
                    "detail": {"note": "no delta here"},
                }
            ),
        ]
    )
    replay = build_replay(parse_record_text(text))
    assert replay.partial_world == 1
    assert any("state_delta" in note for note in replay.notes)


def test_empty_record_still_has_one_frame() -> None:
    replay = build_replay(parse_record_text(""))
    assert len(replay.states) == 1
    assert replay.total_steps == 0
    assert any("ExecEvent" in note for note in replay.notes)


# --- 两次回放一致（同进程） ----------------------------------------------------


@pytest.mark.parametrize("path", ALL_RECORDS, ids=[path.name for path in ALL_RECORDS])
def test_two_replays_render_byte_identical_artifacts(path: Path) -> None:
    first = build_view_from_file(path)
    second = build_view_from_file(path)
    assert first.render_html() == second.render_html()
    assert first.render_text() == second.render_text()
    assert first.overlay_png == second.overlay_png
    assert first.replay.signature() == second.replay.signature()
    assert first.record == second.record


@pytest.mark.parametrize("path", ALL_RECORDS, ids=[path.name for path in ALL_RECORDS])
def test_rerendering_the_same_view_is_stable(path: Path) -> None:
    view = build_view_from_file(path)
    assert _sha(view.render_html()) == _sha(view.render_html())
    assert _sha(view.render_text()) == _sha(view.render_text())


def test_record_equality_is_fieldwise() -> None:
    first = parse_record_file(RUN_DIR / "sample-clipped.jsonl")
    second = parse_record_file(RUN_DIR / "sample-clipped.jsonl")
    assert first == second
    assert first is not second


# --- 换 PYTHONHASHSEED 的子进程也必须一致 --------------------------------------

_SUBPROCESS_SNIPPET = """
import hashlib, sys
from pathlib import Path
from liveblocks.ui.render import build_view_from_file

view = build_view_from_file(Path(sys.argv[1]))
html = view.render_html().encode("utf-8")
text = view.render_text().encode("utf-8")
print(hashlib.sha256(html).hexdigest())
print(hashlib.sha256(text).hexdigest())
print(hashlib.sha256(view.overlay_png).hexdigest())
print(hashlib.sha256(view.replay.signature().encode("utf-8")).hexdigest())
"""


def test_hash_seed_does_not_change_replay_output() -> None:
    record = RUN_DIR / "sample-all-ops.jsonl"
    outputs = []
    for seed in ("0", "1", "12345"):
        env = dict(os.environ)
        env["PYTHONHASHSEED"] = seed
        env["PYTHONPATH"] = str(REPO_ROOT / "src")
        completed = subprocess.run(
            [sys.executable, "-c", _SUBPROCESS_SNIPPET, str(record)],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(REPO_ROOT),
            timeout=120,
        )
        assert completed.returncode == 0, completed.stderr
        outputs.append(completed.stdout)
    assert len(set(outputs)) == 1, outputs


def test_view_only_shows_the_record_file_name_not_the_absolute_path() -> None:
    absolute = build_view_from_file(RUN_DIR / "sample-clipped.jsonl")
    relative = build_view_from_file(Path("fixtures/runs/sample-clipped.jsonl"))
    assert absolute.record_path == relative.record_path == "sample-clipped.jsonl"
    assert absolute.render_html() == relative.render_html()


# --- 单步 ---------------------------------------------------------------------


def test_different_steps_produce_different_views() -> None:
    record = parse_record_file(RUN_DIR / "sample-all-ops.jsonl")
    step0 = build_view(record, step=0, record_path=RUN_DIR / "sample-all-ops.jsonl")
    steplast = build_view(record, step=10**6, record_path=RUN_DIR / "sample-all-ops.jsonl")
    assert step0.step == 0
    assert steplast.step == len(record.events)
    assert step0.render_html() != steplast.render_html()
    assert step0.render_text() != steplast.render_text()
    # 叠加图只画识别与排列，不随事件单步变化
    assert step0.overlay_png == steplast.overlay_png


def test_single_step_walks_the_event_stream() -> None:
    record = parse_record_file(RUN_DIR / "sample-clipped.jsonl")
    replay = build_replay(record)
    worlds = [replay.state_at(step).world_text for step in range(replay.total_steps + 1)]
    assert len(set(worlds)) >= 2  # 世界状态确实随单步变化
    assert replay.state_at(1).current_step_id == ""  # 第 1 条事件是 log
    assert replay.state_at(3).current_step_id == "s0"
