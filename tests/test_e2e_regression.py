"""全场景回归（LB-009 的 ``build_report``）：验收对照、黄金基准、破坏注入、时间上界。

这里是 Issue #22 四条可观察验收的执行证据：

1. 全链路命令退出码 0（本文件用 ``report.passes()`` 对应退出码 0）；
2. 运行记录首行是 RunHeader 且包含全部层的 kind；
3. 人为破坏一个 fixture 时回归明确失败并指出层级（既验默认自检，也验手工 ``--corrupt``）；
4. 回归在固定时间内完成。

另外几条是「别假通过」的反向验证：基准被手改、基准缺场景、自检失效、时间上界被人为压低，
都必须让回归**失败**。
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from liveblocks.e2e.baseline import BASELINE_NAME, baseline_document, dump_baseline
from liveblocks.e2e.protocol import (
    CODE_BASELINE_MISMATCH,
    CODE_BASELINE_MISSING,
    CODE_CORRUPT_NOT_CAUGHT,
    CODE_TIME_BOUND,
    TIME_BOUND_MS,
)
from liveblocks.e2e.regression import (
    CorruptionCheck,
    build_report,
    failure_summary,
    format_report,
    root_layer,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
E2E_DIR = REPO_ROOT / "fixtures" / "e2e"


@pytest.fixture(scope="session")
def report(tmp_path_factory: pytest.TempPathFactory):
    """整份回归（6 个场景 + 破坏注入自检）只跑一遍，多个用例共享。"""
    out = tmp_path_factory.mktemp("e2e-regression")
    return build_report(E2E_DIR, out_dir=out)


# --- 健康路径 -----------------------------------------------------------------


def test_healthy_regression_passes(report) -> None:
    assert report.failures() == []
    assert report.ok
    assert report.passes()
    assert [result.scene for result in report.results] == [
        "clipped",
        "low-light",
        "multi-row",
        "normal",
        "overlap",
        "slight-rotation",
    ]
    assert all(result.ok for result in report.results)
    assert len(report.record_paths) == 6
    assert all(path.is_file() for path in report.record_paths)
    assert report.root_layer() == ""
    assert report.total_lines >= 6 * 8 - 2  # 每份至少 8 行（少的两份是多出事件流而非更少）


def test_every_record_on_disk_has_header_and_all_layers(report) -> None:
    from liveblocks.e2e.protocol import REQUIRED_KINDS, validate_record
    from liveblocks.e2e.record import read_documents

    for result in report.results:
        documents, issues = read_documents(result.record_path)
        assert issues == []
        assert documents[0]["kind"] == "RunHeader"
        kinds = {document["kind"] for document in documents[1:]}
        for kind in REQUIRED_KINDS:
            assert kind in kinds, f"{result.scene} 缺 {kind}"
        shape = validate_record(
            documents,
            scene=result.scene,
            image_exists=lambda ref, base=Path(result.record_path).parent: (base / ref).is_file(),
        )
        assert [item.describe() for item in shape] == []


def test_acceptance_covers_the_issue_criteria(report) -> None:
    checks = report.acceptance()
    assert len(checks) == 5
    assert all(ok for ok, _criterion, _evidence in checks)
    criteria = [criterion for _ok, criterion, _evidence in checks]
    assert "全链路命令退出码 0（六个阶段真跑过且记录落盘）" == criteria[0]
    assert "运行记录首行是 RunHeader 且包含全部层的 kind" == criteria[1]
    assert "与黄金基准一致（逐层指纹 + 计数 + 行数）" == criteria[2]
    assert "人为破坏一个夹具时回归明确失败并指出层级" == criteria[3]
    assert "回归在固定时间内完成" == criteria[4]
    evidence = {criterion: evidence for _ok, criterion, evidence in checks}
    assert "生成层复现 LB-002 入库 fixtures 6/6" in evidence[criteria[0]]
    assert "kind 覆盖 7 种" in evidence[criteria[1]]


def test_report_text_has_no_failure_summary_when_green(report) -> None:
    text = format_report(report)
    assert "端到端回归报告（LB-009" in text
    assert "逐场景：" in text
    assert "验收对照：" in text
    assert "破坏注入自检：" in text
    assert "失败摘要：无" in text
    assert text.count("[通过]") == 5
    assert failure_summary(report) == "失败摘要：无（全部场景与黄金基准一致）"


def test_default_self_check_proves_the_regression_can_fail(report) -> None:
    check = report.corruption_check
    assert isinstance(check, CorruptionCheck)
    assert check.detected is True
    assert check.detected_layer == "synth"
    assert check.target == "scene-image"
    assert check.record_path.is_file()
    assert any(item.code == CODE_BASELINE_MISMATCH for item in check.issues)
    assert "被抓到" in check.describe()


def test_regression_stays_within_the_fixed_time_bound(report) -> None:
    assert report.time_bound_ms == TIME_BOUND_MS
    assert report.wall_ms <= report.time_bound_ms
    assert all(result.wall_ms <= report.record_bound_ms for result in report.results)


# --- 黄金基准 -----------------------------------------------------------------


def test_baseline_matches_a_fresh_run(report) -> None:
    """基准文件是确定性的：用本轮真跑的结果重算，必须与入库文件逐字节相同。"""
    fresh = baseline_document(
        [result.run for result in report.results],
        seed=report.seed,
        time_bound_ms=report.time_bound_ms,
        record_bound_ms=report.record_bound_ms,
    )
    committed = (E2E_DIR / BASELINE_NAME).read_text(encoding="utf-8")
    assert dump_baseline(fresh) == committed


def test_baseline_records_no_wall_clock(report) -> None:
    document = json.loads((E2E_DIR / BASELINE_NAME).read_text(encoding="utf-8"))
    text = json.dumps(document, ensure_ascii=False)
    # 口径说明（note）里可以提到这些字段名，但**场景条目里不能有**它们。
    assert '"timings_ms":' not in text
    assert '"wall_ms":' not in text
    for scene in document["scenes"]:
        assert scene["layers"]["vision"]["sha256"]
        assert len(scene["layers"]["vision"]["sha256"]) == 64
        assert scene["image_sha256"]


def test_baseline_detects_a_hand_edited_layer_hash(tmp_path: Path) -> None:
    """手改基准里某一层的指纹 → 回归必须失败，并点名到那一层。"""
    shutil.copytree(E2E_DIR, tmp_path / "e2e")
    path = tmp_path / "e2e" / BASELINE_NAME
    document = json.loads(path.read_text(encoding="utf-8"))
    for scene in document["scenes"]:
        if scene["name"] == "normal":
            scene["layers"]["layout"]["sha256"] = "0" * 64
    path.write_text(dump_baseline(document), encoding="utf-8")

    broken = build_report(
        tmp_path / "e2e", out_dir=tmp_path / "out", scenes=["normal"], self_check=False
    )
    assert not broken.ok
    assert broken.root_layer() == "layout"
    assert any(item.code == CODE_BASELINE_MISMATCH for item in broken.failures())
    assert "最早失败层：layout" in failure_summary(broken)


def test_baseline_missing_scene_is_reported(tmp_path: Path) -> None:
    shutil.copytree(E2E_DIR, tmp_path / "e2e")
    path = tmp_path / "e2e" / BASELINE_NAME
    document = json.loads(path.read_text(encoding="utf-8"))
    document["scenes"] = [scene for scene in document["scenes"] if scene["name"] != "normal"]
    path.write_text(dump_baseline(document), encoding="utf-8")

    broken = build_report(
        tmp_path / "e2e", out_dir=tmp_path / "out", scenes=["normal"], self_check=False
    )
    assert not broken.ok
    assert any(item.code == CODE_BASELINE_MISSING for item in broken.failures())


# --- 破坏注入 -----------------------------------------------------------------


@pytest.mark.parametrize("stage", ["vision", "layout", "parser", "ir", "exec"])
def test_corrupting_a_layer_fails_and_names_it(stage: str, tmp_path: Path) -> None:
    broken = build_report(
        E2E_DIR,
        out_dir=tmp_path / "out",
        scenes=["normal"],
        corrupt=stage,
        self_check=False,
    )
    assert not broken.passes()
    assert broken.root_layer() == stage
    summary = failure_summary(broken)
    assert f"最早失败层：{stage}" in summary
    assert f"--corrupt {stage}" in summary
    assert broken.results[0].record_path.is_file()


def test_corrupting_the_generated_image_fails_at_the_generation_layer(tmp_path: Path) -> None:
    broken = build_report(
        E2E_DIR,
        out_dir=tmp_path / "out",
        scenes=["normal"],
        corrupt="scene-image",
        self_check=False,
    )
    assert not broken.passes()
    assert broken.root_layer() == "synth"
    assert "生成" in failure_summary(broken)


def test_corruption_probe_failure_is_reported(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """把比对函数改成永真式（模拟「回归是假通过」）→ 必须报 ``LB-E012``。"""
    from liveblocks.e2e import regression as module

    monkeypatch.setattr(module, "compare_run", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(module, "validate_record", lambda *_args, **_kwargs: [])
    report = build_report(E2E_DIR, out_dir=tmp_path / "out", scenes=["normal"], self_check=True)
    assert report.corruption_check is not None
    assert report.corruption_check.detected is False
    assert not report.passes()
    assert any(item.code == CODE_CORRUPT_NOT_CAUGHT for item in report.failures())
    assert "回归是假通过" in failure_summary(report)


def test_self_check_can_be_skipped_and_says_so(tmp_path: Path) -> None:
    report = build_report(
        E2E_DIR, out_dir=tmp_path / "out", scenes=["normal"], self_check=False
    )
    assert report.corruption_check is None
    assert report.ok  # 记录与基准都没问题
    checks = {criterion: (ok, evidence) for ok, criterion, evidence in report.acceptance()}
    ok, evidence = checks["人为破坏一个夹具时回归明确失败并指出层级"]
    assert ok is False
    assert "--no-self-check" in evidence
    # 少了一条证据就不算全过：CLI 会以非零码退出。
    assert not report.passes()


# --- 时间上界 -----------------------------------------------------------------


def test_time_bound_violation_is_reported(tmp_path: Path) -> None:
    report = build_report(
        E2E_DIR,
        out_dir=tmp_path / "out",
        scenes=["normal"],
        self_check=False,
        time_bound_ms=0.001,
    )
    assert not report.ok
    assert any(item.code == CODE_TIME_BOUND for item in report.failures())
    assert "超过固定上界" in failure_summary(report)


def test_record_bound_violation_is_reported(tmp_path: Path) -> None:
    report = build_report(
        E2E_DIR,
        out_dir=tmp_path / "out",
        scenes=["normal"],
        self_check=False,
        record_bound_ms=0.001,
    )
    assert any(item.code == CODE_TIME_BOUND for item in report.failures())
    assert "单条记录实测" in failure_summary(report)


# --- 小工具 -------------------------------------------------------------------


def test_root_layer_picks_the_earliest_stage() -> None:
    from liveblocks.e2e.protocol import issue

    early = issue(CODE_BASELINE_MISMATCH, "…", layer="parser")
    late = issue(CODE_BASELINE_MISMATCH, "…", layer="exec")
    assert root_layer([late, early]) == "parser"
    assert root_layer([late]) == "exec"
    assert root_layer([]) == ""


def test_unknown_scene_in_the_run_is_a_chain_error(tmp_path: Path) -> None:
    from liveblocks.e2e import ChainError

    with pytest.raises(ChainError):
        build_report(E2E_DIR, out_dir=tmp_path / "out", scenes=["nope"], self_check=False)


def test_update_baseline_and_corrupt_are_mutually_exclusive(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        build_report(
            tmp_path / "e2e",
            out_dir=tmp_path / "out",
            scenes=["normal"],
            corrupt="layout",
            update_baseline=True,
        )


def test_update_baseline_writes_a_loadable_file(tmp_path: Path) -> None:
    from liveblocks.e2e.baseline import load_baseline

    report = build_report(
        tmp_path / "e2e", out_dir=tmp_path / "out", scenes=["normal"], update_baseline=True
    )
    assert report.baseline_updated is True
    document = load_baseline(tmp_path / "e2e")
    assert [scene["name"] for scene in document["scenes"]] == ["normal"]
    assert report.passes()
