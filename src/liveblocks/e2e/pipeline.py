"""全链路编排：生成 → 识别 → 排列 → 解析 → IR → 模拟执行。

一条 :func:`run_chain` 就是 LB-009 要求的那条命令的全部内容（CLI 只是参数外壳）：

1. **生成**（LB-002）：按场景预设 + 固定种子真生成一张合成卡片图与真值，**写到磁盘**——
   识别读的是这个文件，所以记录里的 ``frame.image_ref`` 指得到真文件，LB-008 的调试视图
   不需要额外配置就能画出叠加图；
2. **识别**（LB-003）：``recognize_path`` → ``MarkerCandidateList`` + ``CardCandidateList``；
3. **排列**（LB-004）：``layout_from_card_list`` → ``Layout``；
4. **解析**（LB-005）：``parse_layout_document`` → ``Ast``；
5. **IR**（LB-006）：``normalize_document`` → ``Ir``；
6. **模拟执行**（LB-007）：``SimExecutor`` → ``ExecEvent``* + ``ExecResult``。

每一步的产物都是该层**自己的** ``to_dict()``，本模块不重写、不修补字段；每步耗时用
``time.perf_counter()`` 实测后写进 ``RunHeader.timings_ms``（只作记录，不是性能结论）。

本模块**不依赖** ``scripts/`` 下的任何脚本（LB-008 的 ``build_run_records.py`` 只为运行记录
夹具服务）：全链路记录的生产在包内完成，``--check`` 那套拼记录的做法在这里不再有位置。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .. import __version__
from ..ir import normalize_document
from ..layout import layout_from_card_list
from ..parser import parse_layout_document
from ..runtime import SimExecutor, ir_from_document
from ..synth import GeneratedScene, SynthContext, generate_scene, write_scene
from ..vision import recognize_path
from .protocol import (
    DEFAULT_SEED,
    E2E_VERSION,
    KIND_AST,
    KIND_CARD,
    KIND_EXEC_EVENT,
    KIND_IR,
    KIND_LAYOUT,
    KIND_RUN_HEADER,
    SCHEMA_VERSION,
    STAGES,
)
from .record import fingerprint, record_text, sha256_bytes, sha256_text, write_record

#: 生成层的图像与真值在本记录目录下的子目录名。
SCENE_DIR_NAME = "scenes"

#: 阶段 → 被破坏时删掉哪个字段（见 :func:`_corrupt_stage_documents`）。
LAYER_CORRUPTIONS = {
    "vision": (KIND_CARD, "cards"),
    "layout": (KIND_LAYOUT, "rows"),
    "parser": (KIND_AST, "root"),
    "ir": (KIND_IR, "program"),
    "exec": (KIND_EXEC_EVENT, "seq"),
}


class ChainError(RuntimeError):
    """链路跑不动时（缺场景、写盘失败等）抛这个；记录不合格不在这里判。"""


@dataclass(frozen=True)
class StageOutput:
    """一个阶段的产物：记录里的文档 + 实测耗时 + 可读计数。"""

    stage: str
    documents: tuple[dict[str, Any], ...]
    timings_ms: float
    counts: dict[str, Any]

    @property
    def kinds(self) -> tuple[str, ...]:
        return tuple(document.get("kind", "") for document in self.documents)

    @property
    def lines(self) -> int:
        return len(self.documents)

    @property
    def fingerprint(self) -> str:
        return fingerprint(self.documents)

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "kinds": list(self.kinds),
            "lines": self.lines,
            "timings_ms": self.timings_ms,
            "counts": dict(self.counts),
        }


@dataclass
class ChainRun:
    """一次全链路运行的完整结果（含已落盘的记录路径）。"""

    run_id: str
    scene: str
    seed: int
    corrupt: str
    image_path: Path
    ground_truth_path: Path
    header: dict[str, Any]
    stages: dict[str, StageOutput]
    wall_ms: float
    record_path: Path | None = None
    notes: tuple[str, ...] = field(default_factory=tuple)

    # --- 记录视图 ---------------------------------------------------------

    @property
    def body(self) -> tuple[dict[str, Any], ...]:
        """按 §8 产出顺序拼起来的层文档（不含 ``RunHeader``）。"""
        documents: list[dict[str, Any]] = []
        for stage in STAGES:
            documents.extend(self.stages[stage].documents)
        return tuple(documents)

    @property
    def documents(self) -> list[dict[str, Any]]:
        """整份记录：``[RunHeader, *body]``。"""
        return [self.header, *self.body]

    def text(self) -> str:
        return record_text(self.documents)

    def write(self, path: str | Path) -> Path:
        """把整份记录写到 ``path``，并记住落盘位置（``record_path``）。"""
        self.record_path = write_record(path, self.documents)
        return self.record_path

    # --- 基准视图 ---------------------------------------------------------

    def fingerprints(self) -> dict[str, str]:
        return {stage: self.stages[stage].fingerprint for stage in STAGES if self.stages[stage].documents}

    def counts(self) -> dict[str, dict[str, Any]]:
        return {stage: dict(self.stages[stage].counts) for stage in STAGES}

    @property
    def timings(self) -> dict[str, float]:
        return dict(self.header.get("timings_ms") or {})

    @property
    def kinds(self) -> tuple[str, ...]:
        return tuple(kind for stage in STAGES for kind in self.stages[stage].kinds)

    def summary(self) -> str:
        counts = self.counts()
        return (
            f"{self.scene}: 标记 {counts['vision'].get('markers', 0)} / "
            f"卡片 {counts['vision'].get('cards', 0)} / "
            f"行 {counts['layout'].get('rows', 0)} / "
            f"语句 {counts['parser'].get('statements', 0)} / "
            f"步 {counts['ir'].get('steps', 0)} / "
            f"事件 {counts['exec'].get('events', 0)} / "
            f"{counts['exec'].get('status', '?')}"
        )


def _ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000.0, 3)


def run_chain(
    scene: str,
    *,
    out_dir: str | Path,
    seed: int = DEFAULT_SEED,
    run_id: str | None = None,
    corrupt: str = "none",
    context: SynthContext | None = None,
    write: bool = True,
) -> ChainRun:
    """跑一次全链路并（默认）把 §8 运行记录写到 ``out_dir/<scene>.jsonl``。

    ``corrupt`` 是**只用于自检**的破坏注入：``scene-image`` 把生成出来的图像整幅抹成中灰，
    阶段名则删掉该阶段第一个层对象的一个必填字段。回归据此证明「破坏会被抓到、且点名到层」。
    """
    ctx = context or SynthContext.load()
    if scene not in ctx.presets:
        raise ChainError(f"未知场景：{scene}（可用：{', '.join(sorted(ctx.presets))}）")
    if corrupt not in ("none", "scene-image", *LAYER_CORRUPTIONS):
        raise ChainError(f"未知的破坏目标：{corrupt}")

    target_dir = Path(out_dir)
    scene_dir = target_dir / SCENE_DIR_NAME
    started_all = time.perf_counter()
    stages: dict[str, StageOutput] = {}
    notes: list[str] = []

    # --- 1. 生成 ---------------------------------------------------------
    started = time.perf_counter()
    generated: GeneratedScene = generate_scene(scene, seed=seed, context=ctx)
    image_path, truth_path = write_scene(scene_dir, generated)
    if corrupt == "scene-image":
        _corrupt_scene_image(image_path, generated)
        notes.append("破坏注入：生成层图像被整幅抹成中灰（corrupt=scene-image）")
    image_bytes = image_path.read_bytes()
    truth_text = truth_path.read_text(encoding="utf-8")
    stages["synth"] = StageOutput(
        stage="synth",
        documents=(),
        timings_ms=_ms(started),
        counts={
            "cards_expected": len(generated.ground_truth.get("cards", [])),
            "width": generated.width,
            "height": generated.height,
            "image_bytes": len(image_bytes),
        },
    )

    # --- 2. 识别 ---------------------------------------------------------
    started = time.perf_counter()
    recognition = recognize_path(
        image_path, frame_id=f"f-e2e-{scene}", source="synthetic"
    )
    markers_doc = recognition.markers.to_dict()
    cards_doc = recognition.cards.to_dict()
    stages["vision"] = _stage(
        "vision",
        [markers_doc, cards_doc],
        started,
        {
            "markers": len(markers_doc.get("markers", [])),
            "cards": len(cards_doc.get("cards", [])),
            "clipped": sum(1 for c in cards_doc.get("cards", []) if c.get("status") == "clipped"),
            "unknown_card": sum(1 for c in cards_doc.get("cards", []) if c.get("status") == "unknown_card"),
        },
    )

    # --- 3. 排列 ---------------------------------------------------------
    started = time.perf_counter()
    layout = layout_from_card_list(recognition.cards)
    layout_doc = layout.to_dict()
    stages["layout"] = _stage(
        "layout",
        [layout_doc],
        started,
        {
            "rows": len(layout_doc.get("rows", [])),
            "slots": sum(len(row.get("slots", [])) for row in layout_doc.get("rows", [])),
            "diagnostics": len(layout_doc.get("diagnostics", [])),
        },
    )

    # --- 4. 解析 ---------------------------------------------------------
    started = time.perf_counter()
    ast = parse_layout_document(layout_doc)
    ast_doc = ast.to_dict()
    stages["parser"] = _stage(
        "parser",
        [ast_doc],
        started,
        {
            "statements": len(ast_doc.get("root", {}).get("body", [])),
            "diagnostics": len(ast_doc.get("diagnostics", [])),
        },
    )

    # --- 5. IR -----------------------------------------------------------
    started = time.perf_counter()
    ir = normalize_document(ast_doc)
    ir_doc = ir.to_dict()
    stages["ir"] = _stage(
        "ir",
        [ir_doc],
        started,
        {
            "steps": len(ir_doc.get("program", {}).get("steps", [])),
            "diagnostics": len(ir_doc.get("diagnostics", [])),
        },
    )

    # --- 6. 模拟执行 -----------------------------------------------------
    started = time.perf_counter()
    effective_run_id = run_id or f"run-e2e-{scene}"
    run = SimExecutor(run_id=effective_run_id).run(ir_from_document(ir_doc))
    events = [_loads(line) for line in run.events_text().splitlines() if line.strip()]
    result_doc = _loads(run.result_text())
    stages["exec"] = _stage(
        "exec",
        [*events, result_doc],
        started,
        {
            "events": len(events),
            "steps_executed": result_doc.get("steps_executed", 0),
            "status": result_doc.get("status", ""),
            "virtual_ms": result_doc.get("duration_ms", 0.0),
        },
    )

    if corrupt in LAYER_CORRUPTIONS:
        stage = stages[corrupt]
        stages[corrupt] = StageOutput(
            stage=corrupt,
            documents=_corrupt_stage_documents(corrupt, stage.documents),
            timings_ms=stage.timings_ms,
            counts=stage.counts,
        )
        notes.append(f"破坏注入：阶段 {corrupt} 的记录文档缺字段（corrupt={corrupt}）")

    header = _build_header(
        run_id=effective_run_id,
        scene=scene,
        seed=seed,
        generated=generated,
        image_ref=f"{SCENE_DIR_NAME}/{image_path.name}",
        truth_ref=f"{SCENE_DIR_NAME}/{truth_path.name}",
        image_sha256=sha256_bytes(image_bytes),
        truth_sha256=sha256_text(truth_text),
        timings={stage: stages[stage].timings_ms for stage in STAGES},
        corrupt=corrupt,
    )
    chain = ChainRun(
        run_id=effective_run_id,
        scene=scene,
        seed=seed,
        corrupt=corrupt,
        image_path=image_path,
        ground_truth_path=truth_path,
        header=header,
        stages=stages,
        wall_ms=_ms(started_all),
        notes=tuple(notes),
    )
    if write:
        chain.write(target_dir / f"{scene}.jsonl")
    return chain


def _loads(text: str) -> dict[str, Any]:
    """把层自己的 JSON 文本读回字典（严格照抄，不做任何字段修补）。"""
    return json.loads(text)


def _stage(
    stage: str, documents: list[dict[str, Any]], started: float, counts: dict[str, Any]
) -> StageOutput:
    return StageOutput(
        stage=stage, documents=tuple(documents), timings_ms=_ms(started), counts=counts
    )


def _corrupt_scene_image(image_path: Path, generated: GeneratedScene) -> None:
    """把图像整幅抹成中灰（128）：确定性、可复现，且一定改变识别结果（0 个标记）。"""
    from ..synth.png import encode_gray

    flat = bytes([128]) * (generated.width * generated.height)
    image_path.write_bytes(encode_gray(generated.width, generated.height, flat))


def _corrupt_stage_documents(
    stage: str, documents: tuple[dict[str, Any], ...]
) -> tuple[dict[str, Any], ...]:
    """删掉该阶段第一个层对象的一个必填字段（JSON 仍合法，但记录形状不合法）。"""
    kind, field_name = LAYER_CORRUPTIONS[stage]
    corrupted: list[dict[str, Any]] = []
    done = False
    for document in documents:
        if not done and document.get("kind") == kind:
            corrupted.append({key: value for key, value in document.items() if key != field_name})
            done = True
            continue
        corrupted.append(document)
    if not done:
        raise ChainError(f"阶段 {stage} 的文档里没有 {kind}，破坏注入无处下手")
    return tuple(corrupted)


def _build_header(
    *,
    run_id: str,
    scene: str,
    seed: int,
    generated: GeneratedScene,
    image_ref: str,
    truth_ref: str,
    image_sha256: str,
    truth_sha256: str,
    timings: dict[str, float],
    corrupt: str,
) -> dict[str, Any]:
    generation: dict[str, Any] = {
        "stage": "synth",
        "seed": seed,
        "image_ref": image_ref,
        "image_sha256": image_sha256,
        "ground_truth_ref": truth_ref,
        "ground_truth_sha256": truth_sha256,
        "cards_expected": len(generated.ground_truth.get("cards", [])),
    }
    if corrupt != "none":
        generation["corrupt"] = corrupt
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": KIND_RUN_HEADER,
        "run_id": run_id,
        "backend": "sim",
        "scene": scene,
        "seed": seed,
        "frame": {
            "frame_id": f"f-e2e-{scene}",
            "image_ref": image_ref,
            "width": generated.width,
            "height": generated.height,
            "source": "synthetic",
        },
        "generation": generation,
        "created_with": {
            "name": "liveblocks",
            "package_version": __version__,
            "e2e_version": E2E_VERSION,
            "layer_sources": ["synth", "vision", "layout", "parser", "ir", "exec"],
        },
        "timings_ms": {stage: timings[stage] for stage in STAGES},
    }


__all__ = [
    "SCENE_DIR_NAME",
    "LAYER_CORRUPTIONS",
    "ChainError",
    "ChainRun",
    "StageOutput",
    "run_chain",
]
