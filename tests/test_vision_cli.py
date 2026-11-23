"""``liveblocks recognize`` 子命令：detect 输出 §3 JSON，report 输出准确率/漏检统计。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from liveblocks import cli
from liveblocks.vision import validate_card_candidate_list, validate_marker_candidate_list

SCENES_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "scenes"
DATA_DIR = Path(__file__).resolve().parents[1] / "src" / "liveblocks" / "data"


def test_version_and_synth_still_work(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["--version"]) == 0
    assert capsys.readouterr().out.strip().startswith("liveblocks ")


def test_recognize_detect_prints_card_candidate_list(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = cli.main(["recognize", "detect", "--image", str(SCENES_DIR / "normal.png")])
    assert exit_code == 0
    document = json.loads(capsys.readouterr().out)
    validate_card_candidate_list(document)
    assert document["kind"] == "CardCandidateList"
    assert document["frame_id"] == "f-normal"
    assert document["cards"][0]["card_face"] == "move-forward"
    assert document["cards"][0]["status"] == "ok"


def test_recognize_detect_emit_markers_with_pose(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = cli.main(
        [
            "recognize",
            "detect",
            "--image",
            str(SCENES_DIR / "normal.png"),
            "--emit",
            "markers",
            "--intrinsics",
            "700,700,320,240",
        ]
    )
    assert exit_code == 0
    document = json.loads(capsys.readouterr().out)
    validate_marker_candidate_list(document)
    marker = document["markers"][0]
    assert marker["marker_id"] == "0"
    assert marker["pose"]["z_mm"] == pytest.approx(300.0, rel=0.05)


def test_recognize_detect_writes_file(tmp_path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "out" / "normal.cards.json"
    exit_code = cli.main(
        [
            "recognize",
            "detect",
            "--image",
            str(SCENES_DIR / "clipped.png"),
            "--card-layout",
            "card-60x40",
            "--out",
            str(out),
        ]
    )
    assert exit_code == 0
    captured = capsys.readouterr().out
    assert "CardCandidateList" in captured
    document = json.loads(out.read_text(encoding="utf-8"))
    validate_card_candidate_list(document)
    assert [card["status"] for card in document["cards"]] == ["clipped", "clipped"]


def test_recognize_detect_unknown_card_via_alternative_dictionary(
    tmp_path, capsys: pytest.CaptureFixture[str]
) -> None:
    """摘掉标记 0 的卡片字典 → ``unknown_card``（Issue #9 验收第 3 条的命令行入口）。"""
    payload = json.loads((DATA_DIR / "card_dictionary.json").read_text(encoding="utf-8"))
    payload["cards"] = [card for card in payload["cards"] if card["marker_id"] != 0]
    dictionary_path = tmp_path / "card_dictionary_without_C01.json"
    dictionary_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    exit_code = cli.main(
        [
            "recognize",
            "detect",
            "--image",
            str(SCENES_DIR / "normal.png"),
            "--card-dictionary",
            str(dictionary_path),
        ]
    )
    assert exit_code == 0
    document = json.loads(capsys.readouterr().out)
    validate_card_candidate_list(document)
    card = document["cards"][0]
    assert card["status"] == "unknown_card"
    assert card["card_type"] == "unknown"
    assert card["card_face"] == "unknown"
    assert any(entry["code"] == "LB-V001" for entry in document["diagnostics"])


def test_recognize_detect_missing_image_exits(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        cli.main(["recognize", "detect", "--image", "no-such-file.png"])


def test_recognize_detect_bad_intrinsics_exits() -> None:
    with pytest.raises(SystemExit):
        cli.main(
            [
                "recognize",
                "detect",
                "--image",
                str(SCENES_DIR / "normal.png"),
                "--intrinsics",
                "700,700",
            ]
        )


def test_recognize_report_prints_accuracy_table(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = cli.main(["recognize", "report", "--dir", str(SCENES_DIR)])
    assert exit_code == 0
    text = capsys.readouterr().out
    assert "识别统计（LB-003）" in text
    assert "召回率" in text
    assert "「正常」样例无漏检：通过" in text
    assert "「裁切」样例标 clipped：通过" in text
    for scene in ("normal", "clipped", "low-light", "slight-rotation", "multi-row", "overlap"):
        assert scene in text
    # 统计里必须点名漏检的那张卡，不能只报一个总数
    assert "漏检 overlap: 标记 12" in text


def test_recognize_report_writes_json(tmp_path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "report.json"
    exit_code = cli.main(["recognize", "report", "--dir", str(SCENES_DIR), "--json", str(out)])
    assert exit_code == 0
    capsys.readouterr()
    document = json.loads(out.read_text(encoding="utf-8"))
    assert document["kind"] == "RecognitionAccuracyReport"
    assert document["totals"]["expected"] == 17
    assert document["totals"]["extra"] == 0
    assert len(document["scenes"]) == 6
    assert [scene["scene_id"] for scene in document["scenes"]] == sorted(
        scene["scene_id"] for scene in document["scenes"]
    )


def test_recognize_report_on_empty_directory_exits_nonzero(
    tmp_path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = cli.main(["recognize", "report", "--dir", str(tmp_path)])
    assert exit_code == 1
    assert "没有可评估" in capsys.readouterr().out


def test_recognize_report_without_intrinsics_omits_pose(recognized) -> None:
    """``--no-intrinsics`` 时输出里不该有 pose（另一种跑法，用 API 直接验证）。"""
    from liveblocks.vision import evaluate_directory

    report = evaluate_directory(SCENES_DIR, intrinsics_from_truth=False)
    assert report.expected == 17
    assert report.identified == sum(scene.identified for scene in report.scenes)
