"""LB-032 验收：把标记页重新识别一遍（含旋转与确定性核对），并用故障注入验证诊断码。

真跑一次约 7 秒（一次整页识别 ≈ 1.2 秒，另有 3 次旋转核对与 2 次渲染），所以按 module 缓存；
故障注入用假识别器（`recognize_fn`）与假渲染器（`render_fn`），不 monkeypatch 内部函数。
"""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from liveblocks.sheet.render import render_sheet
from liveblocks.sheet.spec import HUMAN_VERIFICATION, CardSheetSpec
from liveblocks.sheet.verify import (
    CENTER_TOLERANCE_PX,
    ORIENTATION_TOLERANCE_DEG,
    format_report,
    verify_sheet,
)


@pytest.fixture(scope="module")
def spec() -> CardSheetSpec:
    return CardSheetSpec.build()


@pytest.fixture(scope="module")
def verification(spec: CardSheetSpec):
    return verify_sheet(spec)


def test_every_card_is_read_back_with_its_identity(verification, spec: CardSheetSpec) -> None:
    assert len(verification.reads) == len(spec.cards)
    assert all(read.found for read in verification.reads)
    assert all(read.status == "ok" for read in verification.reads)
    assert {read.card_id for read in verification.reads} == {card.card_id for card in spec.cards}
    assert {read.marker_id for read in verification.reads} == {card.marker_id for card in spec.cards}
    assert verification.unidentified_candidates == 0


def test_orientation_is_read_from_the_image(verification) -> None:
    for read in verification.reads:
        assert read.orientation_ok
        assert abs(read.angle_deg) <= ORIENTATION_TOLERANCE_DEG


def test_quarter_turn_rotations_shift_every_angle(verification, spec: CardSheetSpec) -> None:
    assert [rotation.turns for rotation in verification.rotations] == [1, 2, 3]
    for rotation in verification.rotations:
        assert rotation.cards == len(spec.cards)
        assert rotation.ids_match
        assert rotation.ok
        # 顺时针转 90° → 面内角（逆时针为正）减少 90°
        assert rotation.expected_angle_deg == pytest.approx(-90.0 * rotation.turns)
        assert rotation.max_angle_error_deg <= ORIENTATION_TOLERANCE_DEG


def test_card_centres_match_the_manifest(verification) -> None:
    for read in verification.reads:
        assert read.center_error_px <= CENTER_TOLERANCE_PX
        assert read.center_px is not None


def test_render_is_deterministic(verification) -> None:
    assert verification.repeated_png_identical
    assert verification.repeated_pdf_identical
    assert verification.repeated_manifest_identical


def test_every_acceptance_line_passes(verification) -> None:
    acceptance = verification.acceptance()
    assert len(acceptance) == 5
    for ok, criterion, evidence in acceptance:
        assert ok, f"{criterion} 未通过：{evidence}"
        assert evidence.strip()
    assert verification.passes()
    assert not verification.diagnostics


def test_report_is_honest_about_what_it_does_not_prove(verification) -> None:
    report = format_report(verification)
    assert "仅限合成管线" in report
    assert "待人工验证" in report
    for item, _steps in HUMAN_VERIFICATION:
        assert item in report
    assert "逐卡：" in report and "旋转核对：" in report


def test_result_document_is_machine_readable(verification, spec: CardSheetSpec) -> None:
    document = verification.to_dict()
    assert document["kind"] == "CardSheetVerification"
    assert document["layout"] == spec.layout_name
    assert len(document["cards"]) == len(spec.cards)
    assert len(document["rotations"]) == 3
    assert document["deterministic"] == {"png": True, "pdf": True, "manifest": True}
    assert all(entry["ok"] for entry in document["acceptance"])
    assert document["human_verification"]


# --- 故障注入：每个 LB-S0xx 都要有真实触发路径 ---------------------------------------


def _real_result(spec: CardSheetSpec):
    from liveblocks.sheet.verify import _recognize

    image = render_sheet(spec)
    return image, _recognize(image.pixels, image.width, image.height, spec, block=21)


def test_missing_card_reports_lb_s004(spec: CardSheetSpec) -> None:
    image, result = _real_result(spec)
    dropped = result.cards.cards[0].card_id
    fake = SimpleNamespace(
        markers=result.markers,
        cards=SimpleNamespace(
            cards=tuple(card for card in result.cards.cards if card.card_id != dropped),
            diagnostics=(),
        ),
    )
    verification = verify_sheet(
        spec, image=image, rotations=False, recognize_fn=lambda *a, **k: fake
    )
    assert any(entry["code"] == "LB-S004" for entry in verification.diagnostics)
    assert not verification.passes()
    assert verification.read_cards == len(spec.cards) - 1


def test_wrong_orientation_reports_lb_s005(spec: CardSheetSpec) -> None:
    image, result = _real_result(spec)
    rotated_markers = tuple(
        SimpleNamespace(
            marker_id=marker.marker_id,
            angle_deg=90.0,
            decode=marker.decode,
            center_px=marker.center_px,
            index=marker.index,
        )
        for marker in result.markers.markers
    )
    fake = SimpleNamespace(
        markers=SimpleNamespace(markers=rotated_markers, diagnostics=(result.markers.diagnostics)),
        cards=result.cards,
    )
    verification = verify_sheet(
        spec, image=image, rotations=False, recognize_fn=lambda *a, **k: fake
    )
    assert any(entry["code"] == "LB-S005" for entry in verification.diagnostics)
    assert not any(read.orientation_ok for read in verification.reads)
    assert not verification.passes()


def test_unidentified_candidates_are_reported_as_noise(spec: CardSheetSpec) -> None:
    image, result = _real_result(spec)
    noise = SimpleNamespace(
        card_id="unknown",
        status="occluded",
        center_px=(0.0, 0.0),
        marker_ids=(),
        marker_index=99,
    )
    fake = SimpleNamespace(
        markers=result.markers,
        cards=SimpleNamespace(
            cards=tuple(result.cards.cards) + (noise,), diagnostics=()
        ),
    )
    verification = verify_sheet(
        spec, image=image, rotations=False, recognize_fn=lambda *a, **k: fake
    )
    assert verification.unidentified_candidates == 1
    assert any(entry["code"] == "LB-S004" and entry["severity"] == "warning" for entry in verification.diagnostics)


def test_non_deterministic_render_reports_lb_s006(spec: CardSheetSpec, tmp_path) -> None:
    counter = {"n": 0}

    def flaky(spec_arg):
        counter["n"] += 1
        image = render_sheet(spec_arg)
        if counter["n"] == 1:
            return image
        return replace(image, png=image.png + b"\x00")

    verification = verify_sheet(
        spec, rotations=False, render_fn=flaky
    )
    assert counter["n"] == 2
    assert any(entry["code"] == "LB-S006" for entry in verification.diagnostics)
    assert not verification.repeated_png_identical
    assert not verification.passes()


def test_skipping_rotations_cannot_claim_a_pass(spec: CardSheetSpec, verification) -> None:
    """旋转核对是「朝向可读」的唯一证据；跳过它就不能声称验收通过（不给假通过留口子）。"""
    cheap = verify_sheet(spec, rotations=False)
    assert cheap.rotations == ()
    assert not cheap.passes()
    rotation_line = cheap.acceptance()[2]  # 第三条验收 = 旋转核对
    assert rotation_line[0] is False
    assert "未跑旋转核对" in rotation_line[2]
    # 少了 3 次整页识别：耗时应当明显低于带旋转的那一次（真实测量，不是常量）
    assert cheap.elapsed_ms < verification.elapsed_ms
