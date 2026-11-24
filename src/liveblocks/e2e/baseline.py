"""黄金样例基准：``fixtures/e2e/baseline.json`` 的生成、加载与比对。

基准只记**跨机器可比**的东西：

- 每个场景每一层的**指纹**（:func:`liveblocks.e2e.record.fingerprint`，canonical JSON + sha256）、
  该层的 ``kind`` 序列与行数；
- 生成层的**图像 / 真值 sha256**（证明「同种子 → 同字节」在整条链路的起点上也成立）；
- 每层的关键计数（标记/卡片/行/语句/步/事件/状态）；
- 固定的时间上界（:data:`liveblocks.e2e.protocol.TIME_BOUND_MS`）。

**不记**墙钟耗时（``RunHeader.timings_ms`` 与 ``wall_ms``）：那是记录时实测，换台机器必然不同。
所以基准文件本身是确定性的——用当前代码重新算一遍应当与入库文件逐字节相同
（``tests/test_e2e_regression.py::test_baseline_matches_a_fresh_run`` 就这么断言）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Sequence

from .pipeline import ChainRun
from .protocol import (
    CODE_BASELINE_MISMATCH,
    CODE_BASELINE_MISSING,
    E2E_VERSION,
    RECORD_BOUND_MS,
    SCHEMA_VERSION,
    STAGE_LABELS,
    STAGES,
    TIME_BOUND_MS,
    E2eIssue,
    issue,
)
from .record import short_hash

#: 基准文件名（相对基准目录）。
BASELINE_NAME = "baseline.json"

#: 基准文档的 ``kind``。
BASELINE_KIND = "E2eBaseline"

#: 基准文档的说明文字（写进文件，读的人不用翻文档也知道口径）。
BASELINE_NOTE = (
    "指纹 = sha256(canonical JSON(该层文档, 键排序) + 换行)；"
    "不含 RunHeader.timings_ms 与 wall_ms（记录时实测的墙钟耗时，换机器必然不同）"
)


class BaselineError(RuntimeError):
    """基准文件缺失或形状不对。"""


def baseline_document(
    runs: Sequence[ChainRun],
    *,
    seed: int,
    time_bound_ms: float = TIME_BOUND_MS,
    record_bound_ms: float = RECORD_BOUND_MS,
) -> dict[str, Any]:
    """把一批全链路运行结果压成入库基准文档（确定性、无墙钟耗时）。"""
    scenes = [_scene_entry(run) for run in sorted(runs, key=lambda item: item.scene)]
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": BASELINE_KIND,
        "e2e_version": E2E_VERSION,
        "seed": int(seed),
        "generator": {"name": "liveblocks.e2e", "package_version": SCHEMA_VERSION},
        "time_bound_ms": float(time_bound_ms),
        "record_bound_ms": float(record_bound_ms),
        "note": BASELINE_NOTE,
        "scenes": scenes,
    }


def _scene_entry(run: ChainRun) -> dict[str, Any]:
    layers: dict[str, Any] = {}
    for stage in STAGES:
        output = run.stages[stage]
        if not output.documents:
            continue
        layers[stage] = {
            "kinds": list(output.kinds),
            "lines": output.lines,
            "sha256": output.fingerprint,
        }
    return {
        "name": run.scene,
        "run_id": run.run_id,
        "image_sha256": run.header["generation"]["image_sha256"],
        "ground_truth_sha256": run.header["generation"]["ground_truth_sha256"],
        "lines": len(run.documents),
        "counts": run.counts(),
        "layers": layers,
    }


def dump_baseline(document: dict[str, Any]) -> str:
    """基准文件的口径：``indent=2``、UTF-8、末尾一个换行（便于人读与 diff）。"""
    return json.dumps(document, ensure_ascii=False, indent=2) + "\n"


def write_baseline(directory: str | Path, document: dict[str, Any]) -> Path:
    target = Path(directory) / BASELINE_NAME
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(dump_baseline(document), encoding="utf-8")
    return target


def load_baseline(directory: str | Path) -> dict[str, Any]:
    """读基准；缺文件或 ``kind`` 不对时抛 :class:`BaselineError`。"""
    path = Path(directory) / BASELINE_NAME
    if not path.is_file():
        raise BaselineError(
            f"找不到黄金基准 {path}；用 `liveblocks e2e regression --update-baseline` 生成"
        )
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise BaselineError(f"{path} 不是合法 JSON：{error}") from error
    if document.get("kind") != BASELINE_KIND:
        raise BaselineError(f"{path} 的 kind 是 {document.get('kind')!r}，这里需要 {BASELINE_KIND}")
    if not isinstance(document.get("scenes"), list) or not document["scenes"]:
        raise BaselineError(f"{path} 里没有 scenes")
    return document


def scene_names(document: dict[str, Any]) -> tuple[str, ...]:
    return tuple(str(entry.get("name") or "") for entry in document.get("scenes", []))


def scene_entry(document: dict[str, Any], scene: str) -> dict[str, Any] | None:
    for entry in document.get("scenes", []):
        if entry.get("name") == scene:
            return entry
    return None


def compare_run(
    run: ChainRun, entry: dict[str, Any] | None, *, scene: str | None = None
) -> list[E2eIssue]:
    """把一次运行与基准里的一个场景条目比对，差异逐阶段报 ``LB-E010``。

    一个阶段的多项差异合成一条问题（读起来更清楚），问题里的 ``layer`` 就是该阶段——
    因此报告可以按「阶段顺序」把最早的一条当成根因层（见
    :func:`liveblocks.e2e.regression.root_layer`）。
    """
    name = scene or run.scene
    if entry is None:
        return [
            issue(
                CODE_BASELINE_MISSING,
                f"黄金基准里没有场景 {name!r}（新增场景要先 `--update-baseline` 更新基准）",
                scene=name,
            )
        ]

    issues: list[E2eIssue] = []
    expected_run_id = entry.get("run_id")
    if expected_run_id and expected_run_id != run.run_id:
        issues.append(
            issue(
                CODE_BASELINE_MISMATCH,
                f"run_id 期望 {expected_run_id!r}，实得 {run.run_id!r}",
                scene=name,
            )
        )

    generation = run.header.get("generation") or {}
    differences: list[str] = []
    if generation.get("image_sha256") != entry.get("image_sha256"):
        differences.append(
            f"图像 sha256 期望 {short_hash(str(entry.get('image_sha256')))}，"
            f"实得 {short_hash(str(generation.get('image_sha256')))}"
        )
    if generation.get("ground_truth_sha256") != entry.get("ground_truth_sha256"):
        differences.append(
            f"真值 sha256 期望 {short_hash(str(entry.get('ground_truth_sha256')))}，"
            f"实得 {short_hash(str(generation.get('ground_truth_sha256')))}"
        )
    if differences:
        issues.append(
            issue(
                CODE_BASELINE_MISMATCH,
                f"阶段 {STAGE_LABELS['synth']} 与基准不同：" + "；".join(differences),
                layer="synth",
                scene=name,
            )
        )

    expected_layers = entry.get("layers") or {}
    expected_counts = entry.get("counts") or {}
    for stage in STAGES[1:]:
        output = run.stages[stage]
        expected = expected_layers.get(stage)
        differences = []
        if not isinstance(expected, dict):
            differences.append("基准里没有这一层")
        else:
            if list(output.kinds) != list(expected.get("kinds") or []):
                differences.append(
                    f"kind 序列期望 {list(expected.get('kinds') or [])}，实得 {list(output.kinds)}"
                )
            if output.lines != expected.get("lines"):
                differences.append(f"行数期望 {expected.get('lines')}，实得 {output.lines}")
            if output.fingerprint != expected.get("sha256"):
                differences.append(
                    f"sha256 期望 {short_hash(str(expected.get('sha256')))}，"
                    f"实得 {short_hash(output.fingerprint)}"
                )
        expected_count = expected_counts.get(stage)
        actual_count = output.counts
        if isinstance(expected_count, dict):
            for key in sorted(expected_count):
                if expected_count[key] != actual_count.get(key):
                    differences.append(
                        f"计数 {key} 期望 {expected_count[key]!r}，实得 {actual_count.get(key)!r}"
                    )
        if differences:
            issues.append(
                issue(
                    CODE_BASELINE_MISMATCH,
                    f"阶段 {STAGE_LABELS[stage]} 与基准不同：" + "；".join(differences),
                    layer=stage,
                    scene=name,
                )
            )

    expected_lines = entry.get("lines")
    if isinstance(expected_lines, int) and expected_lines != len(run.documents):
        issues.append(
            issue(
                CODE_BASELINE_MISMATCH,
                f"记录行数期望 {expected_lines}，实得 {len(run.documents)}",
                scene=name,
            )
        )
    return issues


__all__ = [
    "BASELINE_NAME",
    "BASELINE_KIND",
    "BASELINE_NOTE",
    "BaselineError",
    "baseline_document",
    "dump_baseline",
    "write_baseline",
    "load_baseline",
    "scene_names",
    "scene_entry",
    "compare_run",
]
