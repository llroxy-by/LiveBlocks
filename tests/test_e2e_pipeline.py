"""全链路的产物（LB-009 的 ``run_chain``）：六阶段、记录、确定性与破坏注入。

验收口径：

- 六个阶段都真跑过，且每阶段的 ``kind`` 与 :data:`liveblocks.e2e.protocol.STAGE_KINDS` 一致；
- 生成层**复现** LB-002 入库的 ``fixtures/scenes/*``（同种子 → 同字节），证明整条链路的起点是真的；
- 同一场景跑两遍，除 ``timings_ms`` 外逐字节一致（回归基准才有意义）；
- 每个 ``--corrupt`` 目标都真的让**对应层**的记录文档变得不合格；
- 端到端记录的生产不依赖 ``scripts/``（LB-008 的拼记录脚本只为夹具服务）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from liveblocks.e2e.pipeline import LAYER_CORRUPTIONS, ChainError, run_chain
from liveblocks.e2e.protocol import (
    CODE_BAD_EVENTS,
    CODE_BAD_LAYER_FIELDS,
    CORRUPT_TARGETS,
    KIND_RUN_HEADER,
    STAGE_KINDS,
    STAGES,
    validate_record,
)
from liveblocks.e2e.record import fingerprint, read_documents

REPO_ROOT = Path(__file__).resolve().parents[1]
SCENES_DIR = REPO_ROOT / "fixtures" / "scenes"
E2E_SRC = REPO_ROOT / "src" / "liveblocks" / "e2e"
#: 真正跑链路 / 写记录 / 比基准的模块（``docs.py`` 不在此列：它按设计要**提到**那个脚本名）。
PRODUCTION_MODULES = ("protocol.py", "record.py", "pipeline.py", "baseline.py", "regression.py")


def test_chain_covers_six_stages_in_order(e2e_normal_run) -> None:
    run = e2e_normal_run
    assert tuple(run.stages) == STAGES
    for stage in STAGES:
        assert run.stages[stage].kinds == STAGE_KINDS[stage]
    assert run.kinds[:2] == STAGE_KINDS["vision"]
    assert run.documents[0]["kind"] == KIND_RUN_HEADER


def test_header_records_scene_seed_backend_and_generation(e2e_normal_run) -> None:
    run = e2e_normal_run
    header = run.header
    assert header["kind"] == KIND_RUN_HEADER
    assert header["run_id"] == "run-e2e-normal"
    assert header["scene"] == "normal"
    assert header["backend"] == "sim"
    assert header["seed"] == run.seed
    assert header["frame"]["frame_id"] == "f-e2e-normal"
    assert header["frame"]["source"] == "synthetic"
    assert header["generation"]["stage"] == "synth"
    assert len(header["generation"]["image_sha256"]) == 64
    assert header["created_with"]["e2e_version"] == "0.0.1"
    assert header["created_with"]["layer_sources"] == list(STAGES)


def test_record_passes_shape_validation(e2e_normal_run) -> None:
    run = e2e_normal_run
    issues = validate_record(
        run.documents,
        scene=run.scene,
        image_exists=lambda ref: (Path(run.record_path).parent / ref).is_file(),
    )
    assert [item.describe() for item in issues] == []


def test_counts_match_the_documents(e2e_normal_run) -> None:
    run = e2e_normal_run
    counts = run.counts()
    cards = run.stages["vision"].documents[1]["cards"]
    layout = run.stages["layout"].documents[0]
    ir = run.stages["ir"].documents[0]
    events = [doc for doc in run.stages["exec"].documents if doc.get("kind") == "ExecEvent"]
    assert counts["vision"]["cards"] == len(cards)
    assert counts["vision"]["markers"] == len(run.stages["vision"].documents[0]["markers"])
    assert counts["layout"]["rows"] == len(layout["rows"])
    assert counts["layout"]["slots"] == sum(len(row["slots"]) for row in layout["rows"])
    assert counts["ir"]["steps"] == len(ir["program"]["steps"])
    assert counts["exec"]["events"] == len(events)
    assert counts["exec"]["status"] == run.stages["exec"].documents[-1]["status"]


def test_generation_reproduces_the_lb002_fixture_byte_for_byte(e2e_normal_run) -> None:
    """生成层不是「造个类似的东西」：它与入库的 LB-002 场景图逐字节相同。"""
    run = e2e_normal_run
    committed = (SCENES_DIR / f"{run.scene}.png").read_bytes()
    from liveblocks.e2e.record import sha256_bytes

    assert sha256_bytes(committed) == run.header["generation"]["image_sha256"]
    assert (SCENES_DIR / f"{run.scene}.json").read_text(encoding="utf-8") == run.ground_truth_path.read_text(
        encoding="utf-8"
    )


def test_two_runs_are_identical_apart_from_timings(tmp_path: Path, context) -> None:
    first = run_chain("normal", out_dir=tmp_path / "a", context=context)
    second = run_chain("normal", out_dir=tmp_path / "b", context=context)
    assert first.fingerprints() == second.fingerprints()
    assert first.counts() == second.counts()
    assert first.header["generation"] == second.header["generation"]
    left = dict(first.header)
    right = dict(second.header)
    left.pop("timings_ms")
    right.pop("timings_ms")
    assert left == right
    # 记录除首行外逐字节一致（首行只有 timings_ms 会变）。
    assert "\n".join(json.dumps(doc, ensure_ascii=False) for doc in first.documents[1:]) == (
        "\n".join(json.dumps(doc, ensure_ascii=False) for doc in second.documents[1:])
    )


def test_run_without_write_leaves_no_file(tmp_path: Path, context) -> None:
    run = run_chain("normal", out_dir=tmp_path / "nowrite", context=context, write=False)
    assert run.record_path is None
    assert not run.image_path.parent.joinpath("normal.jsonl").exists()
    assert run.documents  # 产物仍在内存里


@pytest.mark.parametrize("stage", sorted(LAYER_CORRUPTIONS))
def test_corrupting_a_layer_breaks_that_layer_and_names_it(stage: str, tmp_path: Path, context) -> None:
    run = run_chain("normal", out_dir=tmp_path / stage, corrupt=stage, context=context)
    issues = validate_record(run.documents, scene=run.scene)
    assert issues, f"破坏 {stage} 后记录形状必须不合格"
    assert {item.layer for item in issues} == {stage}
    expected = CODE_BAD_EVENTS if stage == "exec" else CODE_BAD_LAYER_FIELDS
    assert expected in {item.code for item in issues}
    assert any("破坏注入" in note for note in run.notes)


def test_corrupt_scene_image_keeps_the_record_valid_but_changes_recognition(tmp_path: Path, context) -> None:
    healthy = run_chain("normal", out_dir=tmp_path / "h", context=context)
    broken = run_chain("normal", out_dir=tmp_path / "c", corrupt="scene-image", context=context)
    assert validate_record(broken.documents, scene=broken.scene) == []
    assert healthy.counts()["vision"]["markers"] == 1
    assert broken.counts()["vision"]["markers"] == 0
    assert broken.header["generation"]["image_sha256"] != healthy.header["generation"]["image_sha256"]
    assert broken.header["generation"]["corrupt"] == "scene-image"
    assert broken.fingerprints()["synth"] if "synth" in broken.fingerprints() else True


def test_layer_fingerprint_changes_when_the_layer_is_corrupted(tmp_path: Path, context) -> None:
    healthy = run_chain("normal", out_dir=tmp_path / "h2", context=context)
    broken = run_chain("normal", out_dir=tmp_path / "c2", corrupt="layout", context=context)
    assert healthy.fingerprints()["layout"] != broken.fingerprints()["layout"]
    assert healthy.fingerprints()["parser"] == broken.fingerprints()["parser"]
    # 破坏只改「记录里的文档」，下游阶段消费的是对象，所以 IR / 执行结果不变。
    assert healthy.fingerprints()["ir"] == broken.fingerprints()["ir"]


def test_unknown_scene_and_unknown_corrupt_target_raise(tmp_path: Path, context) -> None:
    with pytest.raises(ChainError):
        run_chain("no-such-scene", out_dir=tmp_path, context=context)
    with pytest.raises(ChainError):
        run_chain("normal", out_dir=tmp_path, corrupt="nowhere", context=context)


def test_corrupt_targets_cover_the_documented_set() -> None:
    assert CORRUPT_TARGETS == ("none", "scene-image", *STAGES[1:])


def _code_mentions(path: Path, needles: tuple[str, ...]) -> list[str]:
    """``path`` 里**除了文档字符串之外**的字符串常量与 import 提到 ``needles`` 的地方。

    只看 AST：注释与 docstring 里写「不依赖 scripts/…」是正常的说明文字，不算依赖。
    """
    import ast

    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                docstrings.add(id(body[0].value))
    hits: list[str] = []

    def matched(text: str) -> bool:
        return any(needle in text for needle in needles)

    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
            if matched(node.value):
                hits.append(f"字符串常量 {node.value[:40]!r}")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if matched(alias.name):
                    hits.append(f"import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if matched(module):
                hits.append(f"from {module} import …")
    return hits


def test_production_does_not_depend_on_the_lb008_scripts() -> None:
    """运行记录的生产必须在包内完成：不能引用 ``scripts/`` 或 ``build_run_records``。"""
    offenders = []
    for name in PRODUCTION_MODULES:
        path = E2E_SRC / name
        assert path.is_file(), f"缺少生产模块 {name}"
        for hit in _code_mentions(path, ("build_run_records", "scripts")):
            offenders.append(f"{name}: {hit}")
    assert offenders == []
    import liveblocks.e2e as package

    assert not hasattr(package, "build_run_records")


def test_the_dependency_guard_itself_can_fail(tmp_path: Path) -> None:
    """反向验证：真的写了 ``import scripts.build_run_records`` 时必须被抓到。"""
    sample = tmp_path / "fake.py"
    sample.write_text(
        '"""文档字符串里写 scripts/ 不算依赖。"""\nimport scripts.build_run_records\n',
        encoding="utf-8",
    )
    assert _code_mentions(sample, ("build_run_records", "scripts")) == ["import scripts.build_run_records"]


def test_record_round_trips_through_the_reader(e2e_normal_run) -> None:
    documents, issues = read_documents(e2e_normal_run.record_path)
    assert issues == []
    assert documents == e2e_normal_run.documents
    assert fingerprint(documents[1:]) == fingerprint(e2e_normal_run.body)
